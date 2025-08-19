# leavebot/slackapp/slack_blocks.py

"""
Slack Block Kit Construction Utilities

This module provides functions to dynamically generate JSON structures for Slack's
Block Kit UI framework. These functions create interactive and visually appealing
messages and modals for the leave management workflow, such as the leave
request form, manager approval messages, and team calendars.

Separating block generation logic into this file keeps the main application
logic in `views.py` cleaner and more focused on handling events and data,
adhering to the principle of separation of concerns.
"""

# Standard library imports
import json
from calendar import month_name, monthrange
from datetime import date, timedelta
from typing import List, Dict, Any
from django.utils import timezone

# Local application imports
from .models import LeaveRequest, LeaveType


def get_leave_form_modal() -> Dict[str, Any]:
    """
    Generates the Slack modal view for submitting a new leave request.

    This function dynamically populates the 'Leave Type' dropdown by querying all
    available `LeaveType` objects from the database. This makes the form
    flexible and easily manageable via the Django admin panel without code changes.
    The start and end dates default to the next day for user convenience.

    Returns:
        A dictionary representing the JSON structure for the Slack modal.
    """
    tomorrow = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')

    # --- Dynamic Leave Type Options ---
    # Query the database to populate the dropdown, ensuring the form is always
    # up-to-date with the available leave categories.
    try:
        leave_types = LeaveType.objects.all()
        if leave_types.exists():
            # Create a list of option objects for the dropdown menu.
            options = [
                {
                    "text": {"type": "plain_text", "text": leave_type.name, "emoji": True},
                    "value": leave_type.name,  # This value is sent to the app upon submission.
                }
                for leave_type in leave_types
            ]
            initial_option = options[0]  # Set a sensible default.
        else:
            # Provide a fallback option if no leave types are configured in the database.
            options = [{
                "text": {"type": "plain_text", "text": "No leave types found", "emoji": True},
                "value": "error_no_leave_types"
            }]
            initial_option = None
    except Exception:
        # Handle potential database connection errors gracefully.
        options = [{
            "text": {"type": "plain_text", "text": "Error loading leave types", "emoji": True},
            "value": "error_db_connection"
        }]
        initial_option = None

    return {
        "type": "modal",
        "callback_id": "leave_request_modal",
        "title": {"type": "plain_text", "text": "Request Leave"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Submit your leave request*\nPlease fill out all fields below."
                }
            },
            {"type": "divider"},
            {
                "type": "input",
                "block_id": "start_date_block",
                "element": {
                    "type": "datepicker",
                    "action_id": "start_date_input",
                    "initial_date": tomorrow,
                    "placeholder": {"type": "plain_text", "text": "Select a date"}
                },
                "label": {"type": "plain_text", "text": "Start Date"}
            },
            {
                "type": "input",
                "block_id": "end_date_block",
                "element": {
                    "type": "datepicker",
                    "action_id": "end_date_input",
                    "initial_date": tomorrow,
                    "placeholder": {"type": "plain_text", "text": "Select a date"}
                },
                "label": {"type": "plain_text", "text": "End Date"}
            },
            {
                "type": "input",
                "block_id": "leave_type_block",
                "element": {
                    "type": "static_select",
                    "action_id": "leave_type_select",
                    "placeholder": {"type": "plain_text", "text": "Select leave type"},
                    "options": options,
                    "initial_option": initial_option
                },
                "label": {"type": "plain_text", "text": "Leave Type"}
            },
            {
                "type": "input",
                "block_id": "reason_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "reason_input",
                    "multiline": True,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Provide additional details (e.g., flight times, reason for sick day)."
                    }
                },
                "label": {"type": "plain_text", "text": "Reason"},
                "optional": False  # This field is mandatory.
            }
        ]
    }


