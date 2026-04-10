"""
routes.py — Main Application Routes
Dashboard, trip mode API, alert management, video streaming.
"""

import os
import sys
import json
import datetime
from flask import (Blueprint, render_template, request, jsonify, Response,
                   send_from_directory, redirect, url_for)
from flask_login import login_required, current_user

# Add parent dir for CCTV config
PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

import config
from webapp.models import (
    start_trip_session, end_trip_session, get_active_trip,
    create_alert, mark_alert_emailed, mark_alert_reviewed,
    set_alert_action, get_alert_by_id, get_alerts_for_user,
    get_unreviewed_count, get_total_stats, get_user_by_id,
    get_trip_sessions
)
from webapp.cctv_runner import cctv_runner
from webapp.email_service import send_intruder_alert

main_bp = Blueprint('main', __name__)


# ─── Pages ────────────────────────────────────────────────────────────────────

@main_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return redirect(url_for('auth.login'))


@main_bp.route('/dashboard')
@login_required
def dashboard():
    trip = get_active_trip(current_user.id)
    stats = get_total_stats(current_user.id)
    unreviewed = get_unreviewed_count(current_user.id)
    return render_template('dashboard.html',
                           trip=trip, stats=stats, unreviewed=unreviewed)


@main_bp.route('/alerts')
@login_required
def alerts_page():
    alerts = get_alerts_for_user(current_user.id)
    # Parse face_image_paths JSON for each alert
    parsed_alerts = []
    for a in alerts:
        alert_dict = dict(a)
        alert_dict['face_images'] = json.loads(a['face_image_paths'] or '[]')
        parsed_alerts.append(alert_dict)
    return render_template('alerts.html', alerts=parsed_alerts)


@main_bp.route('/alerts/<int:alert_id>')
@login_required
def alert_detail(alert_id):
    alert = get_alert_by_id(alert_id)
    if not alert or alert['user_id'] != current_user.id:
        return redirect(url_for('main.alerts_page'))
    alert_dict = dict(alert)
    alert_dict['face_images'] = json.loads(alert['face_image_paths'] or '[]')
    user = get_user_by_id(current_user.id)
    return render_template('alert_detail.html', alert=alert_dict, user=user)


# ─── Video Stream ─────────────────────────────────────────────────────────────

def generate_frames():
    """Generator for MJPEG streaming with adaptive timing."""
    import time
    target_interval = 1.0 / config.FPS_TARGET
    while True:
        t0 = time.monotonic()
        frame = cctv_runner.get_frame()
        if frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            # Sleep to match target FPS (minus time already spent)
            elapsed = time.monotonic() - t0
            sleep_time = target_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
        else:
            # Camera not producing frames: wait without burning CPU
            time.sleep(0.15)


@main_bp.route('/video_feed')
@login_required
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


# ─── Evidence Images ──────────────────────────────────────────────────────────

@main_bp.route('/evidence/<path:filename>')
def serve_evidence(filename):
    """Serve evidence images from the evidence directory."""
    return send_from_directory(config.EVIDENCE_DIR, filename)


# ─── API Endpoints ────────────────────────────────────────────────────────────

