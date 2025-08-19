# leavebot/slackapp/views.py

"""
Main Views for the Slack Leave Management Application.

This module acts as the primary controller, handling all incoming webhooks from
Slack. It is responsible for parsing requests, dispatching them to the correct
business logic handlers, and communicating back to the Slack API.

The main entry points are:
- `slash_command`: Receives and routes all slash command invocations (e.g., /applyleave).
- `interactions`: Receives and routes all interactive events (e.g., button
                  clicks, modal submissions).
"""

# Standard library imports
import json
import logging
import os
import urllib.parse
from datetime import date, datetime, timedelta
from typing import Dict, Any, Optional

# Django imports
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

# Third-party imports
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Local application imports
from .models import Employee, Holiday, LeaveRequest, LeaveRequestAudit, LeaveType
from .slack_blocks import (get_approval_message_blocks, get_calendar_view_modal,
                           get_employee_notification_blocks,
                           get_leave_form_modal, get_selection_modal,
                           get_update_form_modal)
from .tasks import send_manager_reminder
from .utils import slack_verification_required

# --- Initialization & Constants ---
SLACK_CLIENT = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
LOGGER = logging.getLogger(__name__)

# For better maintainability, define special leave types as constants.
UNPLANNED_LEAVE_TYPES = ["Unplanned", "Emergency"]


# ==============================================================================
# 1. Main Slack Entry Points (Webhook Receivers)
# ==============================================================================

@csrf_exempt
@require_POST
@slack_verification_required
def slash_command(request: HttpRequest) -> HttpResponse:
    """
    Handles and routes incoming slash commands from Slack.

    This view acts as a dispatcher, parsing the command and user data, and
    then calling the appropriate handler function based on the command issued.
    """
    try:
        command_data = urllib.parse.parse_qs(request.body.decode())
        command = command_data.get("command", [None])[0]
        user_id = command_data.get("user_id", [None])[0]
        user_name = command_data.get("user_name", [None])[0]
        trigger_id = command_data.get("trigger_id", [None])[0]

        if not all([command, user_id, trigger_id]):
            LOGGER.warning("Slash command received with missing data.")
            return HttpResponse("Invalid command data.", status=400)

        # Proactively create an Employee record if one doesn't exist.
        # This ensures any user interacting with the bot is in our system.
        employee, _ = Employee.objects.get_or_create(
            slack_user_id=user_id,
            defaults={"name": user_name, "email": f"{user_id}@slackuser.com"}
        )

        LOGGER.info(f"Slash command '{command}' received from {employee.name} ({user_id})")

        # --- Command Routing ---
        # A dictionary-based router is cleaner and more scalable than if/elif chains.
        command_handlers = {
            "/apply_leave": _handle_apply_leave_command,
            "/my_leaves": _handle_my_leaves_command,
            "/update_leave": _handle_modify_leave_command,
            "/cancel_leave": _handle_modify_leave_command,
        }

        handler = command_handlers.get(command)
        if handler:
            return handler(employee, trigger_id, command)
        else:
            LOGGER.warning(f"Unhandled slash command: {command}")
            return HttpResponse(f"Command '{command}' is not recognized.", status=400)

    except Exception as e:
        LOGGER.exception(f"Unexpected error in slash_command: {e}")
        return HttpResponse("An unexpected error occurred. Please try again.", status=500)

@csrf_exempt
@require_POST
@slack_verification_required
def interactions(request: HttpRequest) -> HttpResponse:
    """
    Handles and routes all incoming interactive payloads from Slack.

    This includes modal submissions, button clicks, and other block actions.
    It parses the payload to determine the interaction type and callback/action ID,
    then dispatches to the appropriate handler.
    """
    try:
        payload = json.loads(request.POST.get("payload"))
        interaction_type = payload.get("type")

        # --- Interaction Routing ---
        if interaction_type == "view_submission":
            callback_id = payload.get("view", {}).get("callback_id")
            handler = VIEW_SUBMISSION_HANDLERS.get(callback_id)
        elif interaction_type == "block_actions":
            action_id = payload.get("actions", [{}])[0].get("action_id")
            handler = BLOCK_ACTION_HANDLERS.get(action_id)
        else:
            handler = None

        if handler:
            return handler(payload)
        else:
            unhandled_id = locals().get("callback_id") or locals().get("action_id", "N/A")
            LOGGER.warning(f"Unhandled interaction. Type: '{interaction_type}', ID: '{unhandled_id}'")
            return HttpResponse(status=200)

    except Exception as e:
        LOGGER.exception(f"Unexpected error in interactions view: {e}")
        return HttpResponse("An error occurred while processing your request.", status=500)


