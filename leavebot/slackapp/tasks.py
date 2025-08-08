# leavebot/slackapp/tasks.py
from celery import shared_task
from slack_sdk import WebClient
import os
import logging

from .models import LeaveRequest

# Initialize the Slack client and logger
slack_client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
logger = logging.getLogger("slackapp")

@shared_task
def send_manager_reminder(leave_request_id: int):
    """
    A Celery task to send a reminder to a manager if a leave request
    is still pending after a specified delay.
    """
    try:
        leave_request = LeaveRequest.objects.get(id=leave_request_id)

        # Only send a reminder if the request is still 'pending'.
        if leave_request.status == 'pending' and leave_request.employee.manager:
            manager_id = leave_request.employee.manager.slack_user_id
            employee_name = leave_request.employee.name

            message = (
                f"Hi there! This is a friendly reminder that a leave request from "
                f"*{employee_name}* (submitted on {leave_request.created_at.strftime('%b %d')}) "
                f"is still awaiting your approval."
            )

            slack_client.chat_postMessage(channel=manager_id, text=message)
            logger.info(f"Sent reminder for leave request #{leave_request.id} to manager {manager_id}")

    except LeaveRequest.DoesNotExist:
        logger.info(f"Leave request with ID {leave_request_id} was actioned or deleted. No reminder sent.")
    except Exception as e:
        logger.error(f"Error in send_manager_reminder task for request #{leave_request_id}: {e}")