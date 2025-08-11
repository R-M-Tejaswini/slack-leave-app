# leavebot/slackapp/slack_blocks.py
from datetime import date, timedelta
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

    # --- 1. Handle Cancelled State First ---
    # If the request is cancelled, we show a simple message and stop.
    if is_cancelled:
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Leave request #{leave_request.id} for <@{slack_user_id}> was *cancelled* by the employee."
                }
            }
        ]

    # --- 2. Determine the Title ---
    # This combines your original logic with the new 'updated' state.
    leave_type_name = leave_request.leave_type.name
    is_unplanned = leave_type_name in ["Unplanned", "Emergency"]
    
    # Set the base title based on whether it's planned or retrospective
    base_title = "Retrospective Leave Submission" if is_unplanned else "New Leave Request"
    
    # Add the '(Updated)' tag if needed, then wrap in markdown
    title_text = f"*{base_title} (Updated)*" if is_updated else f"*{base_title}*"


    # --- 3. Build the Message Details ---
    # This section is from your original code.
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
                "text": f"{title_text} (#{leave_request.id})\n\n"
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
    
    # --- 4. Add Final Status or Action Buttons ---
    # This section is also from your original code.
    if is_completed:
        status_emoji = "✅" if leave_request.status == "approved" else "❌"
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
                        "value": str(leave_request.id)
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ Reject"},
                        "style": "danger",
                        "action_id": "reject_leave",
                        "value": str(leave_request.id)
                    },
                    {
                        "type": "button", 
                        "text": {"type": "plain_text", "text": "Who else is off?"}, 
                        "action_id": "view_overlapping_leave", 
                        "value": str(leave_request.id)
                    }
                ]
            }
        ])
    
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