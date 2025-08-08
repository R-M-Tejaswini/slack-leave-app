# leavebot/slackapp/views.py

import json
import logging
import os
import urllib.parse
from datetime import datetime, date, timedelta

from django.db import transaction
from django.db.models import Q 
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# --- Updated Imports ---
# Import all the new models you created
from .models import Employee, LeaveType, LeaveRequest, LeaveRequestAudit, Holiday
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
    --- CORRECTED VALIDATION ORDER ---
    Handles the submission of the leave request modal with specific error messages.
    """
    try:
        user_info = payload["user"]
        values = payload["view"]["state"]["values"]
        employee = Employee.objects.get(slack_user_id=user_info["id"])
        
        start_date_str = values["start_date_block"]["start_date_input"]["selected_date"]
        end_date_str = values["end_date_block"]["end_date_input"]["selected_date"]
        leave_type_str = values["leave_type_block"]["leave_type_select"]["selected_option"]["value"]
        reason = values["reason_block"]["reason_input"]["value"] or ""

        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

        # === RE-ORDERED VALIDATION LOGIC ===

        # 1. Basic Date Sanity Checks
        if start_date < date.today():
            return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "Leave requests cannot be for a date in the past."}})
        if end_date < start_date:
            return JsonResponse({"response_action": "errors", "errors": {"end_date_block": "End date cannot be before the start date."}})
        
        # 2. Check for Weekends and Holidays FIRST
        holidays = set(Holiday.objects.filter(date__range=[start_date, end_date]).values_list('date', flat=True))
        requested_days = 0
        is_range_a_holiday = False
        
        current_date_iterator = start_date
        while current_date_iterator <= end_date:
            if current_date_iterator.weekday() < 5:
                if current_date_iterator not in holidays:
                    requested_days += 1
                else:
                    is_range_a_holiday = True
            current_date_iterator += timedelta(days=1)
        
        if requested_days == 0:
            if is_range_a_holiday:
                return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "The selected date range falls on a company holiday."}})
            else:
                return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "The selected date range falls entirely on a weekend."}})

        # 3. THEN, Check for Overlapping Leave Requests
        overlapping_requests = LeaveRequest.objects.filter(
            employee=employee,
            status__in=['pending', 'approved'],
            start_date__lte=end_date,
            end_date__gte=start_date
        ).exists()

        if overlapping_requests:
            return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "You already have an approved or pending leave request that overlaps with these dates."}})
        
        # 4. --- NEW: Check Monthly Leave Allowance ---
        # Find all approved/pending leaves for the employee in the month of the start date
        month_start = start_date.replace(day=1)
        
        # Get all approved/pending requests that start in the same month and year
        requests_this_month = LeaveRequest.objects.filter(
            employee=employee,
            status__in=['pending', 'approved'],
            start_date__year=month_start.year,
            start_date__month=month_start.month
        )
        
        # Calculate days already taken/requested this month
        days_taken_this_month = 0
        for req in requests_this_month:
            days_taken_this_month += req.duration_days
        
        allowance = employee.monthly_leave_allowance
        remaining_allowance = allowance - days_taken_this_month

        if requested_days > remaining_allowance:
            return JsonResponse({
                "response_action": "errors",
                "errors": {
                    "start_date_block": (
                        f"You have exceeded your monthly allowance. "
                        f"You have {remaining_allowance} days left this month but are requesting {requested_days}."
                    )
                }
            })

        
        # If all checks pass, proceed...
        leave_type = LeaveType.objects.get(name=leave_type_str)
        
        leave_request = LeaveRequest.objects.create(
            employee=employee,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            reason=reason,
        )
        LeaveRequestAudit.objects.create(leave_request=leave_request, action="created", performed_by=employee)
        logger.info(f"Leave Request #{leave_request.id} created for {employee.name}")

        send_approval_request(leave_request)
        slack_client.chat_postMessage(channel=employee.slack_user_id, text=f"Your leave request for *{leave_type.name}* from {start_date} to {end_date} has been submitted successfully.")
        return HttpResponse(status=200)

    except Employee.DoesNotExist:
        return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "Your user profile was not found in the system. Please contact admin."}})
    except LeaveType.DoesNotExist:
        return JsonResponse({"response_action": "errors", "errors": {"leave_type_block": f"The selected leave type '{leave_type_str}' is not valid."}})
    except Exception as e:
        logger.error(f"Error handling modal submission: {e}")
        return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "A server error occurred. Please try again."}})


@transaction.atomic
def handle_button_actions(payload):
    """
    --- UPDATED LOGIC ---
    Handles manager's approval/rejection and posts public announcement on approval.
    """
    try:
        user_info = payload["user"]
        action = payload["actions"][0]
        request_id = int(action["value"])
        action_id = action["action_id"]

        manager, _ = Employee.objects.get_or_create(slack_user_id=user_info["id"], defaults={"name": user_info["username"], "email": f"{user_info['username']}@example.com"})
        leave_request = LeaveRequest.objects.get(id=request_id)
        
        if leave_request.status != 'pending':
            slack_client.chat_postMessage(channel=manager.slack_user_id, text=f"This leave request for {leave_request.employee.name} has already been actioned.")
            return HttpResponse(status=200)

        status_text = ""
        if action_id == "approve_leave":
            leave_request.status = "approved"
            status_text = "approved"
            
            # --- NEW: POST PUBLIC ANNOUNCEMENT ON APPROVAL ---
            post_public_announcement(leave_request)

        elif action_id == "reject_leave":
            leave_request.status = "rejected"
            status_text = "rejected"
        else:
            return HttpResponse(status=400)
            
        leave_request.approver = manager
        leave_request.save()

        LeaveRequestAudit.objects.create(leave_request=leave_request, action=status_text, performed_by=manager)
        logger.info(f"Leave Request #{leave_request.id} {status_text} by {manager.name}")
        
        update_approval_message(leave_request)
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
        destination_channel = os.getenv("SLACK_REQUEST_CHANNEL")
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
        leave_request.save(update_fields=["slack_message_ts", "slack_channel_id"])
        
    except SlackApiError as e:
        print("\n" + "="*60)
        print("!!! SLACK API ERROR WHILE SENDING APPROVAL REQUEST !!!")
        print(f"SLACK'S ERROR MESSAGE: '{e.response['error']}'")
        print(f"FULL SLACK RESPONSE: {e.response}")
        print("="*60 + "\n")
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

def post_public_announcement(leave_request: LeaveRequest):
    """
    Posts a public message about an approved leave to a general channel.
    """
    channel_id = os.getenv("SLACK_REQUEST_CHANNEL") # Using the variable name you specified
    if not channel_id:
        logger.warning("SLACK_REQUEST_CHANNEL not set. Skipping public announcement.")
        return

    employee_name = leave_request.employee.name
    start_date = leave_request.start_date.strftime('%B %d')
    end_date = leave_request.end_date.strftime('%B %d')
    
    message = f"FYI: {employee_name} will be on leave from {start_date} to {end_date}."
    if leave_request.start_date == leave_request.end_date:
        message = f"FYI: {employee_name} will be on leave on {start_date}."

    try:
        slack_client.chat_postMessage(channel=channel_id, text=message)
        logger.info(f"Posted public announcement for leave request #{leave_request.id}")
    except SlackApiError as e:
        logger.error(f"Error posting public announcement: {e.response['error']}")