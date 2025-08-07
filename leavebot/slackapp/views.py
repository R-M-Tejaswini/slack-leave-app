# leavebot/slackapp/views.py

import json
import logging
import os
import urllib.parse
from datetime import datetime

from django.db import transaction
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# --- Updated Imports ---
# Import all the new models you created
from .models import Employee, LeaveType, LeaveRequest, LeaveRequestAudit
from .utils import verify_slack_request
from .slack_blocks import get_leave_form_modal, get_approval_message_blocks

# --- Initialization ---
slack_client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
logger = logging.getLogger("slackapp")


# ==============================================================================
# 1. Main Slack Entry Points (These handle the incoming webhooks)
# ==============================================================================

@csrf_exempt
@require_POST
def slash_command(request):
    """
    Handle the /applyleave slash command.
    Verifies the user and opens the leave request modal.
    """
    try:
        if not verify_slack_request(request):
            return JsonResponse({"error": "Invalid request"}, status=403)

        command_data = urllib.parse.parse_qs(request.body.decode())
        trigger_id = command_data.get("trigger_id", [None])[0]
        user_id = command_data.get("user_id", [None])[0]
        user_name = command_data.get("user_name", [None])[0]

        if not trigger_id or not user_id:
            return JsonResponse({"text": "Error: Missing user or trigger ID."})

        # Proactively create the Employee record if it doesn't exist
        # This ensures any user who uses the command is in our system.
        Employee.objects.get_or_create(
            slack_user_id=user_id, defaults={"name": user_name, "email": f"{user_name}@example.com"}
        )

        logger.info(f"Slash command received from user {user_name} ({user_id})")
        
        # NOTE: You may need to update get_leave_form_modal if it needs dynamic data
        modal_view = get_leave_form_modal()
        slack_client.views_open(trigger_id=trigger_id, view=modal_view)

        return HttpResponse(status=200)

    except SlackApiError as e:
        logger.error(f"Error opening modal: {e.response['error']}")
        return HttpResponse(f"Error: {e.response['error']}")
    except Exception as e:
        logger.error(f"Error in slash_command: {e}")
        return HttpResponse("An unexpected error occurred. Please try again.")

@csrf_exempt
@require_POST
def interactions(request):
    """
    Handles all interactions from Slack (e.g., modal submissions, button clicks).
    Acts as a router to the appropriate handler function.
    """
    try:
        if not verify_slack_request(request):
            return JsonResponse({"error": "Invalid request"}, status=403)

        payload = json.loads(request.POST.get("payload"))
        interaction_type = payload.get("type")

        if interaction_type == "view_submission":
            return handle_modal_submission(payload)
        elif interaction_type == "block_actions":
            return handle_button_actions(payload)
        else:
            logger.warning(f"Unhandled interaction type: {interaction_type}")
            return HttpResponse(status=200)

    except Exception as e:
        logger.error(f"Error in interactions view: {e}")
        return HttpResponse("An error occurred while processing your request.")

# ==============================================================================
# 2. Interaction Handlers (The core logic of your app)
# ==============================================================================

@transaction.atomic
def handle_modal_submission(payload):
    """
    Handles the submission of the leave request modal.
    Creates the LeaveRequest and notifies the manager.
    """
    try:
        user_info = payload["user"]
        values = payload["view"]["state"]["values"]

        # --- Use the new Employee model ---
        # Find the employee in our database who submitted the form.
        try:
            employee = Employee.objects.get(slack_user_id=user_info["id"])
        except Employee.DoesNotExist:
            logger.error(f"Employee with slack_user_id {user_info['id']} not found in database.")
            # You can send an error message back to the user in the modal here
            return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "Your user profile was not found in the system. Please contact admin."}})
        
        # --- Extract and validate form data ---
        start_date_str = values["start_date_block"]["start_date_input"]["selected_date"]
        end_date_str = values["end_date_block"]["end_date_input"]["selected_date"]
        leave_type_str = values["leave_type_block"]["leave_type_select"]["selected_option"]["value"]
        reason = values["reason_block"]["reason_input"]["value"] or ""

        # --- Use the new LeaveType model ---
        try:
            leave_type = LeaveType.objects.get(name=leave_type_str)
        except LeaveType.DoesNotExist:
             return JsonResponse({"response_action": "errors", "errors": {"leave_type_block": f"The selected leave type '{leave_type_str}' is not valid."}})

        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

        if end_date < start_date:
            return JsonResponse({"response_action": "errors", "errors": {"end_date_block": "End date cannot be before the start date."}})
        
        # --- Create the LeaveRequest with foreign keys ---
        leave_request = LeaveRequest.objects.create(
            employee=employee,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            reason=reason,
        )

        # --- Create an audit trail record ---
        LeaveRequestAudit.objects.create(
            leave_request=leave_request,
            action="created",
            performed_by=employee
        )
        logger.info(f"Leave Request #{leave_request.id} created for {employee.name}")

        # --- Notify Manager ---
        send_approval_request(leave_request)
        
        # --- Confirm to User ---
        slack_client.chat_postMessage(
            channel=employee.slack_user_id,
            text=f"Your leave request for *{leave_type.name}* from {start_date} to {end_date} has been submitted successfully."
        )

        return HttpResponse(status=200)

    except Exception as e:
        logger.error(f"Error handling modal submission: {e}")
        # Return a generic error in the modal
        return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "A server error occurred. Please try again."}})

