"""
routes.py — Main Application Routes (v2 — Real-Time + Supabase)
Dashboard, trip mode API, alert management, video streaming.

Changes:
  - Alert callback uploads evidence to Supabase Cloud Storage
  - WebSocket events emitted for instant dashboard updates
  - Trip toggle state synced from runner memory (fixes page-switch desync)
  - Camera auto-recovers preview state on page navigation
"""

import os
import sys
import json
import datetime
import re
import threading
from flask import (Blueprint, render_template, request, jsonify, Response,
                   send_from_directory, redirect, url_for, flash)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

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
    get_trip_sessions, upsert_known_face_activity,
    get_known_face_activity_map, delete_known_face_activity,
    delete_alert_for_user, delete_all_alerts_for_user,
    get_trip_schedule, upsert_trip_schedule, disable_trip_schedule,
    set_schedule_active_by_schedule, get_enabled_trip_schedules,
    get_camera_configs_for_user, upsert_camera_config
)
from webapp.cctv_runner import cctv_runner, cctv_manager
from webapp.email_service import send_intruder_alert
from webapp.supabase_storage import upload_evidence_image

main_bp = Blueprint('main', __name__)

ALLOWED_KNOWN_FACE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp'}
SCHEDULE_DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
LOCATION_TO_PRIORITY = {
    'indoor': 'high',
    'outdoor': 'low',
    'parking': 'medium',
}
PRIORITY_ORDER = ['high', 'medium', 'low']


def _normalize_priority(value):
    v = (value or '').strip().lower()
    return v if v in PRIORITY_ORDER else 'high'


def _normalize_location(value):
    v = (value or '').strip().lower()
    return v if v in LOCATION_TO_PRIORITY else 'indoor'


def _priority_allows_alert(priority_level, alert_level):
    p = _normalize_priority(priority_level)
    level = (alert_level or '').upper()
    if p == 'high':
        return True
    if p == 'medium':
        return level in ('HIGH', 'CRITICAL')
    return level == 'CRITICAL'


def _ensure_camera_configs_for_user(user_id):
    cameras = cctv_manager.list_cameras()
    existing = {
        int(row['camera_id']): row
        for row in get_camera_configs_for_user(user_id)
    }
    for cam in cameras:
        cam_id = int(cam['id'])
        if cam_id in existing:
            continue
        default_location = 'indoor' if cam_id == 0 else 'outdoor'
        default_priority = LOCATION_TO_PRIORITY[default_location]
        upsert_camera_config(
            user_id,
            cam_id,
            cam['label'],
            location_type=default_location,
            priority_level=default_priority,
            trip_enabled=True,
        )


def _camera_config_map(user_id):
    _ensure_camera_configs_for_user(user_id)
    rows = get_camera_configs_for_user(user_id)
    result = {}
    for row in rows:
        cid = int(row['camera_id'])
        result[cid] = {
            'camera_id': cid,
            'camera_label': row['camera_label'],
            'location_type': _normalize_location(row['location_type']),
            'priority_level': _normalize_priority(row['priority_level']),
            'trip_enabled': bool(row['trip_enabled']),
        }
    return result


def _parse_days_csv(days_csv):
    if not days_csv:
        return set()
    return {d.strip().lower() for d in days_csv.split(',') if d.strip()}


def _format_schedule_payload(row):
    if not row:
        return None

    days = [d for d in (row['days_csv'] or '').split(',') if d]
    in_window = _is_schedule_active_now(row)
    return {
        'start_time': row['start_time'],
        'end_time': row['end_time'],
        'days': days,
        'enabled': bool(row['enabled']),
        'active_by_schedule': bool(row['active_by_schedule']),
        'active_now': in_window,
    }


