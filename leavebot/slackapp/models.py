# leavebot/slackapp/models.py
from django.db import models
from django.utils import timezone
from datetime import datetime

# ==============================================================================
# 1. Employee and Leave Type Models (The Foundation)
# ==============================================================================

class Employee(models.Model):
    """
    Stores Slack user information and manager relationships. This is the central
    source of truth for all users interacting with the app.
    """
    slack_user_id = models.CharField(max_length=50, unique=True, help_text="Employee's unique Slack user ID")
    name = models.CharField(max_length=100, help_text="Employee's full name")
    email = models.EmailField(unique=True, help_text="Employee's email address")

    # This is the key to your org chart. It links an employee to another employee (their manager).
    manager = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='direct_reports',
        help_text="The direct manager of this employee"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.slack_user_id})"

class LeaveType(models.Model):
    """
    Creates a manageable list of leave types in the database, instead of
    hardcoding them in the model. This is much more flexible.
    """
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    
    # You could add other rules here, like max days allowed, etc.
    
    def __str__(self):
        return self.name

# ==============================================================================
# 2. The New and Improved LeaveRequest Model
# ==============================================================================

class LeaveRequest(models.Model):
    """
    Tracks all leave applications. This model links everything together.
    It replaces your old LeaveRequest model entirely.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    # Links to the Employee model, replacing the old user_id and user_name fields.
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='leave_requests'
    )

    # Links to the LeaveType model, replacing the old hardcoded choices.
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT)
    start_date = models.DateField(help_text="Leave start date")
    end_date = models.DateField(help_text="Leave end date")
    reason = models.TextField(help_text="Reason for leave")

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='pending'
    )
    
    # Stores the manager who took action, linking back to the Employee model.
    approver = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_requests'
    )
    approval_notes = models.TextField(blank=True, help_text="Manager's notes on the decision")

    # Renamed fields for clarity
    slack_message_ts = models.CharField(max_length=50, blank=True, help_text="Slack message timestamp")

    slack_channel_id = models.CharField(max_length=50, blank=True, help_text="Slack channel ID where the approval message was sent")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Request for {self.employee.name} ({self.status})"

    @property
    def duration_days(self):
        """Calculate the number of days for the leave request."""
        return (self.end_date - self.start_date).days + 1

# ==============================================================================
# 3. Audit Model (Advanced, but good for tracking changes)
# ==============================================================================

class LeaveRequestAudit(models.Model):
    """
    Creates a log of every important action taken on a leave request.
    This is great for history and accountability.
    """
    leave_request = models.ForeignKey(
        LeaveRequest,
        on_delete=models.CASCADE,
        related_name='audit_trail'
    )
    action = models.CharField(max_length=50, help_text="Action performed (e.g., 'created', 'approved')")
    performed_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.leave_request.id}: {self.action} by {self.performed_by.name if self.performed_by else 'System'}"