# ==============================================================================
# 2. Command Handlers
# ==============================================================================

def _handle_apply_leave_command(employee: Employee, trigger_id: str, command: str) -> HttpResponse:
    """Opens the new leave request modal for the user."""
    modal_view = get_leave_form_modal()
    SLACK_CLIENT.views_open(trigger_id=trigger_id, view=modal_view)
    return HttpResponse(status=200)

def _handle_my_leaves_command(employee: Employee, trigger_id: str, command: str) -> HttpResponse:
    """Opens a calendar view of the user's own leaves for the current month."""
    today = date.today()

    # Calculate leave allowance summary for the current month.
    requests_this_month = LeaveRequest.objects.filter(
        employee=employee,
        status__in=[LeaveRequest.STATUS_PENDING, LeaveRequest.STATUS_APPROVED],
        start_date__year=today.year,
        start_date__month=today.month
    )
    days_taken = sum(req.duration_days for req in requests_this_month)
    summary_info = {
        "allowance": employee.monthly_leave_allowance,
        "remaining": employee.monthly_leave_allowance - days_taken
    }

    # Generate and open the calendar modal.
    calendar_modal = get_calendar_view_modal(
        leave_requests=requests_this_month,
        month_date=today,
        title="My Leave Calendar",
        viewer_employee_id=employee.id,
        summary_info=summary_info
    )
    SLACK_CLIENT.views_open(trigger_id=trigger_id, view=calendar_modal)
    return HttpResponse(status=200)

def _handle_modify_leave_command(employee: Employee, trigger_id: str, command: str) -> HttpResponse:
    """Opens a modal for the user to select a pending leave to update or cancel."""
    pending_requests = LeaveRequest.objects.filter(
        employee=employee, status=LeaveRequest.STATUS_PENDING
    ).order_by('start_date')

    if not pending_requests.exists():
        SLACK_CLIENT.chat_postEphemeral(
            channel=employee.slack_user_id,
            user=employee.slack_user_id,
            text="You have no pending leave requests to modify."
        )
        return HttpResponse(status=200)

    action_type = "update" if command == "/update_leave" else "cancel"
    modal_view = get_selection_modal(pending_requests, action_type)
    SLACK_CLIENT.views_open(trigger_id=trigger_id, view=modal_view)
    return HttpResponse(status=200)


# ==============================================================================
# 3. Interaction Handlers
# =================================================
@transaction.atomic
def handle_new_leave_submission(payload: Dict[str, Any]) -> HttpResponse:
    """
    Handles the submission of the new leave request modal. Validates the data,
    creates the leave request, and triggers notifications.
    """
    user_id = payload["user"]["id"]
    values = payload["view"]["state"]["values"]
    
    try:
        employee = Employee.objects.get(slack_user_id=user_id)
        start_date_str = values["start_date_block"]["start_date_input"]["selected_date"]
        end_date_str = values["end_date_block"]["end_date_input"]["selected_date"]
        leave_type_str = values["leave_type_block"]["leave_type_select"]["selected_option"]["value"]
        reason = values["reason_block"]["reason_input"]["value"] or ""

        # Validate form data.
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        validation_error = _validate_leave_request(employee, start_date, end_date, leave_type_str)
        if validation_error:
            return validation_error

        # If validation passes, create the leave request.
        leave_type = LeaveType.objects.get(name=leave_type_str)
        leave_request = LeaveRequest.objects.create(
            employee=employee,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            reason=reason,
        )
        LeaveRequestAudit.objects.create(leave_request=leave_request, action="created", performed_by=employee)
        LOGGER.info(f"Leave Request #{leave_request.id} created for {employee.name}")

        # Schedule a reminder for the manager using Celery.
        send_manager_reminder.apply_async(args=[leave_request.id], countdown=86400) # 24 hours

        # Send notifications.
        _send_approval_request(leave_request)
        SLACK_CLIENT.chat_postMessage(
            channel=employee.slack_user_id,
            text=f"Your leave request for *{leave_type.name}* from {start_date} to {end_date} has been submitted."
        )
        return HttpResponse(status=200)

    except (Employee.DoesNotExist, LeaveType.DoesNotExist) as e:
        error_msg = "Your user profile was not found." if isinstance(e, Employee.DoesNotExist) else f"Leave type '{leave_type_str}' is invalid."
        return JsonResponse({"response_action": "errors", "errors": {"start_date_block": error_msg}})
    except Exception as e:
        LOGGER.exception(f"Error in handle_new_leave_submission: {e}")
        return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "A server error occurred."}})

