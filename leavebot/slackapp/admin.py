# leavebot/slackapp/admin.py
"""
Admin panel configuration for the slackapp.

This file defines how the models are displayed and managed in the Django
admin interface. Using ModelAdmin classes allows for rich customization
of the admin experience.
"""
from django.contrib import admin
from django.urls import path
from django.shortcuts import render
from django.db.models import Count, Sum, Q, Avg, F
from django.db.models.functions import TruncMonth, TruncWeek
from datetime import date, datetime, timedelta
from calendar import monthrange
import base64
from io import BytesIO
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
import seaborn as sns
import numpy as np
from .models import Employee, LeaveType, LeaveRequest, LeaveRequestAudit, Holiday, Team

# Set professional styling
plt.style.use('seaborn-v0_8-whitegrid')
sns.set_palette("husl")

@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    """Admin configuration for the Team model."""
    list_display = ('name', 'slack_channel_id', 'employee_count', 'current_on_leave')
    search_fields = ('name',)
    
    def employee_count(self, obj):
        return obj.employee_set.count()
    employee_count.short_description = 'Team Size'
    
    def current_on_leave(self, obj):
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
    list_display = ('name', 'slack_user_id', 'team', 'manager', 'monthly_leave_allowance', 'leave_balance')
    search_fields = ('name', 'slack_user_id', 'email')
    list_filter = ('team', 'manager',)
    
    def leave_balance(self, obj):
        """Calculate remaining leave balance for current month"""
        today = date.today()
        month_start = date(today.year, today.month, 1)
        current_month_requests = LeaveRequest.objects.filter(
            employee=obj,
            status='approved',
            start_date__gte=month_start,
            start_date__lte=today
        )
        used_days = sum(request.duration_days for request in current_month_requests)
        return max(0, obj.monthly_leave_allowance - used_days)
    leave_balance.short_description = 'Remaining Days'

@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'usage_count', 'avg_duration')
    search_fields = ('name',)
    
    def usage_count(self, obj):
        return obj.leaverequest_set.count()
    usage_count.short_description = 'Total Requests'
    
    def avg_duration(self, obj):
        requests = obj.leaverequest_set.all()
        if requests:
            total_days = sum(request.duration_days for request in requests)
            avg = total_days / len(requests)
            return f"{avg:.1f} days"
        return "N/A"
    avg_duration.short_description = 'Avg Duration'

@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ('name', 'date', 'is_upcoming')
    list_filter = ('date',)
    search_fields = ('name',)
    
    def is_upcoming(self, obj):
        return obj.date > date.today()
    is_upcoming.boolean = True
    is_upcoming.short_description = 'Upcoming'

