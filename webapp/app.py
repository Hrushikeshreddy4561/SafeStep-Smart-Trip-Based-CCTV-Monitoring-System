"""
app.py — SafeStep CCTV Web Application
Flask entry point. Run with: python -m webapp.app
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

# Load .env from webapp directory
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def create_app():
    app = Flask(__name__,
                template_folder=os.path.join(os.path.dirname(__file__), 'templates'),
                static_folder=os.path.join(os.path.dirname(__file__), 'static'))

    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'safestep-dev-key-change-me')

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

    # ── Cleanup on exit ───────────────────────────────────────────────────
    from webapp.cctv_runner import cctv_runner
    atexit.register(cctv_runner.shutdown)

    print("\n" + "=" * 55)
    print("  SafeStep -- Smart Trip-Based CCTV Monitor")
    print("  URL: http://localhost:5000")
    print("=" * 55 + "\n")

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