@transaction.atomic
def handle_cancel_submission(payload: Dict[str, Any]) -> HttpResponse:
    """Handles the final confirmation for cancelling a leave request."""
    try:
        values = payload["view"]["state"]["values"]
        request_id = int(values["request_selection_block"]["request_select_action"]["selected_option"]["value"])
        leave_request = LeaveRequest.objects.get(id=request_id)

        if leave_request.status != LeaveRequest.STATUS_PENDING:
            SLACK_CLIENT.chat_postEphemeral(
                user=leave_request.employee.slack_user_id,
                channel=leave_request.employee.slack_user_id,
                text="This request cannot be cancelled as it has already been actioned."
            )
            return HttpResponse(status=200)

        leave_request.status = LeaveRequest.STATUS_CANCELLED
        leave_request.save(update_fields=['status'])
        LeaveRequestAudit.objects.create(leave_request=leave_request, action="cancelled", performed_by=leave_request.employee)
        LOGGER.info(f"Leave Request #{leave_request.id} cancelled by employee.")

        # Update the original manager message and notify the employee.
        _update_approval_message(leave_request)
        _notify_employee(leave_request)
        return HttpResponse(status=200)

    except LeaveRequest.DoesNotExist:
        LOGGER.error(f"Attempt to cancel non-existent leave request.")
        return HttpResponse("Error: The request was not found.", status=404)


@transaction.atomic
def handle_update_submission(payload: Dict[str, Any]) -> HttpResponse:
    """Handles the submission of the updated leave request form."""
    try:
        private_metadata = json.loads(payload["view"]["private_metadata"])
        request_id = private_metadata["leave_request_id"]
        leave_request = LeaveRequest.objects.get(id=request_id)
        
        values = payload["view"]["state"]["values"]
        start_date_str = values["start_date_block"]["start_date_input"]["selected_date"]
        end_date_str = values["end_date_block"]["end_date_input"]["selected_date"]
        leave_type_str = values["leave_type_block"]["leave_type_select"]["selected_option"]["value"]
        reason = values["reason_block"]["reason_input"]["value"] or ""

        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

        # Validate the updated data, excluding the current request from checks.
        validation_error = _validate_leave_request(
            leave_request.employee, start_date, end_date, leave_type_str, leave_request_to_exclude=leave_request
        )
        if validation_error:
            return validation_error

        # If validation passes, update the request.
        leave_request.start_date = start_date
        leave_request.end_date = end_date
        leave_request.leave_type = LeaveType.objects.get(name=leave_type_str)
        leave_request.reason = reason
        leave_request.save()
        LeaveRequestAudit.objects.create(leave_request=leave_request, action="updated", performed_by=leave_request.employee)
        LOGGER.info(f"Leave Request #{leave_request.id} updated by employee.")

        # Update the original manager message and notify the employee.
        _update_approval_message(leave_request, is_updated=True)
        _notify_employee(leave_request)
        return HttpResponse(status=200)

    except LeaveRequest.DoesNotExist:
        return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "The original request was not found."}})

