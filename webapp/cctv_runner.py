"""
cctv_runner.py — Background CCTV Engine Thread
Runs the CCTV surveillance pipeline in a background thread.
The web app controls start/stop via trip mode toggle.
"""

import sys
import os
import threading
import time
import cv2
import json
import datetime

# Add parent directory to path so we can import the CCTV modules
PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

import config
from motion_detection import MotionDetector
from pet_filter import PetFilter
from face_recognition_module import FaceRecognizer
from alert_system import AlertSystem, set_capture_callback


class CCTVRunner:
    """
    Manages the CCTV surveillance engine in a background thread.

    Two modes:
      1. PREVIEW — Camera is on, shows live feed, no detection.
      2. TRIP MODE — Camera + full detection pipeline (motion, faces, alerts).
    """

    def __init__(self):
        self._camera = None
        self._camera_lock = threading.Lock()
        self._preview_thread = None
        self._trip_thread = None
        self._preview_running = False
        self._trip_running = False
        self._latest_frame = None
        self._frame_lock = threading.Lock()
        self._trip_session_id = None
        self._user_id = None
        self._on_alert_callback = None   # called when unknown person captured

        # Detection modules (loaded once)
        self._detector = None
        self._pet_filter = None
        self._recognizer = None
        self._alerter = None

    # ── Camera Management ─────────────────────────────────────────────────────

    def start_camera(self):
        """Open the camera for live preview."""
        with self._camera_lock:
            if self._camera is not None and self._camera.isOpened():
                return True
            self._camera = cv2.VideoCapture(config.CAMERA_INDEX)
            if not self._camera.isOpened():
                print("[CCTV] ERROR: Cannot open camera")
                self._camera = None
                return False
            self._camera.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_WIDTH)
            self._camera.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_HEIGHT)
            self._camera.set(cv2.CAP_PROP_FPS, config.FPS_TARGET)
            print("[CCTV] Camera opened.")
        return True

    def stop_camera(self):
        """Release the camera."""
        with self._camera_lock:
            if self._camera is not None:
                self._camera.release()
                self._camera = None
                print("[CCTV] Camera released.")

    def get_frame(self):
        """Get the latest frame as JPEG bytes for streaming."""
        with self._frame_lock:
            if self._latest_frame is not None:
                ret, jpeg = cv2.imencode('.jpg', self._latest_frame,
                                         [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ret:
                    return jpeg.tobytes()
        return None

    # ── Preview Mode ──────────────────────────────────────────────────────────

    def start_preview(self):
        """Start camera preview without detection."""
        if self._preview_running:
            return
        if not self.start_camera():
            return
        self._preview_running = True
        self._preview_thread = threading.Thread(target=self._preview_loop,
                                                 daemon=True)
        self._preview_thread.start()
        print("[CCTV] Preview started.")

    def stop_preview(self):
        """Stop the preview loop."""
        self._preview_running = False
        if self._preview_thread:
            self._preview_thread.join(timeout=3)
            self._preview_thread = None
        print("[CCTV] Preview stopped.")

    def _preview_loop(self):
        """Simple loop: read frames and store for streaming."""
        while self._preview_running and not self._trip_running:
            with self._camera_lock:
                if self._camera is None or not self._camera.isOpened():
                    break
                ret, frame = self._camera.read()
            if ret:
                # Add a simple timestamp bar for preview
                self._add_preview_overlay(frame)
                with self._frame_lock:
                    self._latest_frame = frame
            time.sleep(1.0 / config.FPS_TARGET)

    def _add_preview_overlay(self, frame):
        """Add a simple 'STANDBY' overlay to preview frames."""
        h, w = frame.shape[:2]
        now = datetime.datetime.now()
        ts = now.strftime("%Y-%m-%d  %H:%M:%S")

        # Bottom bar
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, h - 32), (w, h), (0, 0, 0), cv2.FILLED)
        cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
        cv2.putText(frame, f"{config.CAMERA_LABEL}   {ts}", (8, h - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1,
                    cv2.LINE_AA)

        # STANDBY badge
        cv2.putText(frame, "STANDBY", (w - 110, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 100, 100), 2,
                    cv2.LINE_AA)

    # ── Trip Mode (Full Detection) ────────────────────────────────────────────

    def start_trip_mode(self, user_id, trip_session_id, on_alert_callback=None):
        """Start full detection pipeline."""
        if self._trip_running:
            return
        self._preview_running = False   # stop preview loop
        time.sleep(0.2)

        if not self.start_camera():
            return

        self._user_id = user_id
        self._trip_session_id = trip_session_id
        self._on_alert_callback = on_alert_callback
        self._trip_running = True

        # Load detection modules
        print("[CCTV] Loading detection modules...")
        self._detector = MotionDetector()
        self._pet_filter = PetFilter()
        if self._recognizer is None:
            self._recognizer = FaceRecognizer()
        self._alerter = AlertSystem()

        # Register capture callback
        set_capture_callback(self._handle_capture)

        self._trip_thread = threading.Thread(target=self._trip_loop, daemon=True)
        self._trip_thread.start()
        print("[CCTV] Trip mode ACTIVE — full detection running.")

    def stop_trip_mode(self):
        """Stop detection, go back to preview."""
        self._trip_running = False
        set_capture_callback(None)
        if self._trip_thread:
            self._trip_thread.join(timeout=5)
            self._trip_thread = None
        self._alerter = None
        self._detector = None
        self._pet_filter = None
        print("[CCTV] Trip mode stopped.")
        # Restart preview
        self.start_preview()

    def _trip_loop(self):
        """Full detection pipeline loop — mirrors main.py logic."""
        frame_counter = 0
        last_face_results = []

        while self._trip_running:
            with self._camera_lock:
                if self._camera is None or not self._camera.isOpened():
                    break
                ret, frame = self._camera.read()
            if not ret:
                time.sleep(0.05)
                continue

            # ── Detection pipeline ────────────────────────────────────────────
            contours, _ = self._detector.detect(frame)
            motion_detected = len(contours) > 0
            person_contours, pet_contours = self._pet_filter.filter(contours)

            # Draw motion boxes
            if config.SHOW_MOTION_BOXES:
                for c in person_contours:
                    x, y, bw, bh = self._pet_filter.get_bounding_box(c)
                    cv2.rectangle(frame, (x, y), (x+bw, y+bh),
                                  config.COLOR_HIGH, 2)
                for c in pet_contours:
                    x, y, bw, bh = self._pet_filter.get_bounding_box(c)
                    cv2.rectangle(frame, (x, y), (x+bw, y+bh),
                                  config.COLOR_LOW, 1)

            # Face recognition with frame skip
            clean_frame = frame.copy()
            frame_counter += 1
            run_face = (bool(person_contours) and
                        frame_counter % config.FACE_DETECT_EVERY_N_FRAMES == 0)

            if run_face:
                last_face_results = self._recognizer.identify_faces(frame)

            face_results = last_face_results

            if face_results:
                self._recognizer.draw_face_boxes(frame, face_results)

            if not person_contours:
                last_face_results = []

            # Alert evaluation
            alert_level, message = self._alerter.evaluate(
                clean_frame, frame, motion_detected,
                person_contours, face_results
            )

            # Add CCTV overlay
            self._add_trip_overlay(frame, alert_level, message)

            with self._frame_lock:
                self._latest_frame = frame

            time.sleep(1.0 / config.FPS_TARGET)

    def _add_trip_overlay(self, frame, alert_level, message):
        """Add monitoring overlay to trip mode frames."""
        h, w = frame.shape[:2]
        now = datetime.datetime.now()
        ts = now.strftime("%Y-%m-%d  %H:%M:%S")

        # Top status bar
        cv2.rectangle(frame, (0, 0), (w, 36), (20, 20, 20), cv2.FILLED)

        # Alert level colors
        colors = {
            "LOW": (0, 200, 0), "MEDIUM": (0, 165, 255),
            "HIGH": (0, 0, 255), "CRITICAL": (0, 0, 180)
        }
        color = colors.get(alert_level, (255, 255, 255))

        cv2.putText(frame, f"[{alert_level}] {message}", (8, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

        # MONITORING badge (green pulse)
        cv2.putText(frame, "MONITORING", (w - 140, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 100), 2,
                    cv2.LINE_AA)

        # Bottom timestamp bar
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, h - 32), (w, h), (0, 0, 0), cv2.FILLED)
        cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
        cv2.putText(frame, f"{config.CAMERA_LABEL}   {ts}", (8, h - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1,
                    cv2.LINE_AA)

        # Blinking REC
        if now.second % 2 == 0:
            cv2.circle(frame, (w - 45, h - 16), 6, (0, 0, 220), cv2.FILLED)
            cv2.putText(frame, "REC", (w - 35, h - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 220), 1,
                        cv2.LINE_AA)

    def _handle_capture(self, face_paths, body_path, alert_level):
        """Called when alert_system captures evidence. Triggers email alert."""
        print(f"[CCTV] Capture hook fired: {len(face_paths)} face(s), level={alert_level}")
        if self._on_alert_callback:
            self._on_alert_callback(
                self._user_id, self._trip_session_id,
                face_paths, body_path, alert_level
            )

    # ── Status ────────────────────────────────────────────────────────────────

    @property
    def is_camera_on(self):
        with self._camera_lock:
            return self._camera is not None and self._camera.isOpened()

    @property
    def is_trip_active(self):
        return self._trip_running

    @property
    def is_preview_active(self):
        return self._preview_running

    def shutdown(self):
        """Clean shutdown."""
        self._trip_running = False
        self._preview_running = False
        time.sleep(0.3)
        self.stop_camera()
        print("[CCTV] Shutdown complete.")


# Singleton
cctv_runner = CCTVRunner()
