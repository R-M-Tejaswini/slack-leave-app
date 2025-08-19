# leavebot/slackapp/models.py

"""
Database Models for the Slack Leave Management Application.

This module defines the data architecture for the application, including core
entities like Employees and Teams, and transactional records like Leave Requests
and their associated Audit Trails. The models are designed to be robust and
maintainable, providing a solid foundation for the app's business logic.
"""

# Standard library imports
from datetime import timedelta

# Django imports
from django.db import models
from django.utils import timezone


# ==============================================================================
# CORE DATA MODELS
# ==============================================================================

class Team(models.Model):
    """
    Represents a team or department within the organization.

    Each team can have a dedicated Slack channel for leave-related announcements,
    linking the organizational structure directly to communication workflows.

    Attributes:
        name (str): The unique name of the team (e.g., "Engineering", "Marketing").
        slack_channel_id (str): The ID of the Slack channel for team announcements.
    """
    name = models.CharField(max_length=100, unique=True)
    slack_channel_id = models.CharField(
        max_length=50,
        help_text="The Slack Channel ID for this team's announcements."
    )

    class Meta:
        verbose_name = "Team"
        verbose_name_plural = "Teams"

    def __str__(self) -> str:
        """Returns the string representation of the Team, which is its name."""
        return self.name


class Employee(models.Model):
    """
    Represents an employee, linking their Slack identity to their organizational role.

    This model stores essential user information and defines hierarchical
    relationships (manager/direct reports) and team associations.

    Attributes:
        slack_user_id (str): The unique ID provided by Slack for the user.
        name (str): The employee's full name.
        email (str): The employee's unique work email address.
        manager (Employee): A self-referential foreign key to another Employee
                           who is their direct manager.
        team (Team): The team to which the employee belongs.
        monthly_leave_allowance (int): The number of leave days allocated per month.
        created_at (datetime): Timestamp of when the employee record was created.
        updated_at (datetime): Timestamp of the last update to the record.
    """
    slack_user_id = models.CharField(max_length=50, unique=True, help_text="Employee's unique Slack user ID")
    name = models.CharField(max_length=100, help_text="Employee's full name")
    email = models.EmailField(unique=True, help_text="Employee's email address")
    manager = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,  # If a manager leaves, their reports are not deleted.
        null=True,
        blank=True,
        related_name='direct_reports',
        help_text="The direct manager of this employee"
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,  # If a team is deleted, employees are not deleted.
        null=True,
        blank=True
    )
    monthly_leave_allowance = models.PositiveSmallIntegerField(
        default=2,
        help_text="Number of leave days allowed per month"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Employee"
        verbose_name_plural = "Employees"
        ordering = ['name']

    def __str__(self) -> str:
        """Returns a user-friendly string representation of the Employee."""
        return f"{self.name} ({self.slack_user_id})"


class LeaveType(models.Model):
    """
    Defines a category of leave (e.g., Vacation, Sick Leave, Personal).

    Storing leave types in the database allows administrators to add or modify
    them easily through the Django admin interface without requiring code changes.

    Attributes:
        name (str): The unique name of the leave category.
        description (str): An optional detailed description of the leave type.
    """
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    
    class Meta:
        verbose_name = "Leave Type"
        verbose_name_plural = "Leave Types"

    def __str__(self) -> str:
        """Returns the name of the leave type."""
        return self.name


class Holiday(models.Model):
    """
    Stores official company-wide holidays.

    These dates are automatically excluded from leave duration calculations to ensure
    employees are not charged leave days for public holidays.

    Attributes:
        name (str): The official name of the holiday (e.g., "New Year's Day").
        date (date): The specific date of the holiday.
    """
    name = models.CharField(max_length=100)
    date = models.DateField(unique=True)

    class Meta:
        verbose_name = "Holiday"
        verbose_name_plural = "Holidays"
        ordering = ['date']

    def __str__(self) -> str:
        """Returns a string representation including the holiday name and date."""
        return f"{self.name} ({self.date.strftime('%Y-%m-%d')})"


# ==============================================================================
# TRANSACTIONAL MODELS
# ==============================================================================

class LeaveRequest(models.Model):
    """
    Represents a single leave request submitted by an employee.

    This is the central transactional model of the application, tracking the
    entire lifecycle of a leave request from submission to final decision.
    """
    # --- Status Choices ---
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CANCELLED = 'cancelled'
    
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
        (STATUS_CANCELLED, 'Cancelled'),
    ]

    # --- Core Fields ---
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='leave_requests')
    leave_type = models.ForeignKey(
        LeaveType,
        on_delete=models.PROTECT, # Prevents deleting a LeaveType if it's in use.
        help_text="The category of leave being requested."
    )
    start_date = models.DateField(help_text="The first day of leave.")
    end_date = models.DateField(help_text="The last day of leave.")
    reason = models.TextField(help_text="A brief reason for the leave.")

    # --- Approval Workflow Fields ---
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    approver = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL, # Keep the request record even if the approver's account is deleted.
        null=True,
        blank=True,
        related_name='approved_requests'
    )
    approval_notes = models.TextField(blank=True, help_text="Optional notes from the manager regarding the decision.")
    
    # --- Slack Integration Fields ---
    # These fields link the request to the specific Slack message sent to the manager for approval.
    slack_message_ts = models.CharField(max_length=50, blank=True, help_text="The timestamp ID of the Slack approval message.")
    slack_channel_id = models.CharField(max_length=50, blank=True, help_text="The channel ID where the approval message was sent.")
    
    # --- Timestamps ---
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Leave Request"
        verbose_name_plural = "Leave Requests"
        ordering = ['-start_date']

    def __str__(self) -> str:
        """Returns a summary of the leave request."""
        return f"Request by {self.employee.name} from {self.start_date} to {self.end_date} ({self.status})"

    @property
    def duration_days(self) -> int:
        """
        Calculates the number of business days for the leave request.

        This calculation excludes weekends (Saturday, Sunday) and any official
        company holidays that fall within the leave period.

        Returns:
            int: The total number of working days requested.
        """
        # Fetch all holidays within the requested date range for efficient lookup.
        holidays = set(Holiday.objects.filter(
            date__range=[self.start_date, self.end_date]
        ).values_list('date', flat=True))

        business_days = 0
        

        current_date = self.start_date
        while current_date <= self.end_date:
            is_weekday = current_date.weekday() < 5  # Monday is 0, Sunday is 6
            is_not_holiday = current_date not in holidays

            if is_weekday and is_not_holiday:
                business_days += 1
            
            current_date += timedelta(days=1)
            
        # A request must be for at least one day, even if it falls on a holiday.
        # This handles cases where a user might request a single day off that
        # happens to be a public holiday (e.g., for travel).
        return business_days if business_days > 0 else 1

