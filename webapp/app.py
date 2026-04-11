"""
app.py — SafeStep CCTV Web Application
Flask + SocketIO entry point. Run with: python -m webapp.app

Changes from v1:
  - Flask-SocketIO replaces plain Flask for real-time push events
  - Camera preview auto-starts on boot (no manual click needed)
  - socketio instance exported for use in routes/cctv_runner
"""

import os
import sys
import atexit
from flask import Flask
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

# Ensure project root is in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Load environment in this order:
# 1) project root .env (primary)
# 2) webapp/.env (optional fallback)
WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(WEBAPP_DIR)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
load_dotenv(os.path.join(WEBAPP_DIR, ".env"))

# ── SocketIO instance (from shared extensions module) ────────────────────────
from webapp.extensions import socketio


def create_app():
    app = Flask(__name__,
                template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
                static_folder=os.path.join(os.path.dirname(__file__), 'static'))

    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'safestep-dev-key-change-me')

    # ── Initialize SocketIO with app ─────────────────────────────────────
    socketio.init_app(app)

    # ── Initialize database ───────────────────────────────────────────────
    from webapp.models import init_db
    init_db()

    # ── Initialize Flask-Login ────────────────────────────────────────────
    from webapp.auth import init_login_manager, auth_bp
    init_login_manager(app)

    # ── Register blueprints ───────────────────────────────────────────────
    from webapp.routes import main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    # ── Start daily summary scheduler ─────────────────────────────────────
    from webapp.daily_summary import send_daily_summary_job

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=lambda: send_daily_summary_job(app),
        trigger='cron',
        hour=23,       # 11 PM
        minute=0,
        id='daily_summary',
        replace_existing=True
    )
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())

    # ── Auto-start camera preview on boot ─────────────────────────────────
    from webapp.cctv_runner import cctv_runner
    import threading

    def _boot_camera():
        """Start camera preview in background so it doesn't block app startup."""
        import time
        time.sleep(1.5)  # Let Flask finish binding to port first
        cctv_runner.start_preview()
        print("[BOOT] Camera preview auto-started.")

    threading.Thread(target=_boot_camera, daemon=True).start()

    # ── Cleanup on exit ───────────────────────────────────────────────────
    atexit.register(cctv_runner.shutdown)

    print("\n" + "=" * 55)
    print("  SafeStep -- Smart Trip-Based CCTV Monitor")
    print("  URL: http://localhost:5000")
    print("  Real-Time: WebSocket enabled")
    print("=" * 55 + "\n")

    return app


if __name__ == '__main__':
    app = create_app()
    # Use socketio.run() instead of app.run() for WebSocket support
    socketio.run(app, host='0.0.0.0', port=5000, debug=False,
                 use_reloader=False, allow_unsafe_werkzeug=True)