def _is_schedule_active_now(row, now=None):
    if not row or not row['enabled']:
        return False

    try:
        start_t = datetime.datetime.strptime(row['start_time'], '%H:%M').time()
        end_t = datetime.datetime.strptime(row['end_time'], '%H:%M').time()
    except Exception:
        return False

    now = now or datetime.datetime.now()
    now_t = now.time()

    days = _parse_days_csv(row['days_csv'] or '')
    if not days:
        return False
    today_key = SCHEDULE_DAY_KEYS[now.weekday()]

    if start_t < end_t:
        day_ok = today_key in days
        return day_ok and (start_t <= now_t < end_t)

    if start_t > end_t:
        if now_t >= start_t:
            day_ok = today_key in days
            return day_ok
        prev_day_key = SCHEDULE_DAY_KEYS[(now.weekday() - 1) % 7]
        day_ok_prev = prev_day_key in days
        return day_ok_prev and (now_t < end_t)

    return False


def _build_trip_callbacks():
    config_cache = {}

    def on_alert(user_id, trip_session_id, face_paths, body_path, alert_level,
                 familiar_names, camera_id=None, camera_label=None):
        capture_camera_label = (camera_label or 'cam1').strip() or 'cam1'
        cid = int(camera_id) if camera_id is not None else 0

        cfg_map = config_cache.get(user_id)
        if cfg_map is None:
            cfg_map = _camera_config_map(user_id)
            config_cache[user_id] = cfg_map

        cfg = cfg_map.get(cid)
        if not cfg:
            # Backward-safe default if config row missing.
            cfg = {
                'trip_enabled': True,
                'priority_level': 'high',
            }

        if not cfg.get('trip_enabled', True):
            return

        if not _priority_allows_alert(cfg.get('priority_level', 'high'), alert_level):
            return

        face_basenames = [os.path.basename(fp) for fp in face_paths]
        body_basename = os.path.basename(body_path) if body_path else ""
        alert_id = create_alert(user_id, trip_session_id, alert_level,
                    face_basenames, body_basename,
                    camera_label=capture_camera_label)

        for name in familiar_names or []:
            upsert_known_face_activity(
                user_id,
                name,
                seen_in_alert=True
            )

        def _upload_and_email():
            cloud_face_urls = []
            for fp in face_paths:
                url = upload_evidence_image(fp)
                cloud_face_urls.append(url if url else "")

            cloud_body_url = ""
            if body_path:
                cloud_body_url = upload_evidence_image(body_path) or ""

            user = get_user_by_id(user_id)
            if user:
                success = send_intruder_alert(
                    user, alert_id, face_paths, body_path, alert_level,
                    cloud_face_urls=cloud_face_urls,
                    cloud_body_url=cloud_body_url,
                    camera_label=capture_camera_label
                )
                if success:
                    mark_alert_emailed(alert_id)

            _emit_socketio('new_alert', {
                'alert_id': alert_id,
                'alert_level': alert_level,
                'face_count': len(face_paths),
                'camera_id': camera_id,
                'camera_label': capture_camera_label,
                'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'cloud_face_url': cloud_face_urls[0] if cloud_face_urls else "",
            })

        threading.Thread(target=_upload_and_email, daemon=True).start()

    def on_familiar_seen(user_id, familiar_names, camera_id=None, camera_label=None):
        for name in familiar_names:
            upsert_known_face_activity(user_id, name, seen_in_camera=True)
        _emit_socketio('familiar_seen', {
            'names': familiar_names,
            'camera_id': camera_id,
            'camera_label': camera_label,
            'timestamp': datetime.datetime.now().strftime("%H:%M:%S"),
        })

    return on_alert, on_familiar_seen


def _start_trip_for_user(user_id, from_schedule=False):
    existing = get_active_trip(user_id)
    if existing and cctv_manager.is_trip_active:
        return True, 'Trip already active', existing['id'], False

    if existing and not cctv_manager.is_trip_active:
        end_trip_session(existing['id'])

    session_id = start_trip_session(user_id)
    on_alert, on_familiar_seen = _build_trip_callbacks()
    cctv_manager.set_familiar_seen_callback_all(on_familiar_seen)

    cfg_map = _camera_config_map(user_id)
    selected_camera_ids = [
        cid for cid, cfg in cfg_map.items()
        if cfg.get('trip_enabled', True)
    ]
    if not selected_camera_ids:
        end_trip_session(session_id)
        return False, 'No cameras selected for trip monitoring. Configure Cameras page first.', None, False

    success = cctv_manager.start_trip_selected(
        user_id,
        session_id,
        selected_camera_ids=selected_camera_ids,
        on_alert_callback=on_alert
    )

    if not success:
        end_trip_session(session_id)
        return False, 'Failed to start camera. Make sure it is connected and not in use by another app.', None, False

    if from_schedule:
        set_schedule_active_by_schedule(user_id, True)

    _emit_socketio('status_update', {
        'trip_active': True,
        'camera_on': True,
        'preview_active': False,
        'cameras': cctv_manager.get_camera_statuses(),
        'schedule': _format_schedule_payload(get_trip_schedule(user_id)),
    })
    return True, 'Trip mode activated', session_id, True


