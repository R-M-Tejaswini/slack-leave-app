# leavebot/slackapp/models.py
from django.db import models
from django.utils import timezone
from datetime import datetime, timedelta

# ==============================================================================
# CORE DATA MODELS
# ==============================================================================

class Team(models.Model):
    """Represents a team within the organization, with a dedicated Slack channel."""
    name = models.CharField(max_length=100, unique=True)
    slack_channel_id = models.CharField(max_length=50, help_text="The Slack Channel ID for this team's announcements.")

    def __str__(self):
        return self.name

class Employee(models.Model):
    """
    Represents an employee in the system, linking their Slack identity to their
    role within the organization's hierarchy.
    """
    slack_user_id = models.CharField(max_length=50, unique=True, help_text="Employee's unique Slack user ID")
    name = models.CharField(max_length=100, help_text="Employee's full name")
    email = models.EmailField(unique=True, help_text="Employee's email address")
    manager = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='direct_reports',
        help_text="The direct manager of this employee"
    )
    team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True)
    monthly_leave_allowance = models.PositiveSmallIntegerField(default=2, help_text="Number of leave days allowed per month")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.slack_user_id})"


class LeaveType(models.Model):
    """
    Defines a category of leave (e.g., Vacation, Sick Leave). Storing this in the
    database allows for easy management without code changes.
    """
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    
    def __str__(self):
        return self.name

class Holiday(models.Model):
    """
    Stores official company-wide holidays. These dates are excluded from
    leave duration calculations.
    """
    name = models.CharField(max_length=100)
    date = models.DateField(unique=True)

    def __str__(self):
        return f"{self.name} ({self.date})"

# ==============================================================================
# TRANSACTIONAL MODELS
# ==============================================================================

class LeaveRequest(models.Model):
    """
    Represents a single leave request submitted by an employee. This is the
    central transactional model of the application.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='leave_requests')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT)
    start_date = models.DateField(help_text="Leave start date")
    end_date = models.DateField(help_text="Leave end date")
    reason = models.TextField(help_text="Reason for leave")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    approver = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_requests')
    approval_notes = models.TextField(blank=True, help_text="Manager's notes on the decision")
    
    # Slack-specific fields to track the approval message
    slack_message_ts = models.CharField(max_length=50, blank=True, help_text="Slack message timestamp")
    slack_channel_id = models.CharField(max_length=50, blank=True, help_text="Slack channel ID where the approval message was sent")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Request for {self.employee.name} ({self.status})"

    @property
    def duration_days(self):
        """
        Calculates the number of business days (Mon-Fri) for the leave request,
        intelligently excluding any company holidays that fall within the range.
        """
        if not self.end_date:
            return 1
        
        # Get all holidays within the requested date range for efficient lookup
        holidays = set(Holiday.objects.filter(date__range=[self.start_date, self.end_date]).values_list('date', flat=True))
        
        business_days = 0
        current_date = self.start_date
        while current_date <= self.end_date:
            # Check if the day is a weekday (0-4) and not in our holiday list
            if current_date.weekday() < 5 and current_date not in holidays:
                business_days += 1
            current_date += timedelta(days=1)
            
        return business_days if business_days > 0 else 1

class LeaveRequestAudit(models.Model):
    """
    Creates a non-editable audit trail for every significant action taken on a
    leave request, ensuring accountability and history tracking.
    """
    leave_request = models.ForeignKey(LeaveRequest, on_delete=models.CASCADE, related_name='audit_trail')
    action = models.CharField(max_length=50, help_text="Action performed (e.g., 'created', 'approved')")
    performed_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"Audit for Request #{self.leave_request.id}: {self.action}"