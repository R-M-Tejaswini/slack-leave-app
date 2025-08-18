# leavebot/slackapp/views.py

import json
import logging
import os
import urllib.parse
from datetime import datetime, date, timedelta
import requests

from django.db import transaction
from django.db.models import Q 
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from django.http import JsonResponse 

from .models import Employee, LeaveType, LeaveRequest, LeaveRequestAudit, Holiday
from .utils import verify_slack_request
from .slack_blocks import get_leave_form_modal, get_approval_message_blocks, get_selection_modal,get_update_form_modal, get_calendar_view_modal
from .tasks import send_manager_reminder

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
    Handles incoming slash commands from Slack (e.g., /applyleave).
    It verifies the request and triggers the appropriate initial action,
    such as opening a modal.
    """
    try:
        if not verify_slack_request(request):
            return JsonResponse({"error": "Invalid request"}, status=403)

        command_data = urllib.parse.parse_qs(request.body.decode())
        command = command_data.get("command", [None])[0]
        trigger_id = command_data.get("trigger_id", [None])[0]
        user_id = command_data.get("user_id", [None])[0]
        user_name = command_data.get("user_name", [None])[0]
        response_url = command_data.get("response_url", [None])[0]


        if not trigger_id or not user_id:
            return JsonResponse({"text": "Error: Missing user or trigger ID."})

        # Proactively create the Employee record if it doesn't exist
        # This ensures any user who uses the command is in our system.
        employee, _ = Employee.objects.get_or_create(
            slack_user_id=user_id, defaults={"name": user_name, "email": f"{user_name}@example.com"}
        )

        logger.info(f"Slash command received from user {user_name} ({user_id})")
        
# --- COMMAND ROUTING ---
        if command == "/applyleave":
            modal_view = get_leave_form_modal()
            slack_client.views_open(trigger_id=trigger_id, view=modal_view)

        elif command == '/my_leaves':
            # --- EMPLOYEE CALENDAR VIEW ---
            today = date.today()
            
            # 1. Calculate remaining allowance for the summary
            requests_this_month = LeaveRequest.objects.filter(employee=employee, status__in=['pending', 'approved'], start_date__year=today.year, start_date__month=today.month)
            days_taken_this_month = sum(req.duration_days for req in requests_this_month)
            remaining_allowance = employee.monthly_leave_allowance - days_taken_this_month
            
            summary_info = {
                "allowance": employee.monthly_leave_allowance,
                "remaining": remaining_allowance
            }

            # 2. Get all of this employee's leaves for the current month to display
            employee_leaves = LeaveRequest.objects.filter(employee=employee, start_date__year=today.year, start_date__month=today.month)
            
            # 3. Generate and open the calendar modal with the summary
            calendar_modal = get_calendar_view_modal(employee_leaves, today, "My Leave Calendar", employee.id, summary_info)
            slack_client.views_open(trigger_id=trigger_id, view=calendar_modal)


        
        elif command in ["/update_leave", "/cancel_leave"]:
            # Find all pending requests for this user
            pending_requests = LeaveRequest.objects.filter(employee=employee, status='pending').order_by('start_date')
            
            if not pending_requests.exists():
                slack_client.chat_postEphemeral(
                    channel=user_id,
                    user=user_id,
                    text="You have no pending leave requests to modify."
                )
                return HttpResponse(status=200)

            # Determine which action is being taken
            action_type = "update" if command == "/update_leave" else "cancel"
            
            # Use a generic selection modal for both actions
            modal_view = get_selection_modal(pending_requests, action_type)
            slack_client.views_open(trigger_id=trigger_id, view=modal_view)

        return HttpResponse(status=200)

    except SlackApiError as e:
        logger.error(f"Error in slash_command: {e.response['error']}")
        return HttpResponse(f"Error: {e.response['error']}")
    except Exception as e:
        logger.error(f"Error in slash_command: {e}")
        return HttpResponse("An unexpected error occurred.")

@csrf_exempt
@require_POST
def interactions(request):
    """
    Acts as a router for all Slack interactive components, such as modal
    submissions and button clicks, directing the payload to the correct handler.
    """
    try:
        if not verify_slack_request(request):
            return JsonResponse({"error": "Invalid request"}, status=403)

        payload = json.loads(request.POST.get("payload"))
        interaction_type = payload.get("type")

        if interaction_type == "view_submission":
            callback_id = payload["view"]["callback_id"]
            print(f"--- DEBUG: Received callback_id: '{callback_id}' ---") 

            if callback_id == "leave_request_modal":
                return handle_modal_submission(payload)
            # --- NEW: Handle cancel submission ---
            elif callback_id == "cancel_leave_submission":
                return handle_cancel_submission(payload)
            # --- NEW: Handle update form submission ---
            elif callback_id == "leave_update_modal_submission":
                return handle_update_submission(payload)
            elif callback_id == "select_leave_to_update": 
                return handle_update_selection(payload) 
                
        elif interaction_type == "block_actions":
            action_id = payload["actions"][0]["action_id"]
            # --- NEW: Handle selecting a request to update ---
            if action_id == "request_select_action":
                return handle_update_selection(payload)
            else:
                 # This handles approve/reject/who-else-is-off
                return handle_button_actions(payload)
        
        logger.warning(f"Unhandled interaction type or callback_id: {interaction_type}")
        return HttpResponse(status=200)


    except Exception as e:
        logger.error(f"Error in interactions view: {e}")
        return HttpResponse("An error occurred while processing your request.")

# ==============================================================================
# 2. Interaction Handlers (The core logic of app)
# ==============================================================================

def validate_leave_request(employee, start_date, end_date, leave_type_str, leave_request_to_exclude=None):
    """
    A reusable function to validate leave request data.
    Returns a JsonResponse with an error if validation fails, otherwise returns None.
    
    Args:
        employee: The Employee object making the request.
        start_date: The proposed start date.
        end_date: The proposed end date.
        leave_type_str: The name of the leave type.
        leave_request_to_exclude: An optional LeaveRequest object to exclude from overlap/balance checks.
                                  This is used when updating an existing request.
    """
    # 1. Conditional Past Date Check
    today = date.today()
    is_unplanned = leave_type_str in ["Unplanned", "Emergency"]
    if is_unplanned:
        thirty_days_ago = today - timedelta(days=30)
        if start_date > today:
            return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "Unplanned leave must be for a date in the past."}})
        if start_date < thirty_days_ago:
            return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "Unplanned leave can only be submitted for up to 30 days in the past."}})
    else:
        if start_date < today:
            return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "Planned leave cannot be for a past date."}})

    if end_date < start_date:
        return JsonResponse({"response_action": "errors", "errors": {"end_date_block": "End date cannot be before the start date."}})

    # 2. Check for Weekends and Holidays
    holidays = set(Holiday.objects.filter(date__range=[start_date, end_date]).values_list('date', flat=True))
    requested_days = 0
    current_date_iterator = start_date
    while current_date_iterator <= end_date:
        if current_date_iterator.weekday() < 5 and current_date_iterator not in holidays:
            requested_days += 1
        current_date_iterator += timedelta(days=1)
    
    if requested_days == 0:
        return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "The selected date range falls entirely on a weekend or holiday."}})

    # 3. Check for Overlapping Leave Requests
    overlapping_query = LeaveRequest.objects.filter(
        employee=employee,
        status__in=['pending', 'approved'],
        start_date__lte=end_date,
        end_date__gte=start_date
    )
    if leave_request_to_exclude:
        overlapping_query = overlapping_query.exclude(id=leave_request_to_exclude.id)
    
    if overlapping_query.exists():
        return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "You have an overlapping leave request for these dates."}})

    # 4. Check Monthly Leave Allowance
    month_start = start_date.replace(day=1)
    requests_this_month_query = LeaveRequest.objects.filter(
        employee=employee,
        status__in=['pending', 'approved'],
        start_date__year=month_start.year,
        start_date__month=month_start.month
    )
    if leave_request_to_exclude:
        requests_this_month_query = requests_this_month_query.exclude(id=leave_request_to_exclude.id)
        
    days_taken_this_month = sum(req.duration_days for req in requests_this_month_query)
    remaining_allowance = employee.monthly_leave_allowance - days_taken_this_month

    if requested_days > remaining_allowance:
        return JsonResponse({
            "response_action": "errors",
            "errors": {
                "start_date_block": (
                    f"You are requesting {requested_days} days, but only have {remaining_allowance} days left this month."
                )
            }
        })
    
    # All checks passed
    return None

@transaction.atomic



def handle_update_selection(payload):
    """
    Handles the user selecting a leave request.
    It now updates the modal using a direct JSON response.
    """
    try:
        # Get the ID of the leave request the user selected from the dropdown.
        values = payload["view"]["state"]["values"]
        selected_request_id = values["request_selection_block"]["request_select_action"]["selected_option"]["value"]
        
        # Fetch the full LeaveRequest object from the database.
        leave_request = LeaveRequest.objects.get(id=int(selected_request_id))
        
        # Build the new modal view, pre-filled with the request's data.
        update_modal_view = get_update_form_modal(leave_request)
        
        # --- THE FIX IS HERE ---
        # Instead of calling slack_client, return a JSON response
        # that tells Slack to update the view directly.
        return JsonResponse({
            "response_action": "update",
            "view": update_modal_view
        })
        
    except Exception as e:
        logger.error(f"Error in handle_update_selection: {e}")
        # In case of an error, it's good practice to close the modal.
        return JsonResponse({"response_action": "clear"})


@transaction.atomic
def handle_cancel_submission(payload):
    """
    Handles the final confirmation for cancelling a leave request.
    """
    try:
        values = payload["view"]["state"]["values"]
        request_id = int(values["request_selection_block"]["request_select_action"]["selected_option"]["value"])
        
        leave_request = LeaveRequest.objects.get(id=request_id)
        
        # Only allow cancelling pending requests
        if leave_request.status != 'pending':
            slack_client.chat_postMessage(channel=leave_request.employee.slack_user_id, text="This request cannot be cancelled as it has already been actioned by your manager.")
            return HttpResponse(status=200)
            
        leave_request.status = 'cancelled'
        leave_request.save(update_fields=['status'])
        
        # Log the action
        LeaveRequestAudit.objects.create(leave_request=leave_request, action="cancelled", performed_by=leave_request.employee)
        logger.info(f"Leave Request #{leave_request.id} cancelled by employee.")

        if leave_request.slack_channel_id and leave_request.slack_message_ts:
            slack_client.chat_postMessage(
                channel=leave_request.slack_channel_id,
                thread_ts=leave_request.slack_message_ts,  # This makes it a threaded reply
                text=f"ℹ️ This leave request was cancelled by {leave_request.employee.name}."
            )

        # Update the manager's message and notify the employee
        update_approval_message(leave_request, is_cancelled=True)
        slack_client.chat_postMessage(channel=leave_request.employee.slack_user_id, text=f"Your leave request for {leave_request.start_date} to {leave_request.end_date} has been successfully cancelled.")
        
        return HttpResponse(status=200)

    except LeaveRequest.DoesNotExist:
        # This shouldn't happen in normal flow, but good to have
        logger.error(f"Attempt to cancel non-existent leave request.")
        return HttpResponse("Error: The request you tried to cancel was not found.", status=404)
    except Exception as e:
        logger.error(f"Error in handle_cancel_submission: {e}")
        return HttpResponse("An error occurred while cancelling the request.", status=500)

@transaction.atomic
def handle_update_submission(payload):
    """
    Handles the submission of the updated leave request form.
    This function re-validates the new data and saves the changes.
    """
    try:
        # Retrieve the request_id from the private_metadata
        private_metadata = json.loads(payload["view"]["private_metadata"])
        request_id = private_metadata["leave_request_id"]
        
        leave_request = LeaveRequest.objects.get(id=request_id)
        employee = leave_request.employee
        
        # Extract new values from the form
        values = payload["view"]["state"]["values"]
        start_date_str = values["start_date_block"]["start_date_input"]["selected_date"]
        end_date_str = values["end_date_block"]["end_date_input"]["selected_date"]
        leave_type_str = values["leave_type_block"]["leave_type_select"]["selected_option"]["value"]
        reason = values["reason_block"]["reason_input"]["value"] or ""

        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

       
        validation_error = validate_leave_request(
            employee, start_date, end_date, leave_type_str, leave_request_to_exclude=leave_request
        )
        if validation_error:
            return validation_error
        
        # Update the LeaveRequest object
        leave_request.start_date = start_date
        leave_request.end_date = end_date
        leave_request.leave_type = LeaveType.objects.get(name=leave_type_str)
        leave_request.reason = reason
        leave_request.save()

        # Log the update
        LeaveRequestAudit.objects.create(leave_request=leave_request, action="updated", performed_by=employee)
        logger.info(f"Leave Request #{leave_request.id} updated by employee.")

        if leave_request.slack_channel_id and leave_request.slack_message_ts:
            slack_client.chat_postMessage(
                channel=leave_request.slack_channel_id,
                thread_ts=leave_request.slack_message_ts, # This makes it a threaded reply
                text=f"ℹ️ This leave request was updated by {leave_request.employee.name}. Please review the new details above."
            )

        # Update the manager's message and notify the employee
        update_approval_message(leave_request, is_updated=True)
        slack_client.chat_postMessage(channel=employee.slack_user_id, text=f"Your leave request for {leave_request.start_date} to {leave_request.end_date} has been successfully updated.")
        
        return HttpResponse(status=200)

    except LeaveRequest.DoesNotExist:
        return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "The original leave request was not found."}})
    except Exception as e:
        logger.error(f"Error handling update submission: {e}")
        return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "A server error occurred during the update."}})


@transaction.atomic
def handle_modal_submission(payload):
    """
    Handles the submission of the leave request modal. This function contains
    all business logic for validating a new leave request before creation.
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


        validation_error = validate_leave_request(employee, start_date, end_date, leave_type_str)
        if validation_error:
            return validation_error

        
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

        # Schedule the reminder to be sent in 24 hours (86400 seconds).
        # apply_async is used for more explicit task options like countdown.
        send_manager_reminder.apply_async(args=[leave_request.id], countdown=120) #86400)
 
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
    """Handles manager's actions, including the improved calendar view."""
    try:
        user_info = payload["user"]
        action = payload["actions"][0]
        action_id = action["action_id"]
        manager = Employee.objects.get(slack_user_id=user_info["id"])

        # --- UNIFIED CALENDAR NAVIGATION ---
        if action_id in ["navigate_calendar_prev", "navigate_calendar_next"]:
            new_month_date = datetime.strptime(action["value"], "%Y-%m-%d").date()
            original_title = payload["view"]["title"]["text"]
            
            summary_info = None # Default to no summary for manager view
            if "My Leave Calendar" in original_title:
                # Employee is navigating their own calendar
                leave_requests = LeaveRequest.objects.filter(employee=manager, start_date__year=new_month_date.year, start_date__month=new_month_date.month)
                
                # Recalculate summary for the new month
                requests_in_new_month = leave_requests.filter(status__in=['pending', 'approved'])
                days_taken = sum(req.duration_days for req in requests_in_new_month)
                summary_info = {
                    "allowance": manager.monthly_leave_allowance,
                    "remaining": manager.monthly_leave_allowance - days_taken
                }
            else:
                # Manager is navigating the team calendar
                leave_requests = LeaveRequest.objects.filter(status='approved', start_date__year=new_month_date.year, start_date__month=new_month_date.month)
            
            new_modal_view = get_calendar_view_modal(leave_requests, new_month_date, original_title, manager.id, summary_info)
            slack_client.views_update(view_id=payload["view"]["id"], view=new_modal_view)
            return HttpResponse(status=200)

        # --- MANAGER'S BUTTON ACTIONS ---
        request_id = int(action["value"])
        leave_request = LeaveRequest.objects.get(id=request_id)
        
        if action_id == "view_overlapping_leave":
            # --- MANAGER TEAM CALENDAR VIEW ---
            month_date = leave_request.start_date
            approved_leaves = LeaveRequest.objects.filter(status='approved', start_date__year=month_date.year, start_date__month=month_date.month)
            # Open the calendar with NO summary info
            calendar_modal = get_calendar_view_modal(approved_leaves, month_date, "Team Leave Calendar", manager.id, summary_info=None)
            slack_client.views_open(trigger_id=payload["trigger_id"], view=calendar_modal)
            return HttpResponse(status=200)

        if leave_request.status != 'pending':
            slack_client.chat_postMessage(channel=manager.slack_user_id, text=f"This leave request for {leave_request.employee.name} has already been actioned.")
            return HttpResponse(status=200)

        if action_id == "approve_leave":
            leave_request.status = "approved"
            if leave_request.leave_type.name not in ["Unplanned", "Emergency"]:
                post_public_announcement(leave_request)
        elif action_id == "reject_leave":
            leave_request.status = "rejected"
        else:
            return HttpResponse(status=400)
            
        leave_request.approver = manager
        leave_request.save()

        LeaveRequestAudit.objects.create(leave_request=leave_request, action=leave_request.status, performed_by=manager)
        update_approval_message(leave_request)
        notify_employee(leave_request)
        return HttpResponse(status=200)

    except Exception as e:
        logger.error(f"Error in handle_button_actions: {e}")
        return HttpResponse("An error occurred.")