@transaction.atomic
def handle_button_actions(payload):
    """
    Handles manager's approval or rejection button clicks.
    """
    try:
        user_info = payload["user"]
        action = payload["actions"][0]
        request_id = int(action["value"])
        action_id = action["action_id"]

        # Get the manager who clicked the button from our Employee database
        manager, _ = Employee.objects.get_or_create(
            slack_user_id=user_info["id"],
            defaults={"name": user_info["username"], "email": f"{user_info['username']}@example.com"}
        )

        leave_request = LeaveRequest.objects.get(id=request_id)
        
        # Check if the request is still pending
        if leave_request.status != 'pending':
            slack_client.chat_postMessage(
                channel=manager.slack_user_id,
                text=f"This leave request for {leave_request.employee.name} has already been actioned."
            )
            return HttpResponse(status=200)

        # Update status and create audit log
        if action_id == "approve_leave":
            leave_request.status = "approved"
            status_text = "approved"
        elif action_id == "reject_leave":
            leave_request.status = "rejected"
            status_text = "rejected"
        else:
            return HttpResponse(status=400)
            
        leave_request.approver = manager
        leave_request.save()

        LeaveRequestAudit.objects.create(
            leave_request=leave_request,
            action=status_text,
            performed_by=manager
        )
        logger.info(f"Leave Request #{leave_request.id} {status_text} by {manager.name}")
        
        # Update the original message to show the final status
        update_approval_message(leave_request)
        
        # Notify the employee who originally made the request
        notify_employee(leave_request)

        return HttpResponse(status=200)

    except LeaveRequest.DoesNotExist:
        logger.error("Leave request not found during button action.")
        return HttpResponse("This leave request could not be found.", status=404)
    except Exception as e:
        logger.error(f"Error in handle_button_actions: {e}")
        return HttpResponse("An error occurred.", status=500)

# ==============================================================================
# 3. Helper Functions (For sending and updating Slack messages)
# ==============================================================================

def send_approval_request(leave_request):
    """
    Finds the employee's manager and sends them a DM with the request.
    Falls back to a general channel if no manager is assigned.
    """
    employee = leave_request.employee
    
    # Determine the destination: manager's DM or a fallback channel
    if employee.manager:
        destination_channel = employee.manager.slack_user_id
    else:
        destination_channel = os.getenv("SLACK_MANAGEMENT_CHANNEL")
        logger.warning(f"No manager for {employee.name}, sending to fallback channel {destination_channel}")

    if not destination_channel:
        logger.error(f"Cannot send approval request for {leave_request.id}: No manager or fallback channel is set.")
        return

    try:
        # NOTE: get_approval_message_blocks must be updated to work with the new model
        blocks = get_approval_message_blocks(leave_request)
        
        response = slack_client.chat_postMessage(
            channel=destination_channel,
            text=f"New leave request from {employee.name}",
            blocks=blocks
        )
        
        # Save the message timestamp so we can update it later
        leave_request.slack_message_ts = response["ts"]
        leave_request.slack_channel_id = response["channel"]
        leave_request.save(update_fields=["slack_message_ts"])
        
    except SlackApiError as e:
        logger.error(f"Error sending approval request DM: {e.response['error']}")

def update_approval_message(leave_request: LeaveRequest):
    """
    Updates the original manager's message to show the final status
    and remove the action buttons. This version reads the exact location
    from the database.
    """
    # If we don't have a channel and message ID, we can't update anything.
    if not (leave_request.slack_channel_id and leave_request.slack_message_ts):
        logger.error(f"Missing channel or timestamp for LeaveRequest #{leave_request.id}, cannot update approval message.")
        return

    try:
        # Get the blocks for the completed state
        blocks = get_approval_message_blocks(leave_request, is_completed=True)
        print(f"--- DEBUG: Attempting to update message in channel '{leave_request.slack_channel_id}' at timestamp '{leave_request.slack_message_ts}' ---")

        
        # Use the saved channel_id and message_ts for the update
        slack_client.chat_update(
            channel=leave_request.slack_channel_id,
            ts=leave_request.slack_message_ts,
            text=f"Leave request for {leave_request.employee.name} has been {leave_request.status}.",
            blocks=blocks
        )
    except SlackApiError as e:
        # This is where the 'message_not_found' error was happening.
        # It should now be resolved.
        logger.error(f"Error updating approval message: {e.response['error']}")


def notify_employee(leave_request):
    """
    Sends a DM to the employee with the final status of their request.
    """
    employee = leave_request.employee
    approver = leave_request.approver
    status_emoji = "✅" if leave_request.status == "approved" else "❌"

    try:
        slack_client.chat_postMessage(
            channel=employee.slack_user_id,
            text=f"Your leave request has been *{leave_request.status}* {status_emoji}\n\n"
                 f"Your request for *{leave_request.leave_type.name}* from "
                 f"*{leave_request.start_date}* to *{leave_request.end_date}* "
                 f"was {leave_request.status} by {approver.name}."
        )
    except SlackApiError as e:
        logger.error(f"Error sending employee notification DM: {e.response['error']}")