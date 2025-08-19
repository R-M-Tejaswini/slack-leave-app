# leavebot/slackapp/admin.py


"""
Admin Panel Configuration for the Slack Leave Management App.

This module configures the Django admin interface for the `slackapp` models.
It defines custom `ModelAdmin` classes to enhance the display, filtering, 
and functionality of models like Employee, LeaveRequest, and Team.

A key feature is a custom analytics dashboard integrated into the LeaveRequest 
admin page, providing visualizations and statistics on leave patterns, team 
coverage, and approval metrics using Matplotlib.
"""

# Standard library imports
import base64
from io import BytesIO
from datetime import date, datetime, timedelta
from typing import Optional # <-- ADD THIS IMPORT

# Django imports
from django.contrib import admin
from django.urls import path
from django.shortcuts import render
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth

# Third-party library imports
import matplotlib
matplotlib.use('Agg')  # Use 'Agg' backend for non-interactive plotting
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
import numpy as np

# Local application imports
from .models import Employee, LeaveType, LeaveRequest, LeaveRequestAudit, Holiday, Team

# --- Matplotlib and Seaborn Styling ---
# Apply a professional and consistent visual style to all generated plots.
plt.style.use('seaborn-v0_8-whitegrid')
sns.set_palette("husl")


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Team model.
    
    Displays team information along with calculated fields for team size
    and the number of members currently on leave.
    """
    list_display = ('name', 'slack_channel_id', 'employee_count', 'current_on_leave')
    search_fields = ('name',)
    
    def employee_count(self, obj: Team) -> int:
        """Calculates the total number of employees in the team."""
        return obj.employee_set.count()
    employee_count.short_description = 'Team Size'
    
    def current_on_leave(self, obj: Team) -> int:
        """Counts employees in the team who are on an approved leave today."""
        today = date.today()
        return LeaveRequest.objects.filter(
            employee__team=obj,
            status='approved',
            start_date__lte=today,
            end_date__gte=today
        ).count()
    current_on_leave.short_description = 'Currently on Leave'


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Employee model.
    
    Provides a list view with key employee details and a calculated
    remaining leave balance for the current month.
    """
    list_display = ('name', 'slack_user_id', 'team', 'manager', 'monthly_leave_allowance', 'leave_balance')
    search_fields = ('name', 'slack_user_id', 'email')
    list_filter = ('team', 'manager',)
    
    def leave_balance(self, obj: Employee) -> float:
        """
        Calculates the employee's remaining leave balance for the current calendar month.
        
        Args:
            obj: The Employee instance.
        
        Returns:
            The number of remaining leave days.
        """
        today = date.today()
        month_start = date(today.year, today.month, 1)
        
        # Sum the duration of all approved leaves starting this month.
        current_month_requests = LeaveRequest.objects.filter(
            employee=obj,
            status='approved',
            start_date__gte=month_start,
            start_date__lte=today
        )
        used_days = sum(request.duration_days for request in current_month_requests)
        
        return max(0, obj.monthly_leave_allowance - used_days)
    leave_balance.short_description = 'Remaining Days (This Month)'


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    """
    Admin configuration for the LeaveType model.
    
    Displays leave types along with usage statistics, such as the total
    number of requests and the average duration.
    """
    list_display = ('name', 'description', 'usage_count', 'avg_duration')
    search_fields = ('name',)
    
    def usage_count(self, obj: LeaveType) -> int:
        """Counts how many times this leave type has been requested."""
        return obj.leaverequest_set.count()
    usage_count.short_description = 'Total Requests'
    
    def avg_duration(self, obj: LeaveType) -> str:
        """Calculates the average duration for this leave type."""
        requests = obj.leaverequest_set.all()
        if requests:
            total_days = sum(request.duration_days for request in requests)
            avg = total_days / len(requests)
            return f"{avg:.1f} days"
        return "N/A"
    avg_duration.short_description = 'Avg Duration'


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    """
    Admin configuration for the Holiday model.
    
    Allows for easy management of public holidays and indicates whether
    a holiday is upcoming.
    """
    list_display = ('name', 'date', 'is_upcoming')
    list_filter = ('date',)
    search_fields = ('name',)
    
    def is_upcoming(self, obj: Holiday) -> bool:
        """Returns True if the holiday date is in the future."""
        return obj.date > date.today()
    is_upcoming.boolean = True
    is_upcoming.short_description = 'Upcoming'


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    """
    Admin configuration for the LeaveRequest model.
    
    This is the main hub for leave management, featuring a detailed list view,
    filters, and a custom analytics dashboard accessible via an "Analytics" button.
    """
    list_display = ('id', 'employee', 'leave_type', 'start_date', 'end_date', 'duration_days', 'status', 'approver')
    list_filter = ('status', 'leave_type', 'start_date', 'employee__team')
    search_fields = ('employee__name', 'employee__slack_user_id')
    readonly_fields = ('created_at', 'updated_at', 'duration_days')
    
    # Use a custom template to add an "Analytics" button to the changelist view.
    change_list_template = "admin/leave_request_changelist.html"

    def get_urls(self):
        """
        Overrides the default admin URLs to add a custom path for the analytics dashboard.
        
        This makes the view available at `/admin/slackapp/leaverequest/analytics/`.
        """
        urls = super().get_urls()
        custom_urls = [
            path('analytics/', self.admin_site.admin_view(self.analytics_view), name='leave_analytics'),
        ]
        return custom_urls + urls

    def analytics_view(self, request):
        """
        Renders the leave management analytics dashboard.
        
        This view aggregates data and generates several plots to provide insights
        into leave trends, team capacity, and operational efficiency.
        
        Args:
            request: The HttpRequest object.
            
        Returns:
            An HttpResponse object rendering the dashboard template with chart data.
        """
        context = {
            'title': 'Leave Management Analytics Dashboard',
            'chart_team_coverage': self.get_team_coverage_chart(),
            'chart_monthly_trends': self.get_monthly_trends_chart(),
            'chart_leave_patterns': self.get_leave_patterns_heatmap(),
            'chart_approval_metrics': self.get_approval_metrics_chart(),
            'chart_utilization_analysis': self.get_utilization_analysis_chart(),
            'chart_team_workload': self.get_team_workload_impact_chart(),
            'summary_stats': self.get_summary_statistics(),
        }
        return render(request, 'admin/leave_analytics_dashboard.html', context)

    def get_summary_statistics(self) -> dict:
        """
        Calculates key summary statistics for the dashboard's header.
        
        Returns:
            A dictionary containing key metrics like pending requests, average
            approval time, and total active employees.
        """
        today = date.today()
        current_month = date(today.year, today.month, 1)
        
        # --- Calculate Key Metrics ---
        current_month_requests = LeaveRequest.objects.filter(start_date__gte=current_month)
        pending_requests = current_month_requests.filter(status='pending').count()
        approved_requests = current_month_requests.filter(status='approved').count()
        
        current_on_leave = LeaveRequest.objects.filter(
            status='approved',
            start_date__lte=today,
            end_date__gte=today
        ).count()
        
        # Calculate average approval time in hours
        approved_with_audit = LeaveRequest.objects.filter(
            status='approved',
            audit_trail__action='approved'
        ).select_related()
        
        approval_times = []
        for request in approved_with_audit:
            created_audit = request.audit_trail.filter(action='created').first()
            approved_audit = request.audit_trail.filter(action='approved').first()
            if created_audit and approved_audit:
                time_diff = approved_audit.timestamp - created_audit.timestamp
                approval_times.append(time_diff.total_seconds() / 3600)  # Convert to hours
        
        avg_approval_time = sum(approval_times) / len(approval_times) if approval_times else 0
        
        return {
            'pending_requests': pending_requests,
            'approved_this_month': approved_requests,
            'currently_on_leave': current_on_leave,
            'avg_approval_hours': round(avg_approval_time, 1),
            'total_employees': Employee.objects.count(),
            'active_teams': Team.objects.count(),
        }

    def get_team_coverage_chart(self) -> Optional[str]: # <-- FIXED
        """
        Generates a chart analyzing team coverage risk.
        
        The chart has two subplots:
        1. A horizontal bar chart showing coverage risk percentage for each team.
        2. A stacked bar chart showing total team size vs. members on leave.
        
        Returns:
            A base64 encoded string of the plot PNG, or None if no data.
        """
        today = date.today()
        teams = Team.objects.all()
        
        team_data = []
        for team in teams:
            total_employees = team.employee_set.count()
            if total_employees == 0:
                continue
            
            on_leave_today = LeaveRequest.objects.filter(
                employee__team=team, status='approved', start_date__lte=today, end_date__gte=today
            ).count()
            
            upcoming_leave = LeaveRequest.objects.filter(
                employee__team=team, status='approved', start_date__gt=today, start_date__lte=today + timedelta(days=7)
            ).count()
            
            # Risk is defined as the percentage of the team unavailable now or in the next week.
            coverage_risk = (on_leave_today + upcoming_leave) / total_employees * 100
            team_data.append((team.name, total_employees, on_leave_today, upcoming_leave, coverage_risk))
        
        if not team_data:
            return None
            
        team_data.sort(key=lambda x: x[4], reverse=True)  # Sort by highest risk
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
        
        # --- Subplot 1: Coverage Risk Bar Chart ---
        team_names = [x[0] for x in team_data]
        risks = [x[4] for x in team_data]
        colors = ['red' if r > 30 else 'orange' if r > 15 else 'green' for r in risks]
        
        bars1 = ax1.barh(team_names, risks, color=colors, alpha=0.7)
        ax1.set_xlabel('Coverage Risk (%)')
        ax1.set_title('Team Coverage Risk (Today + Next 7 Days)')
        ax1.axvline(x=15, color='orange', linestyle='--', alpha=0.5, label='Warning (15%)')
        ax1.axvline(x=30, color='red', linestyle='--', alpha=0.5, label='Critical (30%)')
        ax1.legend()
        
        # Add value labels to bars
        for bar, risk in zip(bars1, risks):
            ax1.text(bar.get_width() + 1, bar.get_y() + bar.get_height()/2, 
                     f'{risk:.1f}%', ha='left', va='center')
        
        # --- Subplot 2: Team Capacity Stacked Bar Chart ---
        team_sizes = [x[1] for x in team_data]
        on_leaves = [x[2] for x in team_data]
        upcomings = [x[3] for x in team_data]
        
        x_indices = np.arange(len(team_names))
        
        ax2.bar(x_indices, team_sizes, label='Total Employees', alpha=0.8)
        ax2.bar(x_indices, on_leaves, label='Currently on Leave', alpha=0.8)
        ax2.bar(x_indices, upcomings, bottom=on_leaves, label='Upcoming Leave', alpha=0.8)
        
        ax2.set_ylabel('Number of Employees')
        ax2.set_title('Team Capacity Overview')
        ax2.set_xticks(x_indices)
        ax2.set_xticklabels(team_names, rotation=45, ha='right')
        ax2.legend()
        
        plt.tight_layout()
        return self._save_plot_to_base64()

    def get_monthly_trends_chart(self) -> Optional[str]: # <-- FIXED
        """
        Generates a dual-axis line chart showing leave trends over the past 12 months.
        
        - Top plot shows the number of leave requests per month.
        - Bottom plot shows the total number of leave days taken per month.
        
        Returns:
            A base64 encoded string of the plot PNG, or None if no data.
        """
        end_date = date.today()
        start_date = end_date - timedelta(days=365)
        
        monthly_data = LeaveRequest.objects.filter(
            start_date__range=[start_date, end_date], status='approved'
        ).annotate(
            month=TruncMonth('start_date')
        ).values('month').annotate(
            requests=Count('id')
        ).order_by('month')
        
        if not monthly_data:
            return None
        
        # Aggregate total days per month in Python for simplicity
        monthly_stats = {}
        for item in monthly_data:
            month_key = item['month'].strftime('%Y-%m')
            monthly_stats[month_key] = {'requests': item['requests'], 'days': 0}
        
        all_requests = LeaveRequest.objects.filter(start_date__range=[start_date, end_date], status='approved')
        for req in all_requests:
            month_key = req.start_date.strftime('%Y-%m')
            if month_key in monthly_stats:
                monthly_stats[month_key]['days'] += req.duration_days
        
        # Prepare data for plotting
        sorted_months = sorted(monthly_stats.keys())
        months = [datetime.strptime(m, '%Y-%m') for m in sorted_months]
        requests = [monthly_stats[m]['requests'] for m in sorted_months]
        days = [monthly_stats[m]['days'] for m in sorted_months]
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
        
        # Subplot 1: Number of requests
        ax1.plot(months, requests, marker='o', color='#2E86AB')
        ax1.fill_between(months, requests, alpha=0.3, color='#2E86AB')
        ax1.set_ylabel('Number of Requests')
        ax1.set_title('Monthly Leave Trends (Past 12 Months)')
        
        # Subplot 2: Total leave days
        ax2.plot(months, days, marker='s', color='#A23B72')
        ax2.fill_between(months, days, alpha=0.3, color='#A23B72')
        ax2.set_ylabel('Total Leave Days')
        
        # Format X-axis for clarity
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
        ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        fig.autofmt_xdate()
        
        plt.tight_layout()
        return self._save_plot_to_base64()

    def get_leave_patterns_heatmap(self) -> Optional[str]: # <-- FIXED
        """
        Generates a heatmap of leave requests by month and day of the week for the current year.
        
        This helps identify patterns, such as leaves being more common on
        Mondays/Fridays or during specific seasons.
        
        Returns:
            A base64 encoded string of the plot PNG, or None if no data.
        """
        leaves = LeaveRequest.objects.filter(
            status='approved',
            start_date__year=date.today().year
        ).values('start_date')
        
        if not leaves:
            return None
            
        # Initialize a 12x7 grid (months x days of week) with zeros.
        heatmap_data = np.zeros((12, 7))  
        
        for leave in leaves:
            month_index = leave['start_date'].month - 1  # 0-indexed month
            day_index = leave['start_date'].weekday()  # 0=Monday, 6=Sunday
            heatmap_data[month_index][day_index] += 1
        
        fig, ax = plt.subplots(figsize=(10, 8))
        
        sns.heatmap(heatmap_data, ax=ax, cmap='YlOrRd', annot=True, fmt=".0f", linewidths=.5,
                    xticklabels=['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
                    yticklabels=['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'])
        
        ax.set_title(f'Leave Request Patterns - {date.today().year}')
        ax.set_xlabel('Day of the Week')
        ax.set_ylabel('Month')
        
        plt.tight_layout()
        return self._save_plot_to_base64()

    def get_approval_metrics_chart(self) -> Optional[str]: # <-- FIXED
        """
        Generates charts related to the leave approval process.
        
        1. A pie chart showing the distribution of all request statuses.
        2. A bar chart showing the approval rate for each leave type.
        
        Returns:
            A base64 encoded string of the plot PNG, or None if no data.
        """
        status_counts = list(LeaveRequest.objects.values('status').annotate(count=Count('id')))
        
        type_approval_data = []
        for leave_type in LeaveType.objects.all():
            total = LeaveRequest.objects.filter(leave_type=leave_type).count()
            if total > 0:
                approved = LeaveRequest.objects.filter(leave_type=leave_type, status='approved').count()
                approval_rate = (approved / total) * 100
                type_approval_data.append((leave_type.name, approval_rate, total))
        
        if not status_counts or not type_approval_data:
            return None
            
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
        
        # --- Subplot 1: Status Distribution Pie Chart ---
        labels = [item['status'].title() for item in status_counts]
        counts = [item['count'] for item in status_counts]
        colors = {'Pending': '#FFA500', 'Approved': '#32CD32', 'Rejected': '#FF6347', 'Cancelled': '#D3D3D3'}
        pie_colors = [colors.get(label, '#CCCCCC') for label in labels]
        
        ax1.pie(counts, labels=labels, autopct='%1.1f%%', colors=pie_colors, startangle=90)
        ax1.set_title('Overall Request Status Distribution')
        ax1.axis('equal') # Ensures pie is drawn as a circle.
        
        # --- Subplot 2: Approval Rates by Leave Type Bar Chart ---
        type_approval_data.sort(key=lambda x: x[1], reverse=True)
        types = [x[0] for x in type_approval_data]
        rates = [x[1] for x in type_approval_data]
        totals = [x[2] for x in type_approval_data]
        
        bars = ax2.bar(types, rates, color='skyblue', alpha=0.8)
        ax2.set_ylabel('Approval Rate (%)')
        ax2.set_title('Approval Rates by Leave Type')
        ax2.set_ylim(0, 105) # Give some space for labels
        
        # Add text labels on top of bars
        for bar, rate, total in zip(bars, rates, totals):
            ax2.text(bar.get_x() + bar.get_width()/2., bar.get_height(),
                     f'{rate:.1f}%\n(n={total})', ha='center', va='bottom', fontsize=9)
        
        plt.setp(ax2.get_xticklabels(), rotation=45, ha='right')
        plt.tight_layout()
        return self._save_plot_to_base64()

    def get_utilization_analysis_chart(self) -> Optional[str]: # <-- FIXED
        """
        Analyzes how employees are utilizing their monthly leave allowance.
        
        1. A bar chart showing the average leave utilization rate per team.
        2. A histogram showing the distribution of utilization rates across all employees.
        
        Returns:
            A base64 encoded string of the plot PNG, or None if no data.
        """
        current_month = date.today().replace(day=1)
        
        team_utilization = {}
        all_employees = Employee.objects.select_related('team').filter(monthly_leave_allowance__gt=0)
        
        for employee in all_employees:
            used_days = sum(req.duration_days for req in LeaveRequest.objects.filter(
                employee=employee, status='approved', start_date__gte=current_month
            ))
            utilization_rate = (used_days / employee.monthly_leave_allowance) * 100
            
            team_name = employee.team.name if employee.team else 'No Team'
            if team_name not in team_utilization:
                team_utilization[team_name] = []
            team_utilization[team_name].append(utilization_rate)
            
        if not team_utilization:
            return None
            
        team_averages = sorted([(team, np.mean(rates)) for team, rates in team_utilization.items()], key=lambda x: x[1], reverse=True)
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
        
        # --- Subplot 1: Team Utilization Averages ---
        teams, avg_rates = zip(*team_averages)
        colors = ['red' if r > 80 else 'orange' if r > 60 else 'green' for r in avg_rates]
        
        bars = ax1.barh(teams, avg_rates, color=colors, alpha=0.7)
        ax1.set_xlabel('Average Utilization of Monthly Allowance (%)')
        ax1.set_title('Team Leave Utilization (Current Month)')
        ax1.axvline(x=80, color='orange', linestyle='--', label='80% Warning')
        ax1.legend()
        
        for bar, rate in zip(bars, avg_rates):
            ax1.text(bar.get_width() + 1, bar.get_y() + bar.get_height()/2, 
                     f'{rate:.1f}%', ha='left', va='center')
        
        # --- Subplot 2: Employee Utilization Distribution Histogram ---
        all_rates = [rate for team_rates in team_utilization.values() for rate in team_rates]
        
        ax2.hist(all_rates, bins=20, color='skyblue', edgecolor='black')
        ax2.set_xlabel('Utilization Rate (%)')
        ax2.set_ylabel('Number of Employees')
        ax2.set_title('Employee Utilization Distribution')
        mean_rate = np.mean(all_rates)
        ax2.axvline(x=mean_rate, color='red', linestyle='--', label=f'Average: {mean_rate:.1f}%')
        ax2.legend()
        
        plt.tight_layout()
        return self._save_plot_to_base64()

    def get_team_workload_impact_chart(self) -> Optional[str]: # <-- FIXED
        """
        Generates a line chart forecasting the workload impact for each team over the next 30 days.
        
        Workload impact is defined as the percentage of a team's members
        on approved leave on any given day.
        
        Returns:
            A base64 encoded string of the plot PNG, or None if no data.
        """
        today = date.today()
        forecast_days = 30
        dates = [today + timedelta(days=i) for i in range(forecast_days)]
        
        teams = Team.objects.prefetch_related('employee_set').all()
        workload_data = []
        
        for team in teams:
            total_employees = team.employee_set.count()
            if total_employees == 0:
                continue
            
            # Pre-fetch relevant leave requests for the team to optimize queries
            team_leaves = LeaveRequest.objects.filter(
                employee__team=team,
                status='approved',
                start_date__lte=dates[-1], # leaves that start before the forecast period ends
                end_date__gte=dates[0]    # leaves that end after the forecast period starts
            )
            
            daily_impact = []
            for check_date in dates:
                on_leave = sum(1 for leave in team_leaves if leave.start_date <= check_date <= leave.end_date)
                impact_percentage = (on_leave / total_employees) * 100
                daily_impact.append(impact_percentage)
            
            workload_data.append((team.name, daily_impact, total_employees))
        
        if not workload_data:
            return None
            
        fig, ax = plt.subplots(figsize=(14, 8))
        
        # Plot each team's workload forecast
        for team_name, daily_impact, team_size in workload_data:
            ax.plot(dates, daily_impact, marker='.', linestyle='-', label=f'{team_name} (n={team_size})')
        
        # Add horizontal lines for warning thresholds
        ax.axhline(y=20, color='orange', linestyle='--', alpha=0.7, label='20% Impact Warning')
        ax.axhline(y=40, color='red', linestyle='--', alpha=0.7, label='40% Critical Impact')
        
        ax.set_xlabel('Date')
        ax.set_ylabel('Workload Impact (% of team on leave)')
        ax.set_title('30-Day Team Workload Impact Forecast')
        ax.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
        ax.grid(True, which='both', linestyle='--', linewidth=0.5)
        
        # Format X-axis for better readability
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=3))
        fig.autofmt_xdate()
        
        plt.tight_layout(rect=[0, 0, 0.85, 1]) # Adjust layout to make space for legend
        return self._save_plot_to_base64()

    def _save_plot_to_base64(self) -> str:
        """
        Saves the current matplotlib plot to a memory buffer and returns it as a base64 encoded string.
        
        This allows embedding the plot directly into HTML templates without
        saving to a file.
        
        Returns:
            A base64 encoded string representation of the PNG image.
        """
        buffer = BytesIO()
        # Save figure to a PNG in the buffer with high resolution and tight bounding box
        plt.savefig(buffer, format='png', dpi=150, bbox_inches='tight')
        buffer.seek(0)
        image_png = buffer.getvalue()
        buffer.close()
        
        # Clear the current figure to free memory and prevent plots from overlapping
        plt.clf()
        plt.close()
        
        # Encode the PNG image to base64 and decode to a UTF-8 string
        return base64.b64encode(image_png).decode('utf-8')


@admin.register(LeaveRequestAudit)
class LeaveRequestAuditAdmin(admin.ModelAdmin):
    """
    Admin configuration for the LeaveRequestAudit model.
    
    Provides a read-only view of the audit trail for leave requests,
    tracking actions like creation, approval, and rejection.
    """
    list_display = ('leave_request', 'action', 'performed_by', 'timestamp') # 'details' was in your original but not a model field, removed for clarity unless you add it
    list_filter = ('action', 'timestamp')
    search_fields = ('leave_request__employee__name', 'performed_by__name')
    readonly_fields = ('leave_request', 'action', 'performed_by', 'timestamp') # removed 'details'

    def has_add_permission(self, request):
        """Disables the ability to add new audit entries from the admin."""
        return False

    def has_change_permission(self, request, obj=None):
        """Disables the ability to change existing audit entries from the admin."""
        return False