def get_approval_message_blocks(leave_request: LeaveRequest,is_completed: bool = False,is_updated: bool = False) ->List[Dict[str, Any]]:
    """
    Generates the Slack blocks for a manager's approval message.

    This versatile function constructs the message body based on the request's
    state. It can display action buttons for pending requests or a final status
    for completed ones. It also adds a prominent "UPDATED" tag if the
    request was modified by the employee.

    Args:
        leave_request: The `LeaveRequest` model instance.
        is_completed: If True, hides action buttons and shows the final status.
        is_updated: If True, adds a warning that the request has been updated.

    Returns:
        A list of dictionaries representing the Slack message blocks.
    """
    employee_name = leave_request.employee.name
    slack_user_id = leave_request.employee.slack_user_id
    leave_type_name = leave_request.leave_type.name

    # --- 1. Determine the Header ---
    header_text = f"New Leave Request for {employee_name}"
    if is_updated:
        header_text = f"UPDATED: {header_text}"

    blocks = [{"type": "header", "text": {"type": "plain_text", "text": header_text, "emoji": True}}]

    # Add a visual warning if the request has been updated by the employee.
    if is_updated:
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": ":warning: *The details of this request have been modified by the employee.*"
            }]
        })

    # --- 2. Build the Core Details Section ---
    duration_days = leave_request.duration_days
    day_text = "day" if duration_days == 1 else "days"

    if leave_request.start_date == leave_request.end_date:
        duration_str = leave_request.start_date.strftime('%B %d, %Y')
    else:
        duration_str = f"{leave_request.start_date.strftime('%b %d')} to {leave_request.end_date.strftime('%b %d, %Y')}"

    # Use 'fields' for a neat two-column layout of key information.
    status_emoji_map = {
        LeaveRequest.STATUS_PENDING: "⏳",
        LeaveRequest.STATUS_APPROVED: "✅",
        LeaveRequest.STATUS_REJECTED: "❌",
        LeaveRequest.STATUS_CANCELLED: "🗑️",
    }
    status_emoji = status_emoji_map.get(leave_request.status, "❔")
    
    details_section = {
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Employee:*\n<@{slack_user_id}>"},
            {"type": "mrkdwn", "text": f"*Dates Requested:*\n{duration_str} (*{duration_days} {day_text}*)"},
            # Use an invisible spacer to create a clean visual break between rows.
            {"type": "mrkdwn", "text": "\u00A0"},
            {"type": "mrkdwn", "text": "\u00A0"},
            {"type": "mrkdwn", "text": f"*Leave Type:*\n{leave_type_name}"},
            {"type": "mrkdwn", "text": f"*Status:*\n{status_emoji} {leave_request.status.title()}"},
        ]
    }
    blocks.append(details_section)

    # Only add the "Reason" section if one was provided.
    if leave_request.reason:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Reason Provided:*\n>>> {leave_request.reason}"}})

    local_created_at = timezone.localtime(leave_request.created_at)
    # Add a context block for metadata (Request ID and submission date).
    blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": f"Request ID: #{leave_request.id} | Submitted: {local_created_at.strftime('%b %d, %Y at %I:%M %p')}"
        }]
    })

    # --- 3. Add Final Status or Action Buttons ---
    if is_completed:
        # If the request is already actioned, show the final status.
        manager_name = leave_request.approver.name if leave_request.approver else "System"
        if leave_request.status == LeaveRequest.STATUS_CANCELLED:
            # For cancellations, the actor is the employee who submitted it.
            actor_name = leave_request.employee.name
            status_text = f"{status_emoji} This request was *Cancelled* by {actor_name}."
        else:
            # For approved/rejected, the actor is the manager (approver).
            actor_name = leave_request.approver.name if leave_request.approver else "System"
            status_text = f"{status_emoji} This request was *{leave_request.status.title()}* by {actor_name}."

        blocks.extend([
            {"type": "section", "text": {"type": "mrkdwn", "text": status_text}},
            {"type": "divider"},{"type": "divider"}
        ])
    else:
        # If the request is pending, show the action buttons.
        request_id_str = str(leave_request.id)
        action_buttons = {
            "type": "actions",
            "block_id": f"approval_actions_{request_id_str}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve", "emoji": True},
                    "style": "primary",
                    "action_id": "approve_leave",
                    "value": request_id_str
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject", "emoji": True},
                    "style": "danger",
                    "action_id": "reject_leave",
                    "value": request_id_str
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Who's Away?", "emoji": True},
                    "action_id": "view_overlapping_leave",
                    "value": request_id_str
                }
            ]
        }
        blocks.extend([action_buttons,{"type": "divider"},{"type": "divider"}])

    return blocks