class LeaveRequestAudit(models.Model):
    """
    Creates an immutable audit trail for actions on a LeaveRequest.

    This model logs every significant event in a leave request's lifecycle
    (e.g., creation, approval, rejection), providing a clear history for
    accountability and record-keeping.

    Attributes:
        leave_request (LeaveRequest): The associated leave request.
        action (str): The action performed (e.g., 'created', 'approved').
        performed_by (Employee): The user who performed the action.
        timestamp (datetime): When the action was performed.
        details (str): Optional notes or context about the action.
    """
    leave_request = models.ForeignKey(LeaveRequest, on_delete=models.CASCADE, related_name='audit_trail')
    action = models.CharField(max_length=50, help_text="Action performed (e.g., 'created', 'approved', 'rejected')")
    performed_by = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True)
    details = models.TextField(blank=True, help_text="System-generated or user-provided notes for this audit entry.")
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Leave Request Audit"
        verbose_name_plural = "Leave Request Audits"
        ordering = ['-timestamp']

    def __str__(self) -> str:
        """Returns a summary of the audit entry."""
        actor = self.performed_by.name if self.performed_by else "System"
        return f"Request #{self.leave_request.id}: '{self.action}' by {actor} at {self.timestamp.strftime('%Y-%m-%d %H:%M')}"