# ==============================================================================
# 3. Helper Functions (For sending and updating Slack messages)
# ==============================================================================

def send_approval_request(leave_request):
    """
    Sends a leave request notification to the appropriate manager via DM,
    with a fallback to a general channel.
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

def update_approval_message(leave_request: LeaveRequest, is_updated=False, is_cancelled=False):
    """
    Updates the original manager's message to show the final status.
    Now passes flags for updated or cancelled states.
    """
    if not (leave_request.slack_channel_id and leave_request.slack_message_ts):
        logger.error(f"Missing channel or timestamp for LeaveRequest #{leave_request.id}, cannot update approval message.")
        return

    try:
        # Determine the final state to generate the correct blocks
        is_completed = leave_request.status in ['approved', 'rejected']
        
        blocks = get_approval_message_blocks(
            leave_request, 
            is_completed=is_completed, 
            is_updated=is_updated, 
            is_cancelled=is_cancelled
        )
        
        text = f"Leave request for {leave_request.employee.name} has been {leave_request.status}."
        if is_updated:
            text = f"Leave request for {leave_request.employee.name} has been updated."
        elif is_cancelled:
            text = f"Leave request for {leave_request.employee.name} has been cancelled."
            
        slack_client.chat_update(
            channel=leave_request.slack_channel_id,
            ts=leave_request.slack_message_ts,
            text=text,
            blocks=blocks
        )
    except SlackApiError as e:
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
    Posts a public message about an approved leave.
    
    It first tries to post to the employee's designated team channel.
    If no team channel is configured, it falls back to the common
    SLACK_REQUEST_CHANNEL defined in the .env file.
    """
    employee = leave_request.employee
    channel_id = None

    # 1. Try to get the specific team channel first.
    if employee.team and employee.team.slack_channel_id:
        channel_id = employee.team.slack_channel_id
    else:
        # 2. If not found, fall back to the common channel.
        logger.warning(f"No team channel for {employee.name}. Falling back to common announcement channel.")
        channel_id = os.getenv("SLACK_REQUEST_CHANNEL")

    # 3. If neither channel is configured, skip the announcement.
    if not channel_id:
        logger.error("No team channel or fallback SLACK_REQUEST_CHANNEL is configured. Skipping announcement.")
        return
    
    # Format the message (no changes here)
    employee_name = employee.name
    start_date = leave_request.start_date.strftime('%B %d')
    end_date = leave_request.end_date.strftime('%B %d')
    
    message = f"FYI: {employee_name} will be on leave from {start_date} to {end_date}."
    if leave_request.start_date == leave_request.end_date:
        message = f"FYI: {employee_name} will be on leave on {start_date}."

    # Post the message to the determined channel
    try:
        slack_client.chat_postMessage(channel=channel_id, text=message)
        logger.info(f"Posted announcement for leave request #{leave_request.id} to channel {channel_id}")
    except SlackApiError as e:
        logger.error(f"Error posting announcement to channel {channel_id}: {e.response['error']}")