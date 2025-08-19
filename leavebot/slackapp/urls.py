# leavebot/slackapp/urls.py

"""
URL Configuration for the Slack App Integration.

This module defines the URL patterns that map incoming webhooks from the Slack
API to the corresponding view functions in this Django application. These
endpoints are the primary entry points for all communication from Slack.
"""

from django.urls import path
from . import views

# Namespacing URLs is a Django best practice. It prevents name collisions with
# other apps in the project and allows for clear URL reversing.
# Example: reverse('slackapp:slash_command')
app_name = 'slackapp'

urlpatterns = [
    # This endpoint receives all slash command invocations from Slack.
    # When a user types a command like `/leave`, Slack sends a POST request here.
    path("commands/", views.slash_command, name="slash_command"),

    # This single endpoint handles all interactive events from Slack.
    # This includes button clicks, modal submissions, menu selections, etc.
    # Slack sends a POST request with a 'payload' parameter to this URL.
    path("interactions/", views.interactions, name="interactions"),
]