def get_selection_modal(pending_requests, action_type: str) -> Dict[str, Any]:
    """
    Creates a modal for selecting a pending leave request to update or cancel.

    Args:
        pending_requests: A queryset of the user's pending `LeaveRequest` objects.
        action_type: A string, either 'update' or 'cancel', to customize the modal's text.

    Returns:
        A dictionary representing the JSON structure for the Slack modal.
    """
    if action_type == 'update':
        title = "Update a Leave Request"
        submit_text = "Select to Edit"
        callback_id = "select_leave_to_update"
        placeholder_text = "Choose the request you want to change"
    else:  # 'cancel'
        title = "Cancel a Leave Request"
        submit_text = "Confirm Cancellation"
        callback_id = "cancel_leave_submission"
        placeholder_text = "Choose the request you want to cancel"

    # Create dropdown options from the user's pending requests.
    options = []
    for req in pending_requests:
        if req.start_date == req.end_date:
            duration = req.start_date.strftime('%b %d, %Y')
        else:
            duration = f"{req.start_date.strftime('%b %d')} to {req.end_date.strftime('%b %d')}"

        options.append({
            "text": {"type": "plain_text", "text": f"{req.leave_type.name}: {duration}"},
            "value": str(req.id)
        })

    # If no pending requests exist, display an informative message instead of an empty form.
    if not options:
        return {
            "type": "modal",
            "title": {"type": "plain_text", "text": title},
            "close": {"type": "plain_text", "text": "Close"},
            "blocks": [{
                "type": "section",
                "text": {"type": "mrkdwn", "text": "You have no pending leave requests to modify."}
            }]
        }

    return {
        "type": "modal",
        "callback_id": callback_id,
        "title": {"type": "plain_text", "text": title},
        "submit": {"type": "plain_text", "text": submit_text},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "Please choose one of your pending requests from the list below."}
            },
            {
                "type": "input",
                "block_id": "request_selection_block",
                "element": {
                    "type": "static_select",
                    "action_id": "request_select_action",
                    "placeholder": {"type": "plain_text", "text": placeholder_text},
                    "options": options
                },
                "label": {"type": "plain_text", "text": "Your Pending Requests"}
            }
        ]
    }


def get_update_form_modal(leave_request: LeaveRequest) -> Dict[str, Any]:
    """
    Generates the modal for updating a leave request, pre-filled with existing data.

    This function reuses the structure of the new leave form but populates each
    field with the data from the provided `LeaveRequest` instance, making it
    easy for the user to edit their submission.

    Args:
        leave_request: The `LeaveRequest` instance to be updated.

    Returns:
        The dictionary for the pre-filled Slack modal.
    """
    # Use private_metadata to securely pass the leave_request.id through the
    # modal submission payload without exposing it in the UI.
    private_metadata = json.dumps({"leave_request_id": leave_request.id})

    # Reuse the base modal structure to avoid code duplication
    modal = get_leave_form_modal()

    # --- Override specific fields for the update form ---
    modal["callback_id"] = "leave_update_modal_submission"
    modal["private_metadata"] = private_metadata
    modal["title"]["text"] = "Update Leave Request"
    modal["submit"]["text"] = "Submit Update"

    # Pre-fill the form blocks with existing data from the leave_request object
    modal["blocks"][0]["text"]["text"] = f"*Updating Leave Request #{leave_request.id}*\nPlease modify the details below."
    modal["blocks"][2]["element"]["initial_date"] = leave_request.start_date.strftime('%Y-%m-%d')  # Start Date
    modal["blocks"][3]["element"]["initial_date"] = leave_request.end_date.strftime('%Y-%m-%d')    # End Date
    modal["blocks"][4]["element"]["initial_option"] = {                                             # Leave Type
        "text": {"type": "plain_text", "text": leave_request.leave_type.name, "emoji": True},
        "value": leave_request.leave_type.name
    }
    modal["blocks"][5]["element"]["initial_value"] = leave_request.reason                            # Reason

    return modal


