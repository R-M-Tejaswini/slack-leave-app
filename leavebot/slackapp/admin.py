# leavebot/slackapp/admin.py
"""
Admin panel configuration for the slackapp.

This file defines how the models are displayed and managed in the Django
admin interface. Using ModelAdmin classes allows for rich customization
of the admin experience.
"""
from django.contrib import admin
from .models import Employee, LeaveType, LeaveRequest, LeaveRequestAudit, Holiday

# Register your models here so you can see them in the admin panel
admin.site.register(Employee)
admin.site.register(LeaveType)
admin.site.register(LeaveRequest)
admin.site.register(LeaveRequestAudit)
admin.site.register(Holiday) 