@transaction.atomic
def handle_update_selection(payload: Dict[str, Any]) -> JsonResponse:
    """
    Handles the user selecting a leave request from a dropdown.
    Updates the current modal to a pre-filled edit form.
    """
    try:
        values = payload["view"]["state"]["values"]
        selected_request_id = values["request_selection_block"]["request_select_action"]["selected_option"]["value"]
        leave_request = LeaveRequest.objects.get(id=int(selected_request_id))
        
        # Build the new modal view, pre-filled with the request's data.
        update_modal_view = get_update_form_modal(leave_request)
        
        # Return a JSON response to tell Slack to update the view directly.
        return JsonResponse({"response_action": "update", "view": update_modal_view})
    except Exception as e:
        LOGGER.exception(f"Error in handle_update_selection: {e}")
        return JsonResponse({"response_action": "clear"}) # Close modal on error

def _handle_manager_approval_action(payload: Dict[str, Any]) -> HttpResponse:
    """Handles a manager approving or rejecting a leave request."""
    user_id = payload["user"]["id"]
    action = payload["actions"][0]
    request_id = int(action["value"])

    manager = Employee.objects.get(slack_user_id=user_id)
    leave_request = LeaveRequest.objects.get(id=request_id)

    if leave_request.status != LeaveRequest.STATUS_PENDING:
        SLACK_CLIENT.chat_postEphemeral(
            user=user_id,
            channel=user_id,
            text=f"This request for {leave_request.employee.name} has already been actioned."
        )
        return HttpResponse(status=200)
    
    action_id = action["action_id"]
    if action_id == "approve_leave":
        leave_request.status = LeaveRequest.STATUS_APPROVED
        if leave_request.leave_type.name not in UNPLANNED_LEAVE_TYPES:
            _post_public_announcement(leave_request)
    elif action_id == "reject_leave":
        leave_request.status = LeaveRequest.STATUS_REJECTED
    else:
        return HttpResponse(status=400) # Should not happen

    leave_request.approver = manager
    leave_request.save()
    LeaveRequestAudit.objects.create(leave_request=leave_request, action=leave_request.status, performed_by=manager)
    
    _update_approval_message(leave_request)
    _notify_employee(leave_request)
    return HttpResponse(status=200)

def _handle_view_team_calendar(payload: Dict[str, Any]) -> HttpResponse:
    """Opens a calendar view of approved team leaves for the manager."""
    user_id = payload["user"]["id"]
    request_id = int(payload["actions"][0]["value"])
    manager = Employee.objects.get(slack_user_id=user_id)
    leave_request = LeaveRequest.objects.get(id=request_id)
    
    month_date = leave_request.start_date
    approved_leaves = LeaveRequest.objects.filter(
        status=LeaveRequest.STATUS_APPROVED,
        start_date__year=month_date.year,
        start_date__month=month_date.month
    )
    calendar_modal = get_calendar_view_modal(
        approved_leaves, month_date, "Team Leave Calendar", manager.id
    )
    SLACK_CLIENT.views_open(trigger_id=payload["trigger_id"], view=calendar_modal)
    return HttpResponse(status=200)

def _handle_calendar_navigation(payload: Dict[str, Any]) -> HttpResponse:
    """Handles clicks on the 'Previous' and 'Next' buttons in a calendar modal."""
    new_month_date = datetime.strptime(payload["actions"][0]["value"], "%Y-%m-%d").date()
    original_title = payload["view"]["title"]["text"]
    user_id = payload["user"]["id"]
    employee = Employee.objects.get(slack_user_id=user_id)

    # Determine if it's a personal or team calendar to fetch correct data.
    if "My Leave Calendar" in original_title:
        leave_requests = LeaveRequest.objects.filter(
            employee=employee,
            start_date__year=new_month_date.year,
            start_date__month=new_month_date.month
        )
        # Recalculate summary for the new month.
        days_taken = sum(req.duration_days for req in leave_requests.filter(status__in=[LeaveRequest.STATUS_PENDING, LeaveRequest.STATUS_APPROVED]))
        summary_info = {
            "allowance": employee.monthly_leave_allowance,
            "remaining": employee.monthly_leave_allowance - days_taken
        }
    else: # Team calendar view
        leave_requests = LeaveRequest.objects.filter(
            status=LeaveRequest.STATUS_APPROVED,
            start_date__year=new_month_date.year,
            start_date__month=new_month_date.month
        )
        summary_info = None

    new_modal_view = get_calendar_view_modal(
        leave_requests, new_month_date, original_title, employee.id, summary_info
    )
    SLACK_CLIENT.views_update(view_id=payload["view"]["id"], view=new_modal_view)
    return HttpResponse(status=200)


