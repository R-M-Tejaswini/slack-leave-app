# leavebot/slackapp/slack_blocks.py
from datetime import date, timedelta
from calendar import month_name, monthrange
from .models import LeaveType, LeaveRequest # Import the new models
import json

def get_leave_form_modal():
    """
    Generates the Slack modal view for the leave request form.

    This function dynamically populates the 'Leave Type' dropdown menu by
    querying all available LeaveType objects from the database, making the
    form flexible and manageable via the Django admin panel.

    Returns:
        dict: The JSON structure for the Slack modal view.
    """

    tomorrow = (date.today() + timedelta(days=1)).strftime('%Y-%m-%d')

    # --- Dynamic Leave Type Options ---
    # Query the database for all available leave types
    leave_types = LeaveType.objects.all()
    options = []
    if leave_types:
        # Create the options list for the dropdown menu
        options = [
            {
                "text": {"type": "plain_text", "text": leave_type.name},
                "value": leave_type.name, # The value sent to your app
            }
            for leave_type in leave_types
        ]
        initial_option = options[0] # Set a default initial option
    else:
        # Provide a fallback if no leave types are in the database
        options = [{
            "text": {"type": "plain_text", "text": "No leave types configured"},
            "value": "error"
        }]
        initial_option = options[0]


    return {
        "type": "modal",
        "callback_id": "leave_request_modal",
        "title": {"type": "plain_text", "text": "Request Leave"},
        "submit": {"type": "plain_text", "text": "Submit Request"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Submit your leave request*\nPlease fill out all required fields below."
                }
            },
            {"type": "divider"},
            {
                "type": "input",
                "block_id": "start_date_block",
                "element": {
                    "type": "datepicker",
                    "action_id": "start_date_input",
                    "initial_date": tomorrow
                },
                "label": {"type": "plain_text", "text": "Start Date *"}
            },
            {
                "type": "input",
                "block_id": "end_date_block",
                "element": {
                    "type": "datepicker",
                    "action_id": "end_date_input",
                    "initial_date": tomorrow
                },
                "label": {"type": "plain_text", "text": "End Date *"}
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
                "label": {"type": "plain_text", "text": "Leave Type *"}
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
                        "text": "Provide additional details about your leave request"
                    }
                },
                "label": {"type": "plain_text", "text": "Reason"},
                "optional": False # Changed to False to match the model field
            }
        ]
    }


def get_approval_message_blocks(leave_request: LeaveRequest, is_completed=False, is_updated=False, is_cancelled=False):
    """
    Generate Slack blocks for the leave approval message.
    This function handles new, updated, completed, and cancelled states.

    Args:
        leave_request: The LeaveRequest model instance.
        is_completed: If True, shows the final status instead of action buttons.
        is_updated: If True, adds an '(Updated)' tag to the title.
        is_cancelled: If True, shows a simple cancellation message.
    """
    employee_name = leave_request.employee.name
    slack_user_id = leave_request.employee.slack_user_id
    leave_type_name = leave_request.leave_type.name

    # --- 1. Determine the Title and Header ---
    is_unplanned = leave_type_name in ["Unplanned", "Emergency"]
    base_title = "Retrospective Leave Submission" if is_unplanned else "New Leave Request"
    header_text = f"{base_title} for {employee_name}"
    if is_updated:
        header_text = f"UPDATED: {header_text}"

    blocks = [{"type": "header", "text": {"type": "plain_text", "text": header_text}}]
    
    # Add a visual warning if the request has been updated
    if is_updated:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": ":warning: *The details of this request have been updated by the employee.*"}]})

    # --- 2. Build the Message Details using Fields for a 2-column layout ---
    duration_days = leave_request.duration_days
    day_text = "day" if duration_days == 1 else "days"
    duration_str = f"{leave_request.start_date.strftime('%b %d')} to {leave_request.end_date.strftime('%b %d')}"
    if leave_request.start_date == leave_request.end_date:
        duration_str = leave_request.start_date.strftime('%B %d, %Y')
    
    # The fields element automatically creates a neat two-column layout
    details_section = {
        "type": "section",
        "fields": [
            # --- First Row ---
            {"type": "mrkdwn", "text": f"*Employee:*\n<@{slack_user_id}>"},
            {"type": "mrkdwn", "text": f"*Dates Requested:*\n{duration_str} (*{duration_days} {day_text}*)"},
            
            # ---  BLANK ROW ---
            {"type": "plain_text", "text": "\u00A0"}, # Invisible spacer for left column
            {"type": "plain_text", "text": "\u00A0"}, # Invisible spacer for right column

            # --- Second Row ---
            {"type": "mrkdwn", "text": f"*Leave Type:*\n{leave_type_name}"},
            {"type": "mrkdwn", "text": f"*Status:*\n{leave_request.status.title()}"},
        ]
    }
    blocks.append(details_section)

    if leave_request.reason:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Reason Provided:*\n{leave_request.reason}"}})
    
    # Add a context block for metadata
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"Request ID: #{leave_request.id} | Submitted: {leave_request.created_at.strftime('%b %d, %Y')}"}]})

    # --- 3. Add Final Status or Action Buttons ---
    if is_completed:
        status_emoji = "✅" if leave_request.status == "approved" else "❌"
        manager_name = leave_request.approver.name if leave_request.approver else "System"
        status_text = f"{status_emoji} *Action taken by {manager_name}*"
        blocks.extend([{"type": "divider"}, {"type": "section", "text": {"type": "mrkdwn", "text": status_text}}])
    else:
        action_buttons = {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "✅ Approve"}, "style": "primary", "action_id": "approve_leave", "value": str(leave_request.id)}, {"type": "button", "text": {"type": "plain_text", "text": "❌ Reject"}, "style": "danger", "action_id": "reject_leave", "value": str(leave_request.id)}, {"type": "button", "text": {"type": "plain_text", "text": "Who else is off?"}, "action_id": "view_overlapping_leave", "value": str(leave_request.id)}]}
        blocks.extend([action_buttons,{"type": "divider"}])
    
    return blocks

