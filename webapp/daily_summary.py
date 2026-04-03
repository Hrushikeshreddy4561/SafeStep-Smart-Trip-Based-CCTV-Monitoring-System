"""
daily_summary.py — APScheduler Job for Daily Summary Emails
Sends a summary email at 11 PM every day while trip mode is active.
"""

import datetime
from webapp.models import get_alerts_today, get_active_trip, get_user_by_id
from webapp.email_service import send_daily_summary


def send_daily_summary_job(app):
    """
    Scheduled job — checks all users with active trips
    and sends them a daily summary email.
    """
    with app.app_context():
        from webapp.models import get_db
        conn = get_db()
        active_trips = conn.execute(
            "SELECT * FROM trip_sessions WHERE status='active'"
        ).fetchall()
        conn.close()

        for trip in active_trips:
            user = get_user_by_id(trip['user_id'])
            if not user:
                continue

            alerts_today = get_alerts_today(user['id'])

            # Calculate trip duration
            start = datetime.datetime.fromisoformat(trip['start_time'])
            duration = datetime.datetime.now() - start
            days = duration.days
            hours, remainder = divmod(duration.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            duration_str = f"{days} day(s), {hours}h {minutes}m"

            success = send_daily_summary(
                user,
                trip_start=trip['start_time'],
                alerts_today=alerts_today,
                trip_duration_str=duration_str
            )

            if success:
                print(f"[SUMMARY] Daily summary sent to {user['email']}")
            else:
                print(f"[SUMMARY] Failed to send summary to {user['email']}")
