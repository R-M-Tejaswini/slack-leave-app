# leavebot/slackapp/admin.py
"""
Admin panel configuration for the slackapp.

This file defines how the models are displayed and managed in the Django
admin interface. Using ModelAdmin classes allows for rich customization
of the admin experience.
"""
from django.contrib import admin
from .models import Employee, LeaveType, LeaveRequest, LeaveRequestAudit, Holiday, Team

@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    """Admin configuration for the Team model."""
    list_display = ('name', 'slack_channel_id')
    search_fields = ('name',)

@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('name', 'slack_user_id', 'team', 'manager', 'monthly_leave_allowance')
    search_fields = ('name', 'slack_user_id', 'email')
    list_filter = ('team', 'manager',)

# ... (The rest of your admin.py file remains the same) ...
@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ('name', 'date')
    list_filter = ('date',)
    search_fields = ('name',)

@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'employee', 'leave_type', 'start_date', 'end_date', 'status', 'approver')
    list_filter = ('status', 'leave_type', 'start_date')
    search_fields = ('employee__name', 'employee__slack_user_id')
    readonly_fields = ('created_at', 'updated_at')

@admin.register(LeaveRequestAudit)
class LeaveRequestAuditAdmin(admin.ModelAdmin):
    list_display = ('leave_request', 'action', 'performed_by', 'timestamp')
    list_filter = ('action', 'timestamp')
    search_fields = ('leave_request__employee__name',)
    readonly_fields = ('timestamp',)