def get_selection_modal(pending_requests, action_type: str):
    """
    Creates a modal for selecting a pending leave request to update or cancel.

    Args:
        pending_requests: A queryset of the user's pending LeaveRequest objects.
        action_type (str): Either 'update' or 'cancel'.
    """
    title = "Update a Leave Request" if action_type == 'update' else "Cancel a Leave Request"
    submit_text = "Select" if action_type == 'update' else "Confirm Cancellation"
    callback_id = "select_leave_to_update" if action_type == 'update' else "cancel_leave_submission"
    
    # Create dropdown options from the pending requests
    options = []
    for req in pending_requests:
        duration = f"{req.start_date.strftime('%b %d')} to {req.end_date.strftime('%b %d')}"
        if req.start_date == req.end_date:
            duration = req.start_date.strftime('%b %d, %Y')
        
        options.append({
            "text": {
                "type": "plain_text",
                "text": f"{req.leave_type.name}: {duration}"
            },
            "value": str(req.id)
        })

    return {
        "type": "modal",
        "callback_id": callback_id,
        "title": {"type": "plain_text", "text": title},
        "submit": {"type": "plain_text", "text": submit_text},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Please choose the pending request you wish to modify."
                }
            },
            {
                "type": "input",
                "block_id": "request_selection_block",
                "element": {
                    "type": "static_select",
                    "action_id": "request_select_action",
                    "placeholder": {"type": "plain_text", "text": "Select a request"},
                    "options": options
                },
                "label": {"type": "plain_text", "text": "Pending Requests"}
            }
        ]
    }


def get_update_form_modal(leave_request: LeaveRequest):
    """
    Generates the Slack modal for updating a leave request, pre-filled with existing data.
    
    Args:
        leave_request: The LeaveRequest instance to be updated.
    """
    leave_types = LeaveType.objects.all()
    options = [
        {"text": {"type": "plain_text", "text": lt.name}, "value": lt.name}
        for lt in leave_types
    ]
    
    initial_leave_type_option = {
        "text": {"type": "plain_text", "text": leave_request.leave_type.name},
        "value": leave_request.leave_type.name
    }

    # Pass the leave request ID through the modal's private metadata
    private_metadata = json.dumps({"leave_request_id": leave_request.id})

    return {
        "type": "modal",
        "private_metadata": private_metadata,
        "callback_id": "leave_update_modal_submission", # New callback_id
        "title": {"type": "plain_text", "text": "Update Leave Request"},
        "submit": {"type": "plain_text", "text": "Submit Update"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            # Blocks are identical to get_leave_form_modal, but with initial values
            {
                "type": "section",
                "text": { "type": "mrkdwn", "text": f"*Updating Leave Request #{leave_request.id}*"}
            },
            {"type": "divider"},
            {
                "type": "input",
                "block_id": "start_date_block",
                "element": {
                    "type": "datepicker",
                    "action_id": "start_date_input",
                    "initial_date": leave_request.start_date.strftime('%Y-%m-%d')
                },
                "label": {"type": "plain_text", "text": "Start Date *"}
            },
            {
                "type": "input",
                "block_id": "end_date_block",
                "element": {
                    "type": "datepicker",
                    "action_id": "end_date_input",
                    "initial_date": leave_request.end_date.strftime('%Y-%m-%d')
                },
                "label": {"type": "plain_text", "text": "End Date *"}
            },
            {
                "type": "input",
                "block_id": "leave_type_block",
                "element": {
                    "type": "static_select",
                    "action_id": "leave_type_select",
                    "options": options,
                    "initial_option": initial_leave_type_option
                },
                "label": {"type": "plain_text", "text": "Leave Type *"}
            },
            {
                "type": "input",
                "block_id": "reason_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "reason_input",
                    "multiline": True,
                    "initial_value": leave_request.reason
                },
                "label": {"type": "plain_text", "text": "Reason"},
                "optional": False
            }
        ]
    }