# ==============================================================================
# 4. Validation & Helper Functions
# ==============================================================================

def _validate_leave_request(employee: Employee,start_date: date,end_date: date,leave_type_str: str,leave_request_to_exclude: LeaveRequest = None) -> Optional[JsonResponse]:
    """
    A reusable function to validate leave request data against business rules.

    Args:
        employee: The Employee submitting the request.
        start_date: The proposed start date.
        end_date: The proposed end date.
        leave_type_str: The name of the leave type.
        leave_request_to_exclude: Optional. A LeaveRequest to exclude from checks
                                  (used when updating an existing request).
    Returns:
        A JsonResponse with a Slack-formatted error if validation fails,
        otherwise returns None.
    """
    # Rule 1: Validate date logic (past dates, end before start).
    today = date.today()
    if leave_type_str in UNPLANNED_LEAVE_TYPES:
        if start_date > today or end_date > today:
            return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "Unplanned leave must be for a past or current date."}})
    elif start_date < today:
        return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "Planned leave cannot be for a past date."}})
    if end_date < start_date:
        return JsonResponse({"response_action": "errors", "errors": {"end_date_block": "End date cannot be before the start date."}})

    # Rule 2: Calculate requested business days, excluding holidays.
    holidays = set(Holiday.objects.filter(date__range=[start_date, end_date]).values_list('date', flat=True))
    requested_days = sum(1 for day_offset in range((end_date - start_date).days + 1)
                         if (start_date + timedelta(days=day_offset)).weekday() < 5
                         and (start_date + timedelta(days=day_offset)) not in holidays)

    
    if requested_days == 0:
        return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "The selected dates fall on a weekend or holiday."}})

    # Rule 3: Check for overlapping leave requests.
    overlapping_query = LeaveRequest.objects.filter(
        employee=employee,
        status__in=[LeaveRequest.STATUS_PENDING, LeaveRequest.STATUS_APPROVED],
        start_date__lte=end_date,
        end_date__gte=start_date
    )
    if leave_request_to_exclude:
        overlapping_query = overlapping_query.exclude(id=leave_request_to_exclude.id)
    if overlapping_query.exists():
        return JsonResponse({"response_action": "errors", "errors": {"start_date_block": "You have an overlapping leave request for these dates."}})

    # Rule 4: Check if remaining monthly allowance is sufficient.
    requests_this_month = LeaveRequest.objects.filter(
        employee=employee,
        status__in=[LeaveRequest.STATUS_PENDING, LeaveRequest.STATUS_APPROVED],
        start_date__year=start_date.year,
        start_date__month=start_date.month
    )
    if leave_request_to_exclude:
        requests_this_month = requests_this_month.exclude(id=leave_request_to_exclude.id)
    
    days_taken = sum(req.duration_days for req in requests_this_month)
    remaining_allowance = employee.monthly_leave_allowance - days_taken

    if requested_days > remaining_allowance:
        err_msg = f"You are requesting {requested_days} day(s), but only have {remaining_allowance} day(s) left this month."
        return JsonResponse({"response_action": "errors", "errors": {"start_date_block": err_msg}})
    
    return None # All checks passed

def _send_approval_request(leave_request: LeaveRequest):
    """Sends a leave request notification to the appropriate manager or fallback channel."""
    destination_channel = (leave_request.employee.manager.slack_user_id
                           if leave_request.employee.manager
                           else os.getenv("SLACK_FALLBACK_CHANNEL"))

    if not destination_channel:
        LOGGER.error(f"Cannot send approval for L R#{leave_request.id}: No manager or fallback channel.")
        return

    try:
        blocks = get_approval_message_blocks(leave_request)
        response = SLACK_CLIENT.chat_postMessage(
            channel=destination_channel,
            text=f"New leave request from {leave_request.employee.name}",
            blocks=blocks
        )
        leave_request.slack_message_ts = response["ts"]
        leave_request.slack_channel_id = response["channel"]
        leave_request.save(update_fields=["slack_message_ts", "slack_channel_id"])
    except SlackApiError as e:
        LOGGER.exception(f"Slack API error sending approval request for LR#{leave_request.id}: {e.response['error']}")

