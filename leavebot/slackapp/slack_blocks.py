# leavebot/slackapp/slack_blocks.py
from datetime import date, timedelta
from .models import LeaveType, LeaveRequest # Import the new models

def get_leave_form_modal():
    """
    Generates the Slack modal for the leave request form.
    This function now dynamically populates the 'Leave Type' dropdown
    from the LeaveType model in your database.
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


def get_approval_message_blocks(leave_request: LeaveRequest, is_completed=False):
    """
    Generate Slack blocks for the leave approval message sent to managers.
    This function is updated to use the new relational models.

    Args:
        leave_request: The LeaveRequest model instance.
        is_completed: If True, shows the final status instead of action buttons.
    """
    # --- Access data through relationships ---
    # OLD: leave_request.user_name -> NEW: leave_request.employee.name
    # OLD: leave_request.leave_type -> NEW: leave_request.leave_type.name
    employee_name = leave_request.employee.name
    slack_user_id = leave_request.employee.slack_user_id
    leave_type_name = leave_request.leave_type.name
    duration = f"{leave_request.start_date}"
    if leave_request.end_date != leave_request.start_date:
        duration = f"{leave_request.start_date} to {leave_request.end_date}"
    
    duration_days = leave_request.duration_days
    day_text = "day" if duration_days == 1 else "days"
    
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*New Leave Request* (#{leave_request.id})\n\n"
                       f"*Employee:* <@{slack_user_id}> ({employee_name})\n"
                       f"*Dates:* {duration} (*{duration_days} {day_text}*)\n"
                       f"*Type:* {leave_type_name}\n"
                       f"*Submitted:* {leave_request.created_at.strftime('%b %d, %Y at %H:%M')}"
            }
        }
    ]
    
    if leave_request.reason:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Reason:* {leave_request.reason}"}
        })
    
    # --- Simplified logic for status vs. buttons ---
    if is_completed:
        # If the request is approved or rejected, show the final status
        status_emoji = "✅" if leave_request.status == "approved" else "❌"
        # The approver's name is on the leave_request object itself
        manager_name = leave_request.approver.name if leave_request.approver else "N/A"
        status_text = f"{status_emoji} *{leave_request.status.title()}* by {manager_name}"
        
        blocks.extend([
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": status_text}}
        ])
    else:
        # If the request is pending, show the action buttons
        blocks.extend([
            {"type": "divider"},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Approve"},
                        "style": "primary",
                        "action_id": "approve_leave",
                        "value": str(leave_request.id) # Pass the request ID
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ Reject"},
                        "style": "danger",
                        "action_id": "reject_leave",
                        "value": str(leave_request.id) # Pass the request ID
                    }
                ]
            }
        ])
    
    return blocks