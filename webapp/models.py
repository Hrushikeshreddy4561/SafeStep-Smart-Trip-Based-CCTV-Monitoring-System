"""
models.py — SQLite Database Models
Handles all database operations for users, trip sessions, and alerts.
"""

import sqlite3
import os
import json
import datetime
import re
import bcrypt

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")


def get_db():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            phone TEXT DEFAULT '',
            neighbour_name TEXT DEFAULT '',
            neighbour_phone TEXT DEFAULT '',
            police_phone TEXT DEFAULT '100',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS trip_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP,
            status TEXT DEFAULT 'active',
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            trip_session_id INTEGER,
            alert_level TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            camera_label TEXT DEFAULT '',
            face_image_paths TEXT DEFAULT '[]',
            body_image_path TEXT DEFAULT '',
            email_sent INTEGER DEFAULT 0,
            action_taken TEXT DEFAULT 'none',
            reviewed INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (trip_session_id) REFERENCES trip_sessions(id)
        );

        CREATE TABLE IF NOT EXISTS known_face_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            person_key TEXT NOT NULL,
            person_name TEXT NOT NULL,
            last_seen_camera TIMESTAMP,
            last_seen_alert TIMESTAMP,
            last_alert_image TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, person_key)
        );

        CREATE TABLE IF NOT EXISTS trip_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            days_csv TEXT DEFAULT '',
            enabled INTEGER DEFAULT 0,
            active_by_schedule INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id)
        );

        CREATE TABLE IF NOT EXISTS camera_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            camera_id INTEGER NOT NULL,
            camera_label TEXT NOT NULL,
            location_type TEXT DEFAULT 'indoor',
            priority_level TEXT DEFAULT 'high',
            trip_enabled INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, camera_id)
        );
    """)

    # Migration for existing databases created before camera_label existed.
    columns = [row[1] for row in conn.execute("PRAGMA table_info(alerts)").fetchall()]
    if 'camera_label' not in columns:
        conn.execute("ALTER TABLE alerts ADD COLUMN camera_label TEXT DEFAULT ''")

    conn.commit()
    conn.close()
    print("[DB] Database initialized.")


def get_trip_schedule(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM trip_schedules WHERE user_id=? LIMIT 1",
        (user_id,)
    ).fetchone()
    conn.close()
    return row


def upsert_trip_schedule(user_id, start_time, end_time, days_csv, enabled=True):
    now = datetime.datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        """INSERT INTO trip_schedules
           (user_id, start_time, end_time, days_csv, enabled, active_by_schedule, updated_at)
           VALUES (?, ?, ?, ?, ?, 0, ?)
           ON CONFLICT(user_id)
           DO UPDATE SET
              start_time=excluded.start_time,
              end_time=excluded.end_time,
              days_csv=excluded.days_csv,
              enabled=excluded.enabled,
              updated_at=excluded.updated_at
        """,
        (user_id, start_time, end_time, days_csv or '', 1 if enabled else 0, now)
    )
    conn.commit()
    conn.close()


def disable_trip_schedule(user_id):
    now = datetime.datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        """UPDATE trip_schedules
           SET enabled=0, active_by_schedule=0, updated_at=?
           WHERE user_id=?
        """,
        (now, user_id)
    )
    conn.commit()
    conn.close()


def set_schedule_active_by_schedule(user_id, active):
    now = datetime.datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        """UPDATE trip_schedules
           SET active_by_schedule=?, updated_at=?
           WHERE user_id=?
        """,
        (1 if active else 0, now, user_id)
    )
    conn.commit()
    conn.close()


def get_enabled_trip_schedules():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM trip_schedules WHERE enabled=1"
    ).fetchall()
    conn.close()
    return rows


def get_camera_configs_for_user(user_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM camera_configs WHERE user_id=? ORDER BY camera_id",
        (user_id,)
    ).fetchall()
    conn.close()
    return rows


def upsert_camera_config(user_id, camera_id, camera_label,
                         location_type='indoor', priority_level='high',
                         trip_enabled=True):
    now = datetime.datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        """INSERT INTO camera_configs
           (user_id, camera_id, camera_label, location_type, priority_level, trip_enabled, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, camera_id)
           DO UPDATE SET
              camera_label=excluded.camera_label,
              location_type=excluded.location_type,
              priority_level=excluded.priority_level,
              trip_enabled=excluded.trip_enabled,
              updated_at=excluded.updated_at
        """,
        (
            user_id,
            int(camera_id),
            camera_label,
            location_type,
            priority_level,
            1 if trip_enabled else 0,
            now,
        )
    )
    conn.commit()
    conn.close()


def _person_key(name):
    cleaned = re.sub(r'[^a-zA-Z0-9\s_-]', '', (name or '').strip())
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.lower().replace(' ', '_')


# ─── User Operations ─────────────────────────────────────────────────────────

def create_user(name, email, password, phone="", neighbour_name="",
                neighbour_phone="", police_phone="100"):
    """Create a new user. Returns user id or None if email exists."""
    conn = get_db()
    try:
        pw_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        cursor = conn.execute(
            """INSERT INTO users (name, email, password_hash, phone,
               neighbour_name, neighbour_phone, police_phone)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, email, pw_hash.decode('utf-8'), phone,
             neighbour_name, neighbour_phone, police_phone)
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def authenticate_user(email, password):
    """Check email/password. Returns user row or None."""
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    if user and bcrypt.checkpw(password.encode('utf-8'),
                                user['password_hash'].encode('utf-8')):
        return user
    return None