def _stop_trip_for_user(user_id, from_schedule=False):
    trip = get_active_trip(user_id)
    if trip:
        end_trip_session(trip['id'])

    cctv_manager.set_familiar_seen_callback_all(None)
    cctv_manager.stop_trip_all()

    if from_schedule:
        set_schedule_active_by_schedule(user_id, False)

    _emit_socketio('status_update', {
        'trip_active': False,
        'camera_on': cctv_manager.is_camera_on,
        'preview_active': cctv_manager.is_preview_active,
        'cameras': cctv_manager.get_camera_statuses(),
        'schedule': _format_schedule_payload(get_trip_schedule(user_id)),
    })
    return True, 'Trip mode deactivated'


def process_trip_schedules(_app=None):
    """Background scheduler job: auto start/stop trip mode by saved windows."""
    now = datetime.datetime.now()
    rows = get_enabled_trip_schedules()
    for row in rows:
        user_id = row['user_id']
        should_run_now = _is_schedule_active_now(row, now)
        active_by_schedule = bool(row['active_by_schedule'])

        if should_run_now and not active_by_schedule:
            ok, _, _, started_new = _start_trip_for_user(user_id, from_schedule=False)
            if ok and started_new:
                set_schedule_active_by_schedule(user_id, True)
        elif (not should_run_now) and active_by_schedule:
            _stop_trip_for_user(user_id, from_schedule=True)


def _slugify_face_name(name):
    cleaned = re.sub(r'[^a-zA-Z0-9\s_-]', '', (name or '').strip())
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.lower().replace(' ', '_')


def _display_face_name(filename):
    stem = os.path.splitext(filename)[0]
    return stem.replace('_', ' ').strip().title() or 'Unknown'


def _load_known_faces():
    os.makedirs(config.KNOWN_FACES_DIR, exist_ok=True)
    entries = []
    for filename in sorted(os.listdir(config.KNOWN_FACES_DIR)):
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_KNOWN_FACE_EXTENSIONS:
            continue
        full_path = os.path.join(config.KNOWN_FACES_DIR, filename)
        if not os.path.isfile(full_path):
            continue
        entries.append({
            'filename': filename,
            'name': _display_face_name(filename)
        })
    return entries


def _refresh_known_faces_in_system():
    if os.path.exists(config.EMBEDDINGS_CACHE):
        os.remove(config.EMBEDDINGS_CACHE)
    cctv_manager.reload_known_faces()


def _delete_evidence_file(filename):
    """Delete an evidence file by basename if it exists."""
    if not filename:
        return
    safe_name = os.path.basename(filename)
    full_path = os.path.join(config.EVIDENCE_DIR, safe_name)
    if os.path.exists(full_path) and os.path.isfile(full_path):
        os.remove(full_path)


def _emit_socketio(event, data):
    """Emit a SocketIO event safely (guarded against import failure)."""
    try:
        from webapp.extensions import socketio
        socketio.emit(event, data)
    except Exception as e:
        print(f"[WS] Emit error ({event}): {e}")


# ─── Pages ────────────────────────────────────────────────────────────────────

@main_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return redirect(url_for('auth.login'))


