# leavebot/leavebot/urls.py
"""
Main URL configuration for the leavebot project.

This file routes incoming URL requests to the appropriate Django app.
All URLs related to the Slack bot functionality are namespaced under `/slack/`
and handled by the `slackapp`.
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    # The Django admin site
    path('admin/', admin.site.urls),
    
    # All Slack-related endpoints (commands, interactions) are routed to the slackapp
    path('slack/', include('slackapp.urls')),
]