def get_calendar_view_modal(leave_requests,month_date: date,title: str,viewer_employee_id: int = None,summary_info: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Generates an interactive modal with a text-based monthly calendar view.

    This function displays all leaves for a given month, highlighting the viewer's
    own leaves. It can also include a summary of the viewer's leave allowance
    and provides navigation buttons to move to the previous or next month.

    Args:
        leave_requests: A queryset of `LeaveRequest` objects for the given month.
        month_date: A `date` object representing the month to display.
        title: The title for the modal window (e.g., "Team Leave Calendar").
        viewer_employee_id: The ID of the employee viewing the calendar, used
                            to highlight their own approved leaves.
        summary_info: A dict with allowance data for the employee view, e.g.,
                      {'allowance': 2, 'remaining': 1.5}.

    Returns:
        The dictionary for the calendar modal.
    """
    blocks = []

    # --- Conditionally add an employee-specific summary block at the top ---
    if summary_info:
        summary_text = (
            f"*Your Leave Summary for {month_name[month_date.month]} {month_date.year}*\n"
            f"Monthly Allowance: *{summary_info.get('allowance', 'N/A')} days*\n"
            f"Remaining This Month: *{summary_info.get('remaining', 'N/A')} days*"
        )
        blocks.extend([
            {"type": "section", "text": {"type": "mrkdwn", "text": summary_text}},
            {"type": "divider"}
        ])

    # --- 1. Prepare Data ---
    # Group leave requests by day for efficient lookup during calendar generation.
    leaves_by_day = {day: [] for day in range(1, 32)}
    for req in leave_requests:
        current_date = req.start_date
        while current_date <= req.end_date:
            if current_date.month == month_date.month and current_date.year == month_date.year:
                leaves_by_day[current_date.day].append(req)
            current_date += timedelta(days=1)

    # --- 2. Build the Calendar String ---
    calendar_header = f"{title} for {month_name[month_date.month]} {month_date.year}"
    calendar_lines = [calendar_header, "=" * len(calendar_header)]

    first_day_of_month, num_days = monthrange(month_date.year, month_date.month)

    for day in range(1, num_days + 1):
        # Determine the day of the week (0=Mon, 6=Sun).
        weekday = (first_day_of_month + day - 1) % 7
        day_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][weekday]

        # Left-pad the date part for consistent alignment.
        date_part = f"{day_name} {day:02d}:".ljust(10)

        # Determine the status for the day.
        if weekday >= 5:  # Weekend
            status_part = "(Weekend)"
        else:  # Weekday
            requests_for_day = leaves_by_day.get(day, [])
            if requests_for_day:
                status_emoji_map = {
                    LeaveRequest.STATUS_APPROVED: '✅',
                    LeaveRequest.STATUS_PENDING: '⏳',
                }
                day_entries = []
                for req in requests_for_day:
                    # Only show approved or pending leaves on the calendar.
                    if req.status in status_emoji_map:
                        name_display = req.employee.name
                        if viewer_employee_id and req.employee.id == viewer_employee_id:
                            name_display = f"*{name_display} (Your Leave)*"
                        day_entries.append(f"{status_emoji_map[req.status]} {name_display}")

                status_part = ", ".join(day_entries) if day_entries else "Available"
            else:
                status_part = "Available"

        calendar_lines.append(f"{date_part}{status_part}")

        # Add a blank line after each Sunday for readability, except for the last day.
        if weekday == 6 and day < num_days:
            calendar_lines.append("")

    # Join all lines and wrap them in a Slack code block for a fixed-width font.
    calendar_body = "\n".join(calendar_lines)
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"```{calendar_body}```"}})

    # --- 3. Add Navigation Buttons ---
    # Calculate the first day of the previous and next months for navigation values.
    # This method safely handles month rollovers, e.g., March 1st - 1 day = Feb 28th/29th.
    prev_month_date = (month_date.replace(day=1) - timedelta(days=1)).strftime('%Y-%m-01')
    # This method safely gets to the next month, regardless of current month's length (28, 30, 31).
    next_month_date = (month_date.replace(day=28) + timedelta(days=4)).strftime('%Y-%m-01')

    navigation_buttons = {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "⬅️ Previous Month"},
                "action_id": "navigate_calendar_prev",
                "value": prev_month_date
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Next Month ➡️"},
                "action_id": "navigate_calendar_next",
                "value": next_month_date
            }
        ]
    }
    blocks.extend([{"type": "divider"}, navigation_buttons])

    return {
        "type": "modal",
        "callback_id": "team_leave_calendar_modal",
        "title": {"type": "plain_text", "text": title},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": blocks
    }


def get_employee_notification_blocks(leave_request: LeaveRequest) -> List[Dict[str, Any]]:
    """
    Generates a notification message for an employee after their request is actioned.

    This creates a clear and concise message informing the user of the outcome
    of their leave request (approved, rejected, or cancelled).

    Args:
        leave_request: The `LeaveRequest` instance that has been actioned.

    Returns:
        A list of dictionaries representing the Slack message blocks.
    """
    status = leave_request.status
    approver_name = leave_request.approver.name if leave_request.approver else "the system"

    # Customize the header and context text based on the final status.
    if status == LeaveRequest.STATUS_APPROVED:
        header_text = "✅ Your Leave Request was Approved"
        context_text = f"This request was approved by *{approver_name}*."
    elif status == LeaveRequest.STATUS_REJECTED:
        header_text = "❌ Your Leave Request was Rejected"
        context_text = f"This request was rejected by *{approver_name}*."
    else:  # Cancelled
        header_text = "🗑️ Your Leave Request was Cancelled"
        context_text = "You successfully cancelled this request."

    if leave_request.start_date == leave_request.end_date:
        duration_str = leave_request.start_date.strftime('%B %d, %Y')
    else:
        duration_str = f"{leave_request.start_date.strftime('%b %d')} to {leave_request.end_date.strftime('%b %d, %Y')}"

    return [
        {"type": "header", "text": {"type": "plain_text", "text": header_text, "emoji": True}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Leave Type:*\n{leave_request.leave_type.name}"},
                {"type": "mrkdwn", "text": f"*Dates:*\n{duration_str}"}
            ]
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": context_text}]}
    ]