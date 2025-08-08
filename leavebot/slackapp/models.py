# leavebot/slackapp/models.py
from django.db import models
from django.utils import timezone
from datetime import datetime, timedelta

# ==============================================================================
# 1. Employee, LeaveType, and NEW Holiday Models
# ==============================================================================

class Employee(models.Model):
    """
    Stores Slack user information and manager relationships. This is the central
    source of truth for all users interacting with the app.
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
    
    def __str__(self):
        return self.name

# --- NEW MODEL FOR COMPANY HOLIDAYS ---
class Holiday(models.Model):
    """
    Stores company-wide holidays to exclude them from leave calculations.
    """
    name = models.CharField(max_length=100)
    date = models.DateField(unique=True)

    def __str__(self):
        return f"{self.name} ({self.date})"

# ==============================================================================
# 2. The New and Improved LeaveRequest Model
# ==============================================================================

class LeaveRequest(models.Model):
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
        --- UPDATED LOGIC ---
        Calculate the number of business days (Mon-Fri) for the leave request,
        excluding any company holidays.
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

# ==============================================================================
# 3. Audit Model (Advanced, but good for tracking changes)
# ==============================================================================
class LeaveRequestAudit(models.Model):
    leave_request = models.ForeignKey(LeaveRequest, on_delete=models.CASCADE, related_name='audit_trail')
    action = models.CharField(max_length=50, help_text="Action performed (e.g., 'created', 'approved')")
    performed_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.leave_request.id}: {self.action} by {self.performed_by.name if self.performed_by else 'System'}"