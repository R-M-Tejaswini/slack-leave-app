# leavebot/slackapp/tasks.py

"""
Asynchronous Background Tasks for the Slack App.

This module defines Celery tasks that are executed in the background by a
Celery worker, separate from the main web process. This is ideal for handling
long-running or periodic operations that shouldn't block the main application,
such as sending notifications or reminders.

Tasks defined here are automatically discovered by the Celery instance defined
in `leavebot/celery.py`.
"""

# Standard library imports
import logging
import os

# Third-party imports
from celery import shared_task
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Local application imports
from .models import LeaveRequest

# --- Initialization ---
# It's good practice to initialize clients and loggers once at the module level.
SLACK_CLIENT = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
LOGGER = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_manager_reminder(self, leave_request_id: int):
    """
    A Celery task to send a reminder DM to a manager if a leave request
    is still pending after a specified delay (e.g., 24 hours).

    This task is designed to be fault-tolerant. It will retry up to 3 times
    if a transient error (like a network issue) occurs.

    Args:
        self: The Celery task instance (automatically passed with `bind=True`).
        leave_request_id: The primary key of the `LeaveRequest` to check.
    """
    try:
        # Fetch the leave request from the database.
        leave_request = LeaveRequest.objects.get(id=leave_request_id)

        # --- Core Logic: Send reminder only if still pending ---
        # This check is crucial to prevent sending reminders for requests that have
        # already been approved, rejected, or cancelled by the time the task runs.
        if (leave_request.status == LeaveRequest.STATUS_PENDING 
                and leave_request.employee.manager):
            
            manager_id = leave_request.employee.manager.slack_user_id
            employee_name = leave_request.employee.name

            message = (
                f"Hi there! This is a friendly reminder that a leave request from "
                f"*{employee_name}* (submitted on {leave_request.created_at.strftime('%b %d')}) "
                f"is still awaiting your approval."
            )

            SLACK_CLIENT.chat_postMessage(channel=manager_id, text=message)
            LOGGER.info(f"Successfully sent reminder for LR#{leave_request.id} to manager {manager_id}")
        
        else:
            LOGGER.info(f"No reminder sent for LR#{leave_request_id}. Status was '{leave_request.status}' or no manager was found.")

    except LeaveRequest.DoesNotExist:
        # This is not an error. It's an expected outcome if the request was
        # deleted before the task ran. We log it for informational purposes.
        LOGGER.info(f"LeaveRequest with ID {leave_request_id} no longer exists. No reminder sent.")
    
    except SlackApiError as e:
        # If the Slack API fails (e.g., channel not found, token invalid),
        # we log the specific Slack error and retry the task.
        LOGGER.error(f"Slack API error for LR#{leave_request_id}: {e.response['error']}. Retrying task.")
        raise self.retry(exc=e)

    except Exception as e:
        # For any other unexpected errors, log with the full traceback and retry.
        # `logger.exception` is preferred over `logger.error` here as it includes the stack trace.
        LOGGER.exception(f"Unexpected error in send_manager_reminder for LR#{leave_request_id}: {e}")
        raise self.retry(exc=e)