def _update_approval_message(leave_request: LeaveRequest, is_updated: bool = False):
    """Updates the original manager's message to show the final or updated status."""
    if not (leave_request.slack_channel_id and leave_request.slack_message_ts):
        LOGGER.error(f"Cannot update approval message for LR#{leave_request.id}: missing channel/ts.")
        return

    try:
        is_completed = leave_request.status in [LeaveRequest.STATUS_APPROVED, LeaveRequest.STATUS_REJECTED,LeaveRequest.STATUS_CANCELLED]
        blocks = get_approval_message_blocks(
            leave_request, is_completed=is_completed, is_updated=is_updated
        )
        
        status_text = f"Leave request for {leave_request.employee.name} has been {leave_request.status}."
        if is_updated:
            status_text = f"Leave request for {leave_request.employee.name} has been updated."

        SLACK_CLIENT.chat_update(
            channel=leave_request.slack_channel_id,
            ts=leave_request.slack_message_ts,
            text=status_text,
            blocks=blocks
        )
    except SlackApiError as e:
        LOGGER.exception(f"Slack API error updating approval message for LR#{leave_request.id}: {e.response['error']}")

def _notify_employee(leave_request: LeaveRequest):
    """Sends a final confirmation DM to the employee about their request status."""
    try:
        blocks = get_employee_notification_blocks(leave_request)
        text = f"Your leave request for {leave_request.start_date} has been {leave_request.status}."
        SLACK_CLIENT.chat_postMessage(
            channel=leave_request.employee.slack_user_id, text=text, blocks=blocks
        )
    except SlackApiError as e:
        LOGGER.exception(f"Slack API error sending notification to employee for LR#{leave_request.id}: {e.response['error']}")

def _post_public_announcement(leave_request: LeaveRequest):
    """Posts a public message about an approved leave to the relevant team or fallback channel."""
    employee = leave_request.employee
    channel_id = (employee.team.slack_channel_id if employee.team and employee.team.slack_channel_id
                  else os.getenv("SLACK_FALLBACK_CHANNEL"))

    if not channel_id:
        LOGGER.error(f"Cannot post announcement for LR#{leave_request.id}: No team or fallback channel.")
        return

    start_str = leave_request.start_date.strftime('%B %d')
    end_str = leave_request.end_date.strftime('%B %d')
    message = (f"FYI: {employee.name} will be on leave on {start_str}." if start_str == end_str
               else f"FYI: {employee.name} will be on leave from {start_str} to {end_str}.")

    try:
        SLACK_CLIENT.chat_postMessage(channel=channel_id, text=message)
        LOGGER.info(f"Posted announcement for LR#{leave_request.id} to channel {channel_id}")
    except SlackApiError as e:
        LOGGER.exception(f"Slack API error posting announcement for LR#{leave_request.id} to channel {channel_id}: {e.response['error']}")


# ==============================================================================
# 5. Interaction Handler Mappings
# ==============================================================================

# Using dictionaries for routing makes the `interactions` view cleaner and
# more extensible. To add new functionality, you just add an entry here.
VIEW_SUBMISSION_HANDLERS = {
    "leave_request_modal": handle_new_leave_submission,
    "cancel_leave_submission": handle_cancel_submission,
    "leave_update_modal_submission": handle_update_submission,
    "select_leave_to_update": handle_update_selection,
}

BLOCK_ACTION_HANDLERS = {
    "approve_leave": _handle_manager_approval_action,
    "reject_leave": _handle_manager_approval_action,
    "view_overlapping_leave": _handle_view_team_calendar,
    "navigate_calendar_prev": _handle_calendar_navigation,
    "navigate_calendar_next": _handle_calendar_navigation,
    "request_select_action": handle_update_selection, # This is a special case that updates a view
}