# leavebot/slackapp/admin.py
from django.contrib import admin
from .models import Employee, LeaveType, LeaveRequest, LeaveRequestAudit, Holiday

# Register your models here so you can see them in the admin panel
admin.site.register(Employee)
admin.site.register(LeaveType)
admin.site.register(LeaveRequest)
admin.site.register(LeaveRequestAudit)
admin.site.register(Holiday) 