@main_bp.route('/dashboard')
@login_required
def dashboard():
    # Sync: if runner says trip is active, make sure DB agrees
    trip = get_active_trip(current_user.id)
    runner_trip_active = cctv_manager.is_trip_active

    # Fix ghost sessions: DB says active but runner says no
    if trip and not runner_trip_active:
        end_trip_session(trip['id'])
        trip = None

    stats = get_total_stats(current_user.id)
    unreviewed = get_unreviewed_count(current_user.id)
    schedule = get_trip_schedule(current_user.id)
    camera_statuses = cctv_manager.get_camera_statuses()
    camera_ids = [c['id'] for c in camera_statuses]
    return render_template('dashboard.html',
                           trip=trip, stats=stats, unreviewed=unreviewed,
                           runner_trip_active=runner_trip_active,
                           camera_on=cctv_manager.is_camera_on,
                           preview_active=cctv_manager.is_preview_active,
                           trip_schedule=_format_schedule_payload(schedule),
                           cameras=camera_statuses,
                           camera_ids=camera_ids,
                           primary_camera_id=cctv_manager.primary_camera_id)


@main_bp.route('/cameras')
@login_required
def cameras_page():
    _ensure_camera_configs_for_user(current_user.id)
    config_map = _camera_config_map(current_user.id)
    camera_rows = []
    for cam in cctv_manager.list_cameras():
        cid = int(cam['id'])
        cfg = config_map.get(cid, {})
        camera_rows.append({
            'id': cid,
            'label': cam['label'],
            'source': cam['source'],
            'location_type': cfg.get('location_type', 'indoor'),
            'priority_level': cfg.get('priority_level', 'high'),
            'trip_enabled': bool(cfg.get('trip_enabled', True)),
        })
    return render_template('cameras.html', cameras=camera_rows)


@main_bp.route('/cameras/save', methods=['POST'])
@login_required
def cameras_save():
    cameras = cctv_manager.list_cameras()
    for cam in cameras:
        cid = int(cam['id'])
        location = _normalize_location(request.form.get(f'location_{cid}', 'indoor'))
        priority = _normalize_priority(request.form.get(f'priority_{cid}', LOCATION_TO_PRIORITY[location]))
        trip_enabled = bool(request.form.get(f'trip_enabled_{cid}'))
        upsert_camera_config(
            current_user.id,
            cid,
            cam['label'],
            location_type=location,
            priority_level=priority,
            trip_enabled=trip_enabled,
        )

    flash('Camera configuration saved.', 'success')
    return redirect(url_for('main.cameras_page'))


@main_bp.route('/logs')
@login_required
def logs_page():
    sessions = get_trip_sessions(current_user.id, limit=5)
    parsed_sessions = []

    for s in sessions:
        start_time = datetime.datetime.fromisoformat(s['start_time'])
        end_time = (datetime.datetime.fromisoformat(s['end_time'])
                    if s['end_time'] else None)
        duration_ref = end_time or datetime.datetime.now()
        duration_sec = max(0, int((duration_ref - start_time).total_seconds()))
        hours, rem = divmod(duration_sec, 3600)
        minutes, seconds = divmod(rem, 60)

        parsed_sessions.append({
            'id': s['id'],
            'status': s['status'],
            'start_time': s['start_time'],
            'end_time': s['end_time'],
            'duration': f"{hours}h {minutes}m {seconds}s"
        })

    return render_template('logs.html', sessions=parsed_sessions)


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


@main_bp.route('/alerts/<int:alert_id>/delete', methods=['POST'])
@login_required
def delete_alert(alert_id):
    alert = get_alert_by_id(alert_id)
    if not alert or alert['user_id'] != current_user.id:
        flash('Alert not found.', 'error')
        return redirect(url_for('main.alerts_page'))

    face_images = json.loads(alert['face_image_paths'] or '[]')
    for face_file in face_images:
        _delete_evidence_file(face_file)
    _delete_evidence_file(alert['body_image_path'])

    delete_alert_for_user(alert_id, current_user.id)
    flash(f'Alert #{alert_id} deleted.', 'success')
    return redirect(url_for('main.alerts_page'))


@main_bp.route('/alerts/delete-all', methods=['POST'])
@login_required
def delete_all_alerts():
    alerts = get_alerts_for_user(current_user.id, limit=10000)
    for alert in alerts:
        face_images = json.loads(alert['face_image_paths'] or '[]')
        for face_file in face_images:
            _delete_evidence_file(face_file)
        _delete_evidence_file(alert['body_image_path'])

    deleted_count = delete_all_alerts_for_user(current_user.id)
    flash(f'Deleted {deleted_count} alert(s).', 'success')
    return redirect(url_for('main.alerts_page'))


