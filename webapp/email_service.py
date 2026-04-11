"""
email_service.py — EmailJS REST API Integration (v2 — Supabase Cloud URLs)
Sends intruder alert emails and daily summaries via EmailJS.

v2 Changes:
  - send_intruder_alert() now accepts optional cloud_face_urls and cloud_body_url
  - If cloud URLs are available, they are used in the email (accessible anywhere)
  - Falls back to local APP_URL/evidence/ links if Supabase upload failed
"""

import os
import json
import datetime
import requests
from dotenv import load_dotenv

WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(WEBAPP_DIR)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
load_dotenv(os.path.join(WEBAPP_DIR, ".env"))

EMAILJS_API_URL = "https://api.emailjs.com/api/v1.0/email/send"
EMAILJS_PUBLIC_KEY = os.getenv("EMAILJS_PUBLIC_KEY", "")
EMAILJS_PRIVATE_KEY = os.getenv("EMAILJS_PRIVATE_KEY", "")
EMAILJS_SERVICE_ID = os.getenv("EMAILJS_SERVICE_ID", "")
EMAILJS_ALERT_TEMPLATE_ID = os.getenv("EMAILJS_ALERT_TEMPLATE_ID", "")
EMAILJS_SUMMARY_TEMPLATE_ID = os.getenv("EMAILJS_SUMMARY_TEMPLATE_ID", "")
APP_URL = os.getenv("APP_URL", "http://localhost:5000")


def _send_email(template_id, template_params):
    """Send an email via EmailJS REST API."""
    if not all([EMAILJS_PUBLIC_KEY, EMAILJS_SERVICE_ID, template_id]):
        print("[EMAIL] EmailJS not configured — skipping email.")
        return False

    payload = {
        "service_id": EMAILJS_SERVICE_ID,
        "template_id": template_id,
        "user_id": EMAILJS_PUBLIC_KEY,
        "accessToken": EMAILJS_PRIVATE_KEY,
        "template_params": template_params,
    }

    try:
        resp = requests.post(EMAILJS_API_URL, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"[EMAIL] Sent successfully via template {template_id}")
            return True
        else:
            print(f"[EMAIL] Failed: {resp.status_code} — {resp.text}")
            return False
    except Exception as e:
        print(f"[EMAIL] Error: {e}")
        return False


def send_intruder_alert(user, alert_id, face_paths, body_path, alert_level,
                        cloud_face_urls=None, cloud_body_url=None):
    """
    Send an intruder alert email with captured face details.

    Parameters
    ----------
    user             : dict-like user row (name, email)
    alert_id         : int, the alert ID for the review link
    face_paths       : list of filenames (just the basename, not full path)
    body_path        : str, body image filename
    alert_level      : str, e.g. 'HIGH' or 'CRITICAL'
    cloud_face_urls  : list of str, optional Supabase public URLs for face images
    cloud_body_url   : str, optional Supabase public URL for body image
    """
    now = datetime.datetime.now()
    timestamp = now.strftime("%B %d, %Y at %I:%M %p")

    # Use cloud URLs if available, otherwise fall back to local
    if cloud_face_urls and cloud_face_urls[0]:
        face_url_primary = cloud_face_urls[0]
    else:
        face_url_primary = (f"{APP_URL}/evidence/{os.path.basename(face_paths[0])}"
                            if face_paths else "")

    if cloud_body_url:
        body_url = cloud_body_url
    else:
        body_url = (f"{APP_URL}/evidence/{os.path.basename(body_path)}"
                    if body_path else "")

    review_link = f"{APP_URL}/alerts/{alert_id}"

    # Build a text description of images for the email template
    face_count = len(face_paths)
    alert_details = (
        f"Alert Level: {alert_level}\n"
        f"Time: {timestamp}\n"
        f"Unknown faces detected: {face_count}\n"
        f"Camera: CAM-01"
    )

    template_params = {
        "to_name": user['name'],
        "to_email": user['email'],
        "email": user['email'],
        "alert_level": alert_level,
        "timestamp": timestamp,
        "face_count": str(face_count),
        "alert_details": alert_details,
        "review_link": review_link,
        "face_image_url": face_url_primary,
        "body_image_url": body_url,
    }

    return _send_email(EMAILJS_ALERT_TEMPLATE_ID, template_params)


def send_daily_summary(user, trip_start, alerts_today, trip_duration_str):
    """
    Send a daily summary email.

    Parameters
    ----------
    user             : dict-like user row
    trip_start       : str, when the trip started
    alerts_today     : list of alert rows from today
    trip_duration_str: str, human-readable duration
    """
    today = datetime.date.today().strftime("%B %d, %Y")
    alert_count = len(alerts_today)

    # Build timeline
    lines = []
    for a in alerts_today:
        ts = a['timestamp']
        level = a['alert_level']
        lines.append(f"  • [{level}] {ts}")

    alert_details = "\n".join(lines) if lines else "No alerts today — all clear!"

    template_params = {
        "to_name": user['name'],
        "to_email": user['email'],
        "email": user['email'],
        "date": today,
        "alert_count": str(alert_count),
        "alert_details": alert_details,
        "trip_duration": trip_duration_str,
        "trip_start": trip_start,
        "dashboard_link": f"{APP_URL}/dashboard",
    }

    return _send_email(EMAILJS_SUMMARY_TEMPLATE_ID, template_params)
