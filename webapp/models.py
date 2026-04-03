"""
models.py — SQLite Database Models
Handles all database operations for users, trip sessions, and alerts.
"""

import sqlite3
import os
import json
import datetime
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
            face_image_paths TEXT DEFAULT '[]',
            body_image_path TEXT DEFAULT '',
            email_sent INTEGER DEFAULT 0,
            action_taken TEXT DEFAULT 'none',
            reviewed INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (trip_session_id) REFERENCES trip_sessions(id)
        );
    """)
    conn.commit()
    conn.close()
    print("[DB] Database initialized.")


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

def create_alert(user_id, trip_session_id, alert_level, face_paths, body_path):
    """Create a new alert record. Returns alert id."""
    conn = get_db()
    now = datetime.datetime.now().isoformat()
    cursor = conn.execute(
        """INSERT INTO alerts (user_id, trip_session_id, alert_level, timestamp,
           face_image_paths, body_image_path)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, trip_session_id, alert_level, now,
         json.dumps(face_paths), body_path)
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
