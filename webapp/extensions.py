"""
extensions.py — Shared Flask extensions
Avoids circular imports between app.py and routes.py.
"""

from flask_socketio import SocketIO

# async_mode='threading' is safest for OpenCV + background threads
socketio = SocketIO(async_mode='threading', cors_allowed_origins="*")