def get_user_by_id(user_id):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user


def update_user_contacts(user_id, phone, neighbour_name, neighbour_phone, police_phone):
    conn = get_db()
    conn.execute(
        """UPDATE users SET phone=?, neighbour_name=?, neighbour_phone=?, police_phone=?
           WHERE id=?""",
        (phone, neighbour_name, neighbour_phone, police_phone, user_id)
    )
    conn.commit()
    conn.close()


# ─── Trip Session Operations ─────────────────────────────────────────────────

def start_trip_session(user_id):
    """Start a new trip session. Returns session id."""
    conn = get_db()
    now = datetime.datetime.now().isoformat()
    cursor = conn.execute(
        "INSERT INTO trip_sessions (user_id, start_time, status) VALUES (?, ?, 'active')",
        (user_id, now)
    )
    conn.commit()
    session_id = cursor.lastrowid
    conn.close()
    return session_id


def end_trip_session(session_id):
    """End an active trip session."""
    conn = get_db()
    now = datetime.datetime.now().isoformat()
    conn.execute(
        "UPDATE trip_sessions SET end_time=?, status='completed' WHERE id=?",
        (now, session_id)
    )
    conn.commit()
    conn.close()


def get_active_trip(user_id):
    """Get the active trip session for a user, if any."""
    conn = get_db()
    session = conn.execute(
        "SELECT * FROM trip_sessions WHERE user_id=? AND status='active' ORDER BY id DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    conn.close()
    return session


def get_trip_sessions(user_id, limit=10):
    conn = get_db()
    sessions = conn.execute(
        "SELECT * FROM trip_sessions WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return sessions


# ─── Alert Operations ─────────────────────────────────────────────────────────

def create_alert(user_id, trip_session_id, alert_level, face_paths, body_path,
                 camera_label=""):
    """Create a new alert record. Returns alert id."""
    conn = get_db()
    now = datetime.datetime.now().isoformat()
    cursor = conn.execute(
        """INSERT INTO alerts (user_id, trip_session_id, alert_level, timestamp,
              camera_label, face_image_paths, body_image_path)
              VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, trip_session_id, alert_level, now,
            camera_label or "", json.dumps(face_paths), body_path)
    )
    conn.commit()
    alert_id = cursor.lastrowid
    conn.close()
    return alert_id


def mark_alert_emailed(alert_id):
    conn = get_db()
    conn.execute("UPDATE alerts SET email_sent=1 WHERE id=?", (alert_id,))
    conn.commit()
    conn.close()


def mark_alert_reviewed(alert_id):
    conn = get_db()
    conn.execute("UPDATE alerts SET reviewed=1 WHERE id=?", (alert_id,))
    conn.commit()
    conn.close()


def set_alert_action(alert_id, action):
    """Set action_taken to 'police' or 'neighbour'."""
    conn = get_db()
    conn.execute("UPDATE alerts SET action_taken=? WHERE id=?", (action, alert_id))
    conn.commit()
    conn.close()


def get_alert_by_id(alert_id):
    conn = get_db()
    alert = conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    conn.close()
    return alert


def get_alerts_for_user(user_id, limit=50):
    conn = get_db()
    alerts = conn.execute(
        "SELECT * FROM alerts WHERE user_id=? ORDER BY timestamp DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return alerts


def delete_alert_for_user(alert_id, user_id):
    """Delete one alert owned by user. Returns number of deleted rows."""
    conn = get_db()
    cur = conn.execute(
        "DELETE FROM alerts WHERE id=? AND user_id=?",
        (alert_id, user_id)
    )
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return deleted


def delete_all_alerts_for_user(user_id):
    """Delete all alerts for user. Returns number of deleted rows."""
    conn = get_db()
    cur = conn.execute("DELETE FROM alerts WHERE user_id=?", (user_id,))
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return deleted


def get_unreviewed_count(user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM alerts WHERE user_id=? AND reviewed=0",
        (user_id,)
    ).fetchone()
    conn.close()
    return row['cnt'] if row else 0


def get_alerts_today(user_id):
    """Get all alerts from today for daily summary."""
    conn = get_db()
    today = datetime.date.today().isoformat()
    alerts = conn.execute(
        "SELECT * FROM alerts WHERE user_id=? AND date(timestamp)=? ORDER BY timestamp",
        (user_id, today)
    ).fetchall()
    conn.close()
    return alerts


def get_total_stats(user_id):
    """Get aggregate stats for a user."""
    conn = get_db()
    total_trips = conn.execute(
        "SELECT COUNT(*) as cnt FROM trip_sessions WHERE user_id=?", (user_id,)
    ).fetchone()['cnt']
    total_alerts = conn.execute(
        "SELECT COUNT(*) as cnt FROM alerts WHERE user_id=?", (user_id,)
    ).fetchone()['cnt']
    conn.close()
    return {"total_trips": total_trips, "total_alerts": total_alerts}


# ─── Known Face Activity ─────────────────────────────────────────────────────

def upsert_known_face_activity(user_id, person_name,
                               seen_in_camera=False,
                               seen_in_alert=False,
                               alert_image=""):
    """Update per-person last-seen fields for known faces."""
    key = _person_key(person_name)
    if not key:
        return

    now = datetime.datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        """INSERT INTO known_face_activity
           (user_id, person_key, person_name, last_seen_camera, last_seen_alert,
            last_alert_image, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, person_key)
           DO UPDATE SET
              person_name=excluded.person_name,
              last_seen_camera=CASE WHEN excluded.last_seen_camera IS NOT NULL
                 THEN excluded.last_seen_camera ELSE known_face_activity.last_seen_camera END,
              last_seen_alert=CASE WHEN excluded.last_seen_alert IS NOT NULL
                 THEN excluded.last_seen_alert ELSE known_face_activity.last_seen_alert END,
              last_alert_image=CASE WHEN excluded.last_alert_image <> ''
                 THEN excluded.last_alert_image ELSE known_face_activity.last_alert_image END,
              updated_at=excluded.updated_at
        """,
        (
            user_id,
            key,
            person_name,
            now if seen_in_camera else None,
            now if seen_in_alert else None,
            alert_image or "",
            now
        )
    )
    conn.commit()
    conn.close()


def get_known_face_activity_map(user_id):
    """Return map: person_key -> activity row dict."""
    conn = get_db()
    rows = conn.execute(
        """SELECT person_key, person_name, last_seen_camera,
                  last_seen_alert, last_alert_image
           FROM known_face_activity WHERE user_id=?""",
        (user_id,)
    ).fetchall()
    conn.close()

    result = {}
    for row in rows:
        result[row['person_key']] = dict(row)
    return result


def delete_known_face_activity(user_id, person_name):
    key = _person_key(person_name)
    if not key:
        return
    conn = get_db()
    conn.execute(
        "DELETE FROM known_face_activity WHERE user_id=? AND person_key=?",
        (user_id, key)
    )
    conn.commit()
    conn.close()