# Add this new function to slack_blocks.py
def get_calendar_view_modal(leave_requests, month_date, title: str, viewer_employee_id=None, summary_info=None):

    """
    Generates a reusable, interactive modal with a monthly calendar view.
    Can optionally include a summary block for the employee view.

    Args:
        leave_requests: A queryset of LeaveRequest objects to display.
        month_date: A date object for the month to display.
        title (str): The title for the modal window.
        viewer_employee_id: The ID of the employee viewing the calendar, used to highlight their own leaves.
        summary_info (dict, optional): A dictionary with allowance data for the employee view.
                                       Example: {'allowance': 2, 'remaining': 1}
    """
    blocks = []

    # --- NEW: Conditionally add a summary block at the top ---
    if summary_info:
        summary_text = (
            f"*Your Leave Summary for {month_name[month_date.month]} {month_date.year}*\n"
            f"Monthly Allowance: *{summary_info['allowance']} days*\n"
            f"Remaining This Month: *{summary_info['remaining']} days*"
        )
        blocks.extend([
            {"type": "section", "text": {"type": "mrkdwn", "text": summary_text}},
            {"type": "divider"}
        ])

    # 1. Group leave requests by the day they occur on.
    leaves_by_day = {day: [] for day in range(1, 32)}
    for req in leave_requests:
        current_date = req.start_date
        while current_date <= req.end_date:
            if current_date.month == month_date.month:
                leaves_by_day[current_date.day].append(req)
            current_date += timedelta(days=1)

    # 2. Build the calendar string week by week.
    calendar_title = f"{title} for {month_name[month_date.month]} {month_date.year}"
    
    calendar_lines = [calendar_title, "=" * len(calendar_title)] # Add a title and underline
    
    first_day_of_month, num_days = monthrange(month_date.year, month_date.month)

    for day in range(1, num_days + 1):
        weekday = (first_day_of_month + day - 1) % 7
        day_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][weekday]
        
        # Create the date part and pad it to a fixed length
        date_part = f"{day_name} {day:02d}:"
        padded_date_part = date_part.ljust(10) # Pad with spaces to 10 characters
        
        # Create the status part
        status_part = ""
        if weekday < 5: # It's a weekday
            requests_for_day = leaves_by_day.get(day, [])
            if requests_for_day:
                day_entries = []
                for req in requests_for_day:
                    status_emoji = {'approved': '✅', 'rejected': '❌', 'pending': '⏳', 'cancelled': '🗑️'}.get(req.status, '')
                    name_display = req.employee.name
                    if viewer_employee_id and req.employee.id == viewer_employee_id:
                        name_display = f"{req.employee.name} (Your Leave)"
                    day_entries.append(f"{status_emoji} {name_display}")
                status_part = ", ".join(day_entries)
            else:
                status_part = "Available"
        else: # It's a weekend
            status_part = "(Weekend)"
        
        calendar_lines.append(f"{padded_date_part}{status_part}")

                # If the current day is a Sunday and it's not the last day of the month, add a blank line.
        if weekday == 6 and day < num_days:
            calendar_lines.append("")

    # Join all lines and wrap them in a code block
    calendar_body = "\n".join(calendar_lines)
    final_markdown = f"```{calendar_body}```"

    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": final_markdown}})
    
    # Navigation buttons remain the same
    prev_month = (month_date.replace(day=1) - timedelta(days=1)).strftime('%Y-%m-%d')
    next_month = (month_date.replace(day=28) + timedelta(days=4)).strftime('%Y-%m-01')

    navigation_buttons = {
        "type": "actions",
        "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "⬅️ Previous"}, "action_id": "navigate_calendar_prev", "value": prev_month},
            {"type": "button", "text": {"type": "plain_text", "text": "Next ➡️"}, "action_id": "navigate_calendar_next", "value": next_month}
        ]
    }
    blocks.extend([{"type": "divider"}, navigation_buttons])
    
    return {
        "type": "modal",
        "callback_id": "team_leave_calendar",
        "title": {"type": "plain_text", "text": title},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": blocks
    }

def get_employee_notification_blocks(leave_request: LeaveRequest):
    """
    Generates a clean, professional notification for the employee after
    their request has been actioned.
    """
    status = leave_request.status
    approver_name = leave_request.approver.name if leave_request.approver else "the system"

    if status == 'approved':
        header_text = "✅ Your Leave Request was Approved"
        context_text = f"Approved by *{approver_name}*."
    elif status == 'rejected':
        header_text = "❌ Your Leave Request was Rejected"
        context_text = f"Rejected by *{approver_name}*."
    else: # Cancelled
        header_text = "🗑️ Your Leave Request was Cancelled"
        context_text = "You have cancelled this request."

    duration_str = f"{leave_request.start_date.strftime('%b %d')} to {leave_request.end_date.strftime('%b %d')}"
    if leave_request.start_date == leave_request.end_date:
        duration_str = leave_request.start_date.strftime('%B %d, %Y')

    return [
        {"type": "header", "text": {"type": "plain_text", "text": header_text}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Leave Type:*\n{leave_request.leave_type.name}"},
                {"type": "mrkdwn", "text": f"*Dates:*\n{duration_str}"}
            ]
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": context_text}]}
    ]