@main_bp.route('/known-faces')
@login_required
def known_faces_page():
    trip_active = cctv_manager.is_trip_active or bool(get_active_trip(current_user.id))
    known_faces = _load_known_faces()
    activity_map = get_known_face_activity_map(current_user.id)
    for face in known_faces:
        person_key = _slugify_face_name(face['name'])
        activity = activity_map.get(person_key, {})
        face['last_seen_camera'] = activity.get('last_seen_camera')
        face['last_seen_alert'] = activity.get('last_seen_alert')
    return render_template('known_faces.html', known_faces=known_faces,
                           trip_active=trip_active)


@main_bp.route('/known-faces/image/<path:filename>')
@login_required
def known_faces_image(filename):
    safe_name = secure_filename(filename)
    return send_from_directory(config.KNOWN_FACES_DIR, safe_name)


@main_bp.route('/known-faces/add', methods=['POST'])
@login_required
def add_known_face():
    if cctv_manager.is_trip_active or get_active_trip(current_user.id):
        flash('Stop Trip Mode before adding a new face.', 'error')
        return redirect(url_for('main.known_faces_page'))

    person_name = (request.form.get('person_name') or '').strip()
    face_image = request.files.get('face_image')

    if not person_name:
        flash('Please enter a person name.', 'error')
        return redirect(url_for('main.known_faces_page'))

    if not face_image or not face_image.filename:
        flash('Please upload a face image.', 'error')
        return redirect(url_for('main.known_faces_page'))

    ext = os.path.splitext(face_image.filename)[1].lower()
    if ext not in ALLOWED_KNOWN_FACE_EXTENSIONS:
        flash('Unsupported image format. Use JPG, PNG, or BMP.', 'error')
        return redirect(url_for('main.known_faces_page'))

    slug = _slugify_face_name(person_name)
    if not slug:
        flash('Invalid name. Use letters and numbers only.', 'error')
        return redirect(url_for('main.known_faces_page'))

    os.makedirs(config.KNOWN_FACES_DIR, exist_ok=True)
    base_filename = f"{slug}{ext}"
    target_filename = base_filename
    counter = 2
    while os.path.exists(os.path.join(config.KNOWN_FACES_DIR, target_filename)):
        target_filename = f"{slug}_{counter}{ext}"
        counter += 1

    target_path = os.path.join(config.KNOWN_FACES_DIR, target_filename)
    face_image.save(target_path)
    _refresh_known_faces_in_system()
    flash(f'Known face added: {person_name}', 'success')
    return redirect(url_for('main.known_faces_page'))


@main_bp.route('/known-faces/delete/<path:filename>', methods=['POST'])
@login_required
def delete_known_face(filename):
    safe_name = secure_filename(filename)
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in ALLOWED_KNOWN_FACE_EXTENSIONS:
        flash('Invalid face file.', 'error')
        return redirect(url_for('main.known_faces_page'))

    target_path = os.path.join(config.KNOWN_FACES_DIR, safe_name)
    if os.path.exists(target_path) and os.path.isfile(target_path):
        person_name = _display_face_name(safe_name)
        os.remove(target_path)
        _refresh_known_faces_in_system()
        delete_known_face_activity(current_user.id, person_name)
        flash(f'Removed known face: {person_name}', 'success')
    else:
        flash('Face file not found.', 'error')
    return redirect(url_for('main.known_faces_page'))


# ─── Video Stream ─────────────────────────────────────────────────────────────