@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'employee', 'leave_type', 'start_date', 'end_date', 'duration_days', 'status', 'approver')
    list_filter = ('status', 'leave_type', 'start_date', 'employee__team')
    search_fields = ('employee__name', 'employee__slack_user_id')
    readonly_fields = ('created_at', 'updated_at', 'duration_days')
    
    change_list_template = "admin/leave_request_changelist.html"

    def get_urls(self):
        """Override default admin URLs to add analytics dashboard."""
        urls = super().get_urls()
        custom_urls = [
            path('analytics/', self.admin_site.admin_view(self.analytics_view), name='leave_analytics'),
        ]
        return custom_urls + urls

    def analytics_view(self, request):
        """Enhanced analytics dashboard with multiple professional charts."""
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

    def get_summary_statistics(self):
        """Calculate key summary statistics for the dashboard."""
        today = date.today()
        current_month = date(today.year, today.month, 1)
        
        # Current month stats
        current_month_requests = LeaveRequest.objects.filter(start_date__gte=current_month)
        pending_requests = current_month_requests.filter(status='pending').count()
        approved_requests = current_month_requests.filter(status='approved').count()
        
        # People currently on leave
        current_on_leave = LeaveRequest.objects.filter(
            status='approved',
            start_date__lte=today,
            end_date__gte=today
        ).count()
        
        # Average approval time (in hours)
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

    def get_team_coverage_chart(self):
        """Generate team coverage risk analysis chart."""
        today = date.today()
        teams = Team.objects.all()
        
        team_data = []
        for team in teams:
            total_employees = team.employee_set.count()
            if total_employees == 0:
                continue
                
            # People currently on leave
            on_leave = LeaveRequest.objects.filter(
                employee__team=team,
                status='approved',
                start_date__lte=today,
                end_date__gte=today
            ).count()
            
            # Upcoming leave (next 7 days)
            upcoming = LeaveRequest.objects.filter(
                employee__team=team,
                status='approved',
                start_date__gt=today,
                start_date__lte=today + timedelta(days=7)
            ).count()
            
            coverage_risk = (on_leave + upcoming) / total_employees * 100
            team_data.append((team.name, total_employees, on_leave, upcoming, coverage_risk))
        
        if not team_data:
            return None
            
        team_data.sort(key=lambda x: x[4], reverse=True)  # Sort by risk
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
        
        # Coverage risk chart
        teams = [x[0] for x in team_data]
        risks = [x[4] for x in team_data]
        colors = ['red' if r > 30 else 'orange' if r > 15 else 'green' for r in risks]
        
        bars1 = ax1.barh(teams, risks, color=colors, alpha=0.7)
        ax1.set_xlabel('Coverage Risk (%)')
        ax1.set_title('Team Coverage Risk Analysis')
        ax1.axvline(x=15, color='orange', linestyle='--', alpha=0.5, label='Warning (15%)')
        ax1.axvline(x=30, color='red', linestyle='--', alpha=0.5, label='Critical (30%)')
        ax1.legend()
        
        # Add value labels on bars
        for bar, risk in zip(bars1, risks):
            width = bar.get_width()
            ax1.text(width + 1, bar.get_y() + bar.get_height()/2, 
                    f'{risk:.1f}%', ha='left', va='center')
        
        # Team capacity overview
        team_sizes = [x[1] for x in team_data]
        on_leaves = [x[2] for x in team_data]
        upcomings = [x[3] for x in team_data]
        
        x = np.arange(len(teams))
        width = 0.35
        
        ax2.bar(x, team_sizes, width, label='Total Employees', alpha=0.8)
        ax2.bar(x, on_leaves, width, label='Currently on Leave', alpha=0.8)
        ax2.bar(x, upcomings, width, bottom=on_leaves, label='Upcoming Leave', alpha=0.8)
        
        ax2.set_xlabel('Teams')
        ax2.set_ylabel('Number of Employees')
        ax2.set_title('Team Capacity Overview')
        ax2.set_xticks(x)
        ax2.set_xticklabels(teams, rotation=45, ha='right')
        ax2.legend()
        
        plt.tight_layout()
        return self._save_plot_to_base64()

    def get_monthly_trends_chart(self):
        """Generate monthly leave trends over the past 12 months."""
        end_date = date.today()
        start_date = end_date - timedelta(days=365)
        
        # Get monthly data - just count requests, calculate days in Python
        monthly_data = LeaveRequest.objects.filter(
            start_date__range=[start_date, end_date],
            status='approved'
        ).extra(
            select={'month': "strftime('%%Y-%%m', start_date)"}
        ).values('month').annotate(
            requests=Count('id')
        ).order_by('month')
        
        if not monthly_data:
            return None
        
        # Calculate total days for each month in Python
        monthly_stats = []
        for month_data in monthly_data:
            month_str = month_data['month']
            requests_count = month_data['requests']
            
            # Get all leave requests for this month and calculate total days
            month_requests = LeaveRequest.objects.filter(
                start_date__year=int(month_str[:4]),
                start_date__month=int(month_str[5:7]),
                status='approved'
            )
            
            total_days = sum(request.duration_days for request in month_requests)
            monthly_stats.append((month_str, requests_count, total_days))
            
        months = [datetime.strptime(item[0], '%Y-%m') for item in monthly_stats]
        requests = [item[1] for item in monthly_stats]
        days = [item[2] for item in monthly_stats]
        
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
        
        # Requests trend
        ax1.plot(months, requests, marker='o', linewidth=2, markersize=6, color='#2E86AB')
        ax1.fill_between(months, requests, alpha=0.3, color='#2E86AB')
        ax1.set_ylabel('Number of Requests')
        ax1.set_title('Monthly Leave Trends (Past 12 Months)')
        ax1.grid(True, alpha=0.3)
        
        # Days trend
        ax2.plot(months, days, marker='s', linewidth=2, markersize=6, color='#A23B72')
        ax2.fill_between(months, days, alpha=0.3, color='#A23B72')
        ax2.set_ylabel('Total Leave Days')
        ax2.set_xlabel('Month')
        ax2.grid(True, alpha=0.3)
        
        # Format x-axis
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        plt.xticks(rotation=45)
        
        plt.tight_layout()
        return self._save_plot_to_base64()

    def get_leave_patterns_heatmap(self):
        """Generate a heatmap showing leave patterns by day of week and month."""
        current_year = date.today().year
        
        # Get leave data for current year
        leaves = LeaveRequest.objects.filter(
            status='approved',
            start_date__year=current_year
        ).values('start_date')
        
        if not leaves:
            return None
            
        # Create heatmap data
        heatmap_data = np.zeros((12, 7))  # 12 months, 7 days of week
        
        for leave in leaves:
            month = leave['start_date'].month - 1  # 0-indexed
            day_of_week = leave['start_date'].weekday()  # 0=Monday
            heatmap_data[month][day_of_week] += 1
        
        fig, ax = plt.subplots(figsize=(10, 8))
        
        # Create heatmap
        im = ax.imshow(heatmap_data, cmap='YlOrRd', aspect='auto')
        
        # Set ticks and labels
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        
        ax.set_xticks(np.arange(7))
        ax.set_yticks(np.arange(12))
        ax.set_xticklabels(days)
        ax.set_yticklabels(months)
        
        # Add colorbar
        plt.colorbar(im, ax=ax, label='Number of Leave Requests')
        
        # Add text annotations
        for i in range(12):
            for j in range(7):
                text = ax.text(j, i, int(heatmap_data[i, j]),
                             ha="center", va="center", color="black", fontsize=8)
        
        ax.set_title(f'Leave Request Patterns - {current_year}\n(By Month and Day of Week)')
        plt.tight_layout()
        return self._save_plot_to_base64()

    def get_approval_metrics_chart(self):
        """Generate approval workflow metrics."""
        # Get approval statistics
        total_requests = LeaveRequest.objects.count()
        status_counts = LeaveRequest.objects.values('status').annotate(count=Count('id'))
        
        # Calculate approval rates by leave type
        type_approval_data = []
        for leave_type in LeaveType.objects.all():
            total = LeaveRequest.objects.filter(leave_type=leave_type).count()
            approved = LeaveRequest.objects.filter(leave_type=leave_type, status='approved').count()
            if total > 0:
                approval_rate = (approved / total) * 100
                type_approval_data.append((leave_type.name, approval_rate, total))
        
        if not status_counts or not type_approval_data:
            return None
            
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
        
        # Status distribution pie chart
        statuses = [item['status'].title() for item in status_counts]
        counts = [item['count'] for item in status_counts]
        colors = {'Pending': '#FFA500', 'Approved': '#32CD32', 'Rejected': '#FF6347', 'Cancelled': '#D3D3D3'}
        pie_colors = [colors.get(status, '#CCCCCC') for status in statuses]
        
        wedges, texts, autotexts = ax1.pie(counts, labels=statuses, autopct='%1.1f%%', 
                                          colors=pie_colors, startangle=90)
        ax1.set_title('Overall Request Status Distribution')
        
        # Approval rates by leave type
        type_approval_data.sort(key=lambda x: x[1], reverse=True)
        types = [x[0] for x in type_approval_data]
        rates = [x[1] for x in type_approval_data]
        totals = [x[2] for x in type_approval_data]
        
        bars = ax2.bar(types, rates, color='skyblue', alpha=0.7)
        ax2.set_ylabel('Approval Rate (%)')
        ax2.set_xlabel('Leave Type')
        ax2.set_title('Approval Rates by Leave Type')
        ax2.set_ylim(0, 100)
        
        # Add value labels and sample sizes
        for bar, rate, total in zip(bars, rates, totals):
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 1,
                    f'{rate:.1f}%\n(n={total})', ha='center', va='bottom', fontsize=9)
        
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        return self._save_plot_to_base64()

    def get_utilization_analysis_chart(self):
        """Analyze leave utilization vs. allowances."""
        current_month = date.today().replace(day=1)
        
        # Calculate utilization by employee
        utilization_data = []
        for employee in Employee.objects.select_related('team'):
            # Get approved requests for current month and calculate total days in Python
            current_month_requests = LeaveRequest.objects.filter(
                employee=employee,
                status='approved',
                start_date__gte=current_month
            )
            
            used_days = sum(request.duration_days for request in current_month_requests)
            
            utilization_rate = (used_days / employee.monthly_leave_allowance) * 100 if employee.monthly_leave_allowance > 0 else 0
            utilization_data.append((employee.team.name if employee.team else 'No Team', utilization_rate))
        
        if not utilization_data:
            return None
            
        # Group by team
        team_utilization = {}
        for team, rate in utilization_data:
            if team not in team_utilization:
                team_utilization[team] = []
            team_utilization[team].append(rate)
        
        # Calculate team averages
        team_averages = [(team, np.mean(rates)) for team, rates in team_utilization.items()]
        team_averages.sort(key=lambda x: x[1], reverse=True)
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
        
        # Team utilization averages
        teams = [x[0] for x in team_averages]
        avg_rates = [x[1] for x in team_averages]
        
        colors = ['red' if r > 80 else 'orange' if r > 60 else 'green' for r in avg_rates]
        bars = ax1.barh(teams, avg_rates, color=colors, alpha=0.7)
        ax1.set_xlabel('Average Utilization (%)')
        ax1.set_title('Team Leave Utilization (Current Month)')
        ax1.axvline(x=50, color='gray', linestyle='--', alpha=0.5, label='50% Target')
        ax1.axvline(x=80, color='orange', linestyle='--', alpha=0.5, label='80% Warning')
        ax1.legend()
        
        # Add value labels
        for bar, rate in zip(bars, avg_rates):
            width = bar.get_width()
            ax1.text(width + 1, bar.get_y() + bar.get_height()/2, 
                    f'{rate:.1f}%', ha='left', va='center')
        
        # Utilization distribution histogram
        all_rates = [rate for team_rates in team_utilization.values() for rate in team_rates]
        ax2.hist(all_rates, bins=20, color='skyblue', alpha=0.7, edgecolor='black')
        ax2.set_xlabel('Utilization Rate (%)')
        ax2.set_ylabel('Number of Employees')
        ax2.set_title('Employee Utilization Distribution')
        ax2.axvline(x=np.mean(all_rates), color='red', linestyle='--', 
                   label=f'Average: {np.mean(all_rates):.1f}%')
        ax2.legend()
        
        plt.tight_layout()
        return self._save_plot_to_base64()

    def get_team_workload_impact_chart(self):
        """Analyze workload impact across teams."""
        today = date.today()
        
        # Get team workload data for next 30 days
        teams = Team.objects.all()
        workload_data = []
        
        for team in teams:
            total_employees = team.employee_set.count()
            if total_employees == 0:
                continue
                
            # Calculate daily impact for next 30 days
            daily_impact = []
            for i in range(30):
                check_date = today + timedelta(days=i)
                on_leave = LeaveRequest.objects.filter(
                    employee__team=team,
                    status='approved',
                    start_date__lte=check_date,
                    end_date__gte=check_date
                ).count()
                
                impact_percentage = (on_leave / total_employees) * 100
                daily_impact.append(impact_percentage)
            
            workload_data.append((team.name, daily_impact, total_employees))
        
        if not workload_data:
            return None
            
        fig, ax = plt.subplots(figsize=(14, 8))
        
        dates = [today + timedelta(days=i) for i in range(30)]
        
        # Plot each team's workload impact
        for team_name, daily_impact, team_size in workload_data:
            ax.plot(dates, daily_impact, marker='o', linewidth=2, label=f'{team_name} (n={team_size})')
        
        # Add warning zones
        ax.axhline(y=20, color='orange', linestyle='--', alpha=0.5, label='20% Impact Warning')
        ax.axhline(y=40, color='red', linestyle='--', alpha=0.5, label='40% Critical Impact')
        
        ax.set_xlabel('Date')
        ax.set_ylabel('Workload Impact (%)')
        ax.set_title('30-Day Team Workload Impact Forecast')
        ax.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
        ax.grid(True, alpha=0.3)
        
        # Format x-axis
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=5))
        plt.xticks(rotation=45)
        
        plt.tight_layout()
        return self._save_plot_to_base64()

    def _save_plot_to_base64(self):
        """Helper method to save matplotlib plot to base64 string."""
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=150, bbox_inches='tight', 
                   facecolor='white', edgecolor='none')
        buffer.seek(0)
        image_png = buffer.getvalue()
        buffer.close()
        plt.clf()
        plt.close()
        return base64.b64encode(image_png).decode('utf-8')

@admin.register(LeaveRequestAudit)
class LeaveRequestAuditAdmin(admin.ModelAdmin):
    list_display = ('leave_request', 'action', 'performed_by', 'timestamp')
    list_filter = ('action', 'timestamp')
    search_fields = ('leave_request__employee__name',)
    readonly_fields = ('timestamp',)
    list_display = ('leave_request', 'action', 'performed_by', 'timestamp')
    list_filter = ('action', 'timestamp')
    search_fields = ('leave_request__employee__name',)
    readonly_fields = ('timestamp',)