@main_bp.route('/api/status')
@login_required
def api_status():
    """Get current system status."""
    trip = get_active_trip(current_user.id)
    unreviewed = get_unreviewed_count(current_user.id)
    stats = get_total_stats(current_user.id)

    trip_data = None
    if trip:
        start = datetime.datetime.fromisoformat(trip['start_time'])
        duration = datetime.datetime.now() - start
        hours, remainder = divmod(int(duration.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        trip_data = {
            "id": trip['id'],
            "start_time": trip['start_time'],
            "duration": f"{hours}h {minutes}m {seconds}s",
            "duration_seconds": int(duration.total_seconds())
        }

    return jsonify({
        "camera_on": cctv_runner.is_camera_on,
        "trip_active": cctv_runner.is_trip_active,
        "preview_active": cctv_runner.is_preview_active,
        "trip": trip_data,
        "unreviewed_alerts": unreviewed,
        "stats": stats
    })


@main_bp.route('/api/camera/start', methods=['POST'])
@login_required
def api_start_camera():
    """Start camera preview."""
    cctv_runner.start_preview()
    return jsonify({"success": True, "message": "Camera preview started"})


@main_bp.route('/api/camera/stop', methods=['POST'])
@login_required
def api_stop_camera():
    """Stop camera — releases hardware."""
    cctv_runner.stop_preview()
    cctv_runner._stop_loop()
    cctv_runner.stop_camera()
    return jsonify({"success": True, "message": "Camera stopped"})


@main_bp.route('/api/trip/start', methods=['POST'])
@login_required
def api_start_trip():
    """Start trip mode — activates full detection."""
    existing = get_active_trip(current_user.id)
    if existing:
        if cctv_runner.is_trip_active:
            return jsonify({"success": False, "message": "Trip already active"})
        else:
            # Ghost session: DB thinks it's active but runner is not
            end_trip_session(existing['id'])

    session_id = start_trip_session(current_user.id)

    def on_alert(user_id, trip_session_id, face_paths, body_path, alert_level):
        """Called when an unknown person is captured."""
        # Save to database
        face_basenames = [os.path.basename(fp) for fp in face_paths]
        body_basename = os.path.basename(body_path) if body_path else ""
        alert_id = create_alert(user_id, trip_session_id, alert_level,
                                face_basenames, body_basename)

        # Send email
        user = get_user_by_id(user_id)
        if user:
            success = send_intruder_alert(user, alert_id, face_paths,
                                          body_path, alert_level)
            if success:
                mark_alert_emailed(alert_id)

    success = cctv_runner.start_trip_mode(current_user.id, session_id,
                                 on_alert_callback=on_alert)

    if not success:
        end_trip_session(session_id)
        return jsonify({"success": False, "message": "Failed to start camera. Make sure it is connected and not in use by another app."})

    return jsonify({"success": True, "message": "Trip mode activated",
                    "session_id": session_id})


@main_bp.route('/api/trip/stop', methods=['POST'])
@login_required
def api_stop_trip():
    """Stop trip mode."""
    trip = get_active_trip(current_user.id)
    if trip:
        end_trip_session(trip['id'])
    cctv_runner.stop_trip_mode()
    return jsonify({"success": True, "message": "Trip mode deactivated"})


@main_bp.route('/api/alerts')
@login_required
def api_alerts():
    """Get alerts as JSON."""
    alerts = get_alerts_for_user(current_user.id)
    result = []
    for a in alerts:
        result.append({
            "id": a['id'],
            "alert_level": a['alert_level'],
            "timestamp": a['timestamp'],
            "face_images": json.loads(a['face_image_paths'] or '[]'),
            "body_image": a['body_image_path'],
            "email_sent": bool(a['email_sent']),
            "reviewed": bool(a['reviewed']),
            "action_taken": a['action_taken']
        })
    return jsonify(result)


@main_bp.route('/api/alerts/<int:alert_id>/review', methods=['POST'])
@login_required
def api_review_alert(alert_id):
    """Mark an alert as reviewed."""
    alert = get_alert_by_id(alert_id)
    if not alert or alert['user_id'] != current_user.id:
        return jsonify({"success": False}), 404
    mark_alert_reviewed(alert_id)
    return jsonify({"success": True})


@main_bp.route('/api/alerts/<int:alert_id>/action', methods=['POST'])
@login_required
def api_alert_action(alert_id):
    """Record action taken (police/neighbour)."""
    alert = get_alert_by_id(alert_id)
    if not alert or alert['user_id'] != current_user.id:
        return jsonify({"success": False}), 404
    action = request.json.get('action', 'none')
    if action in ('police', 'neighbour'):
        set_alert_action(alert_id, action)
        mark_alert_reviewed(alert_id)
    return jsonify({"success": True, "action": action})