def generate_frames(camera_id=None):
    """Generator for MJPEG streaming with adaptive timing."""
    import time
    target_interval = 1.0 / config.FPS_TARGET
    while True:
        t0 = time.monotonic()
        frame = cctv_manager.get_frame(camera_id)
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
    return Response(generate_frames(cctv_manager.primary_camera_id),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@main_bp.route('/video_feed/<int:camera_id>')
@login_required
def video_feed_camera(camera_id):
    if cctv_manager.get_runner(camera_id) is None:
        return Response(status=404)
    return Response(generate_frames(camera_id),
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
    """Get current system status — source of truth is the runner, not DB."""
    trip = get_active_trip(current_user.id)
    unreviewed = get_unreviewed_count(current_user.id)
    stats = get_total_stats(current_user.id)
    schedule = get_trip_schedule(current_user.id)

    # Runner is the source of truth for trip state
    runner_trip = cctv_manager.is_trip_active

    # Fix ghost sessions: DB says active but runner says no
    if trip and not runner_trip:
        end_trip_session(trip['id'])
        trip = None

    trip_data = None
    if trip and runner_trip:
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
        "camera_on": cctv_manager.is_camera_on,
        "trip_active": runner_trip,
        "preview_active": cctv_manager.is_preview_active,
        "cameras": cctv_manager.get_camera_statuses(),
        "trip": trip_data,
        "schedule": _format_schedule_payload(schedule),
        "unreviewed_alerts": unreviewed,
        "stats": stats
    })


@main_bp.route('/api/camera/start', methods=['POST'])
@login_required
def api_start_camera():
    """Start camera preview."""
    cctv_manager.start_preview_all()
    return jsonify({"success": True, "message": "Camera preview started"})


@main_bp.route('/api/camera/stop', methods=['POST'])
@login_required
def api_stop_camera():
    """Stop camera — releases hardware."""
    cctv_manager.stop_all()
    return jsonify({"success": True, "message": "Camera stopped"})


@main_bp.route('/api/trip/start', methods=['POST'])
@login_required
def api_start_trip():
    """Start trip mode — activates full detection."""
    ok, message, session_id, _started_new = _start_trip_for_user(current_user.id)
    if not ok:
        return jsonify({"success": False, "message": message})
    return jsonify({"success": True, "message": message, "session_id": session_id})


@main_bp.route('/api/trip/stop', methods=['POST'])
@login_required
def api_stop_trip():
    """Stop trip mode."""
    ok, message = _stop_trip_for_user(current_user.id)
    return jsonify({"success": ok, "message": message})


@main_bp.route('/api/trip/schedule')
@login_required
def api_trip_schedule_get():
    row = get_trip_schedule(current_user.id)
    return jsonify({"success": True, "schedule": _format_schedule_payload(row)})


@main_bp.route('/api/trip/schedule', methods=['POST'])
@login_required
def api_trip_schedule_set():
    payload = request.get_json(silent=True) or {}
    start_time = (payload.get('start_time') or '').strip()
    end_time = (payload.get('end_time') or '').strip()
    days = payload.get('days') or []

    try:
        datetime.datetime.strptime(start_time, '%H:%M')
        datetime.datetime.strptime(end_time, '%H:%M')
    except Exception:
        return jsonify({"success": False, "message": "Invalid time format. Use HH:MM."}), 400

    if start_time == end_time:
        return jsonify({"success": False, "message": "Start and end time cannot be the same."}), 400

    if not isinstance(days, list):
        return jsonify({"success": False, "message": "Days must be a list."}), 400

    safe_days = [d for d in [str(x).lower().strip() for x in days] if d in SCHEDULE_DAY_KEYS]
    if not safe_days:
        return jsonify({"success": False, "message": "Please select at least one day."}), 400
    days_csv = ','.join(sorted(set(safe_days), key=SCHEDULE_DAY_KEYS.index))

    upsert_trip_schedule(current_user.id, start_time, end_time, days_csv, enabled=True)
    row = get_trip_schedule(current_user.id)
    return jsonify({
        "success": True,
        "message": "Trip schedule saved.",
        "schedule": _format_schedule_payload(row)
    })


@main_bp.route('/api/trip/schedule/disable', methods=['POST'])
@login_required
def api_trip_schedule_disable():
    disable_trip_schedule(current_user.id)
    row = get_trip_schedule(current_user.id)
    return jsonify({
        "success": True,
        "message": "Trip schedule disabled.",
        "schedule": _format_schedule_payload(row)
    })


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
            "camera_label": a['camera_label'] if 'camera_label' in a.keys() else '',
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
