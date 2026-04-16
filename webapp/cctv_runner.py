"""
cctv_runner.py — Background CCTV Engine Thread (v3 — Lag-Free)
Runs the CCTV surveillance pipeline in a background thread.
The web app controls start/stop via trip mode toggle.

Performance Architecture:
  - Single camera loop thread that stays running across mode switches
  - Mode flag flips instantly — no thread stop/start during transitions
  - FaceRecognizer loaded once and kept alive permanently
  - Camera stays open between preview↔trip toggles
  - Buffer drain only when camera is first opened or restarted
"""

import sys
import os
import threading
import time
import cv2
import numpy as np
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
from alert_system import AlertSystem


def _camera_sources_from_config():
    sources = getattr(config, 'CAMERA_SOURCES', None)
    if isinstance(sources, (list, tuple)) and len(sources) > 0:
        return list(sources)
    return [config.CAMERA_INDEX]


def _camera_label(index):
    return f"cam{index + 1}"


def _is_video_file_source(source):
    if not isinstance(source, str):
        return False
    lowered = source.lower()
    return not lowered.startswith(("rtsp://", "http://", "https://"))


def _source_display_name(source):
    if _is_video_file_source(source):
        return os.path.basename(source) or source
    return str(source)


class CCTVRunner:
    """
    Manages the CCTV surveillance engine in a background thread.

    Uses a SINGLE camera loop thread that switches between two modes:
      1. PREVIEW — Camera is on, shows live feed, no detection.
      2. TRIP MODE — Camera + full detection pipeline (motion, faces, alerts).

    Mode switches are instant flag flips — no thread teardown/recreation needed.
    """

    # Operating modes
    MODE_OFF     = 0
    MODE_PREVIEW = 1
    MODE_TRIP    = 2

    def __init__(self, camera_source=None, camera_id=0, camera_label=None):
        self.camera_id = camera_id
        self.camera_source = config.CAMERA_INDEX if camera_source is None else camera_source
        self.camera_label = camera_label or _camera_label(camera_id)
        self._logical_camera_id = self.camera_id
        self._logical_camera_label = self.camera_label
        self._is_video_file = _is_video_file_source(self.camera_source)
        self._source_display = _source_display_name(self.camera_source)
        self._source_kind = "video_file" if self._is_video_file else "live_camera"
        self._native_fps = float(config.FPS_TARGET)

        self._camera = None
        self._camera_lock = threading.Lock()
        self._loop_thread = None
        self._loop_running = False
        self._mode = self.MODE_OFF
        self._mode_lock = threading.Lock()

        self._latest_frame = None
        self._frame_lock = threading.Lock()
        self._trip_session_id = None
        self._user_id = None
        self._on_alert_callback = None
        self._on_familiar_seen_callback = None
        self._familiar_emit_cache = {}
        self._last_face_results = []
        self._face_thread = None
        self._warmup_thread = None
        self._warmup_lock = threading.Lock()

        # Detection modules — loaded once and reused
        self._detector = None
        self._pet_filter = None
        self._recognizer = None      # expensive; loaded once, kept forever
        self._alerter = None
        self._modules_loaded = False  # True after first trip start

        # Target frame interval (seconds)
        self._frame_interval = 1.0 / config.FPS_TARGET

        # ── Video-file performance tuning ─────────────────────────────────
        # Process detection pipeline every Nth frame for video file sources.
        # Skipped frames are still displayed (smooth playback) but not analyzed.
        self._detect_every_n = (
            max(1, int(getattr(config, 'VIDEO_DETECT_EVERY_N_FRAMES', 3)))
            if self._is_video_file else 1
        )
        # Scale factor for motion detection input (video files only).
        self._detection_scale = (
            float(getattr(config, 'VIDEO_DETECTION_SCALE', 0.5))
            if self._is_video_file else 1.0
        )
        # Persist last alert state across skipped frames so overlay stays correct.
        self._last_alert_level = "LOW"
        self._last_alert_message = "Monitoring"

    # ── Camera Management ─────────────────────────────────────────────────────

    def _drain_buffer(self):
        """Discard stale frames from OpenCV internal buffer."""
        with self._camera_lock:
            if self._camera and self._camera.isOpened():
                for _ in range(5):
                    self._camera.grab()

    def _refresh_frame_interval(self):
        """Set playback cadence for live cameras vs recorded video files."""
        if self._is_video_file:
            speed = max(float(getattr(config, "VIDEO_SPEED", 1.0)), 0.1)
            min_fps = float(getattr(config, "VIDEO_MIN_FPS", 5.0))
            # Use native_fps when available; otherwise ensure a reasonable floor
            computed = float(self._native_fps or 0.0) * speed
            target_fps = max(min_fps, computed)
            if computed < min_fps:
                print(f"[CCTV:{self.camera_label}] Warning: video native FPS ({self._native_fps}) * speed ({speed}) = {computed:.2f} is below VIDEO_MIN_FPS ({min_fps}). Using {min_fps} FPS for playback.")
        else:
            target_fps = max(float(config.FPS_TARGET), 1.0)
        self._frame_interval = 1.0 / target_fps

    def _rewind_video(self):
        """Restart a recorded video source from the beginning."""
        with self._camera_lock:
            if self._camera is None or not self._camera.isOpened():
                return False
            # Try direct rewind first. Some backends/codecs don't support this
            # reliably; if it fails, fall back to closing and re-opening the
            # capture to ensure the loop continues.
            try:
                ok = bool(self._camera.set(cv2.CAP_PROP_POS_FRAMES, 0))
            except Exception:
                ok = False

            if ok:
                return True

            # Fallback: release and re-open the video file source
            source = self.camera_source
            try:
                self._camera.release()
            except Exception:
                pass

            self._camera = cv2.VideoCapture(source)
            if not self._camera or not self._camera.isOpened():
                print(f"[CCTV:{self.camera_label}] Failed to rewind or reopen video source: {source}")
                self._camera = None
                return False

            # Reconfigure native fps and frame interval for the reopened capture
            native_fps = self._camera.get(cv2.CAP_PROP_FPS) or config.FPS_TARGET
            self._native_fps = float(native_fps) if native_fps and native_fps > 1 else float(config.FPS_TARGET)
            self._refresh_frame_interval()

            # Drain a few frames to avoid stale buffers
            for _ in range(5):
                try:
                    self._camera.grab()
                except Exception:
                    break

            print(f"[CCTV:{self.camera_label}] Reopened video source for loop: {self._source_display}")
            return True

    def _handle_stream_end(self):
        """Loop recorded files cleanly so preview and trip mode stay alive."""
        if not self._is_video_file:
            return False

        if not getattr(config, "VIDEO_LOOP", False):
            with self._mode_lock:
                self._mode = self.MODE_OFF
            return False

        rewound = self._rewind_video()
        if rewound:
            print(f"[CCTV:{self.camera_label}] Rewinding looped video: {self._source_display}")
            self._last_face_results = []
            if self._detector:
                self._detector.reset_background()
            if self._alerter:
                self._alerter.reset()
        return rewound

    def start_camera(self):
        """Open the camera hardware."""
        with self._camera_lock:
            if self._camera is not None and self._camera.isOpened():
                return True
            self._camera = cv2.VideoCapture(self.camera_source)
            if not self._camera.isOpened():
                print(f"[CCTV:{self.camera_label}] ERROR: Cannot open camera source {self.camera_source}")
                self._camera = None
                return False
            if not self._is_video_file:
                self._camera.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_WIDTH)
                self._camera.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_HEIGHT)
                self._camera.set(cv2.CAP_PROP_FPS, config.FPS_TARGET)
            # Minimise internal buffer for lowest latency
            self._camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if self._is_video_file:
                native_fps = self._camera.get(cv2.CAP_PROP_FPS) or config.FPS_TARGET
                self._native_fps = float(native_fps) if native_fps and native_fps > 1 else float(config.FPS_TARGET)
            else:
                self._native_fps = float(config.FPS_TARGET)
            self._refresh_frame_interval()
            source_mode = "looped video" if self._is_video_file else "live source"
            print(f"[CCTV:{self.camera_label}] Camera opened ({source_mode}: {self._source_display}).")
        self._drain_buffer()
        return True

    def stop_camera(self):
        """Release the camera hardware."""
        with self._camera_lock:
            if self._camera is not None:
                self._camera.release()
                self._camera = None
                print(f"[CCTV:{self.camera_label}] Camera released.")
        with self._frame_lock:
            self._latest_frame = None
        self._last_face_results = []

    def _read_frame(self):
        """Read a single frame from the camera. Returns (ret, frame)."""
        with self._camera_lock:
            if self._camera is None or not self._camera.isOpened():
                return False, None
            return self._camera.read()

    def get_frame(self):
        """Get the latest frame as JPEG bytes for MJPEG streaming."""
        with self._frame_lock:
            if self._latest_frame is not None:
                ret, jpeg = cv2.imencode('.jpg', self._latest_frame,
                                         [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ret:
                    return jpeg.tobytes()
        return None

    # ── Unified Camera Loop ───────────────────────────────────────────────────

    def _start_loop(self):
        """Start the single camera loop thread if not running."""
        if self._loop_running:
            return
        self._loop_running = True
        self._loop_thread = threading.Thread(target=self._camera_loop, daemon=True)
        self._loop_thread.start()

    def _stop_loop(self):
        """Stop the camera loop thread."""
        self._loop_running = False
        if self._loop_thread:
            self._loop_thread.join(timeout=3)
            self._loop_thread = None

    def _camera_loop(self):
        """
        Single loop that handles BOTH preview and trip modes.
        Mode switches are checked every frame — no thread restart needed.

        Performance optimizations for video file sources:
          - Frame skipping: detection runs only every Nth frame
          - Downscale: motion detection on smaller frame (4× fewer pixels)
          - Lazy copy: frame.copy() only when face detection actually runs
          - Skipped frames still display with the last known alert overlay
          - Drift-compensated sleep: exact-rate playback for smoother video
        """
        frame_counter = 0
        last_face_results = []
        expected_time = time.monotonic()

        while self._loop_running:
            try:
                expected_time += self._frame_interval

                # Check current mode
                with self._mode_lock:
                    current_mode = self._mode

                if current_mode == self.MODE_OFF:
                    time.sleep(0.05)
                    expected_time = time.monotonic()
                    continue

                ret, frame = self._read_frame()
                if not ret:
                    if self._handle_stream_end():
                        frame_counter = 0
                        last_face_results = []
                    else:
                        time.sleep(0.02)
                    expected_time = time.monotonic()
                    continue

                if current_mode == self.MODE_PREVIEW:
                    # ── Preview: just overlay and store ───────────────────────
                    self._add_preview_overlay(frame)
                    last_face_results = []
                    self._last_face_results = []
                    frame_counter = 0

                elif current_mode == self.MODE_TRIP:
                    # ── Trip: full detection pipeline ─────────────────────────
                    detector = self._detector
                    pet_filter = self._pet_filter
                    recognizer = self._recognizer
                    alerter = self._alerter

                    if not detector or not pet_filter or not recognizer or not alerter:
                        # Shouldn't happen, but guard against it
                        self._add_preview_overlay(frame)
                    else:
                        frame_counter += 1

                        # ── Frame skipping for video files ────────────────────
                        # On skipped frames: just overlay last known state and store for streaming.
                        if (self._detect_every_n > 1 and
                                frame_counter % self._detect_every_n != 0):
                            self._add_trip_overlay(
                                frame,
                                self._last_alert_level,
                                self._last_alert_message,
                            )
                        else:
                            # ── Resize for faster motion detection (video only) ──
                            scale = self._detection_scale
                            if scale < 1.0:
                                det_frame = cv2.resize(
                                    frame, (0, 0), fx=scale, fy=scale,
                                    interpolation=cv2.INTER_AREA,
                                )
                            else:
                                det_frame = frame

                            contours, _ = detector.detect(det_frame)

                            # Scale contours back to original resolution
                            if scale < 1.0 and contours:
                                inv = 1.0 / scale
                                scaled = []
                                for c in contours:
                                    scaled.append(
                                        (c.astype(np.float64) * inv).astype(np.int32)
                                    )
                                contours = scaled

                            motion_detected = len(contours) > 0
                            person_contours, pet_contours = pet_filter.filter(contours)

                            # Draw motion boxes
                            if config.SHOW_MOTION_BOXES:
                                for c in person_contours:
                                    x, y, bw, bh = pet_filter.get_bounding_box(c)
                                    cv2.rectangle(frame, (x, y), (x+bw, y+bh),
                                                  config.COLOR_HIGH, 2)
                                for c in pet_contours:
                                    x, y, bw, bh = pet_filter.get_bounding_box(c)
                                    cv2.rectangle(frame, (x, y), (x+bw, y+bh),
                                                  config.COLOR_LOW, 1)

                            # Lazy frame.copy(): only copy when person is detected
                            clean_frame = frame.copy() if person_contours else frame

                            # Face recognition with frame skip and background threading
                            if self._is_video_file:
                                run_face = bool(person_contours)
                            else:
                                run_face = (bool(person_contours) and
                                            frame_counter % config.FACE_DETECT_EVERY_N_FRAMES == 0)

                            if run_face:
                                face_thread = self._face_thread
                                if face_thread is None or not face_thread.is_alive():
                                    # Start face recog in a background thread to prevent camera stutter
                                    frame_copy = frame.copy()

                                    def _run_recog():
                                        try:
                                            self._last_face_results = recognizer.identify_faces(frame_copy)
                                        except Exception as e:
                                            self._last_face_results = []
                                            print(f"[CCTV:{self.camera_label}] Face recognition error: {e}")

                                    self._face_thread = threading.Thread(target=_run_recog, daemon=True)
                                    self._face_thread.start()

                            face_results = getattr(self, '_last_face_results', [])
                            last_face_results = face_results

                            if face_results:
                                if config.SHOW_FACE_BOXES:
                                    recognizer.draw_face_boxes(frame, face_results)
                                self._emit_familiar_seen(face_results)

                            if not person_contours:
                                last_face_results = []
                                self._last_face_results = []

                            # Alert evaluation
                            alert_level, message = alerter.evaluate(
                                clean_frame, frame, motion_detected,
                                person_contours, face_results
                            )

                            # Persist alert state for skipped-frame overlays
                            self._last_alert_level = alert_level
                            self._last_alert_message = message

                            # Trip overlay
                            self._add_trip_overlay(frame, alert_level, message)

                # Store frame for streaming
                with self._frame_lock:
                    self._latest_frame = frame

                # Drift-compensated sleep
                now = time.monotonic()
                sleep_amount = expected_time - now
                if sleep_amount > 0.002:
                    time.sleep(sleep_amount)
                elif sleep_amount < -1.0:
                    # If we fell more than 1 second behind, reset the clock
                    expected_time = now

            except Exception as e:
                print(f"[CCTV:{self.camera_label}] Camera loop error: {e}")
                time.sleep(0.05)
                expected_time = time.monotonic()

    # ── Preview Mode ──────────────────────────────────────────────────────────

    def start_preview(self):
        """Start camera preview without detection."""
        if self._mode == self.MODE_PREVIEW:
            return
        if not self.start_camera():
            return
        self._last_face_results = []
        with self._mode_lock:
            self._mode = self.MODE_PREVIEW
        self._start_loop()
        print(f"[CCTV:{self.camera_label}] Preview started.")

    def stop_preview(self):
        """Stop the preview (does NOT release camera)."""
        with self._mode_lock:
            if self._mode == self.MODE_PREVIEW:
                self._mode = self.MODE_OFF
        print(f"[CCTV:{self.camera_label}] Preview stopped.")

    # ── Trip Mode ─────────────────────────────────────────────────────────────

    def _ensure_modules_loaded(self):
        """Load detection modules. FaceRecognizer is loaded once and kept."""
        if self._modules_loaded and self._recognizer is not None:
            # Re-create only the per-trip state modules (fast)
            self._detector = MotionDetector()
            self._pet_filter = PetFilter()
            self._alerter = AlertSystem(
                on_capture_callback=self._handle_capture,
                camera_label=self._logical_camera_label,
            )
            return

        print(f"[CCTV:{self.camera_label}] Loading detection modules...")
        self._detector = MotionDetector()
        self._pet_filter = PetFilter()
        self._alerter = AlertSystem(
            on_capture_callback=self._handle_capture,
            camera_label=self._logical_camera_label,
        )

        if self._recognizer is None:
            # This is the slow call (~2-4s) — only happens once per app run
            self._recognizer = FaceRecognizer()

        self._modules_loaded = True
        print(f"[CCTV:{self.camera_label}] Modules ready.")

    def preload_recognizer(self):
        """Warm the expensive recognizer before trip mode is enabled."""
        with self._warmup_lock:
            if self._recognizer is not None:
                return True
            try:
                print(f"[CCTV:{self.camera_label}] Preloading face recognizer...")
                self._recognizer = FaceRecognizer()
                print(f"[CCTV:{self.camera_label}] Face recognizer ready.")
                return True
            except Exception as e:
                print(f"[CCTV:{self.camera_label}] Recognizer preload failed: {e}")
                return False

    def preload_recognizer_async(self):
        """Start recognizer warm-up in the background if needed."""
        if self._recognizer is not None:
            return
        if self._warmup_thread and self._warmup_thread.is_alive():
            return

        self._warmup_thread = threading.Thread(
            target=self.preload_recognizer,
            daemon=True,
            name=f"{self.camera_label}-recognizer-warmup"
        )
        self._warmup_thread.start()

    def start_trip_mode(self, user_id, trip_session_id, on_alert_callback=None):
        """Start full detection pipeline. Returns True on success, False on failure."""
        if self._mode == self.MODE_TRIP:
            return True

        if not self.start_camera():
            return False

        self._user_id = user_id
        self._trip_session_id = trip_session_id
        self._on_alert_callback = on_alert_callback
        self._familiar_emit_cache = {}
        self._last_face_results = []

        # Load/reload detection modules
        self._ensure_modules_loaded()
        if self._alerter:
            self._alerter.set_capture_callback(self._handle_capture)
            self._alerter.set_camera_label(self._logical_camera_label)

        # Instant mode switch — no thread restart needed!
        with self._mode_lock:
            self._mode = self.MODE_TRIP
        self._start_loop()

        print(f"[CCTV:{self.camera_label}] Trip mode ACTIVE.")
        return True

    def stop_trip_mode(self):
        """Stop trip mode and return to preview mode."""
        if self._alerter:
            self._alerter.set_capture_callback(None)

        # Return to preview mode so camera stays available in dashboard.
        with self._mode_lock:
            self._mode = self.MODE_PREVIEW

        # Ensure loop/camera are running in case of unexpected state.
        if not self.is_camera_on:
            self.start_camera()
        self._start_loop()

        # Keep modules allocated to avoid race with the loop thread.
        # Fresh per-trip state is rebuilt in _ensure_modules_loaded() on next start.
        self._familiar_emit_cache = {}
        self._last_face_results = []

        print(f"[CCTV:{self.camera_label}] Trip mode stopped -> Preview.")

    # ── Overlays ──────────────────────────────────────────────────────────────

    def _add_preview_overlay(self, frame):
        """Add a simple 'STANDBY' overlay to preview frames."""
        h, w = frame.shape[:2]
        now = datetime.datetime.now()
        ts = now.strftime("%Y-%m-%d  %H:%M:%S")

        # Bottom bar
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, h - 32), (w, h), (0, 0, 0), cv2.FILLED)
        cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
        cv2.putText(frame, f"{self.camera_label}   {ts}", (8, h - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1,
                    cv2.LINE_AA)

        # STANDBY badge
        cv2.putText(frame, "STANDBY", (w - 110, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 100, 100), 2,
                    cv2.LINE_AA)
        if self._is_video_file:
            cv2.putText(frame, "VIDEO LOOP", (w - 135, h - 42),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 220, 140), 1,
                        cv2.LINE_AA)

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

        # MONITORING badge
        cv2.putText(frame, "MONITORING", (w - 140, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 100), 2,
                    cv2.LINE_AA)

        # Bottom timestamp bar
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, h - 32), (w, h), (0, 0, 0), cv2.FILLED)
        cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)
        cv2.putText(frame, f"{self.camera_label}   {ts}", (8, h - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1,
                    cv2.LINE_AA)

        # Blinking REC
        if now.second % 2 == 0:
            cv2.circle(frame, (w - 45, h - 16), 6, (0, 0, 220), cv2.FILLED)
            cv2.putText(frame, "REC", (w - 35, h - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 220), 1,
                        cv2.LINE_AA)

    def _handle_capture(self, face_paths, body_path, alert_level, familiar_names=None):
        """Called when alert_system captures evidence. Triggers email alert."""
        print(f"[CCTV:{self.camera_label}] Capture hook fired: {len(face_paths)} face(s), level={alert_level}")
        if self._on_alert_callback:
            try:
                self._on_alert_callback(
                    self._user_id, self._trip_session_id,
                    face_paths, body_path, alert_level, familiar_names or [],
                    self._logical_camera_id, self._logical_camera_label
                )
            except TypeError:
                self._on_alert_callback(
                    self._user_id, self._trip_session_id,
                    face_paths, body_path, alert_level, familiar_names or []
                )

    def set_familiar_seen_callback(self, callback):
        """Set callback(user_id, names) for known-face camera sightings."""
        self._on_familiar_seen_callback = callback

    def _emit_familiar_seen(self, face_results):
        """Emit familiar names with simple per-name throttling to avoid DB spam."""
        if not self._on_familiar_seen_callback or self._user_id is None:
            return

        now = time.time()
        names_to_emit = []
        for face in face_results:
            if not face.get('familiar'):
                continue
            name = (face.get('name') or '').strip()
            if not name:
                continue
            last_ts = self._familiar_emit_cache.get(name, 0)
            if now - last_ts >= 10:
                names_to_emit.append(name)
                self._familiar_emit_cache[name] = now

        if names_to_emit:
            try:
                self._on_familiar_seen_callback(
                    self._user_id, names_to_emit, self._logical_camera_id, self._logical_camera_label
                )
            except TypeError:
                self._on_familiar_seen_callback(self._user_id, names_to_emit)

    def set_logical_camera_identity(self, camera_id, camera_label):
        """Set logical camera identity used for callbacks when sources are shared."""
        self._logical_camera_id = camera_id
        self._logical_camera_label = camera_label
        if self._alerter:
            self._alerter.set_camera_label(camera_label)

    def source_info(self):
        """Describe how this logical camera is backed."""
        if self._is_video_file:
            return {
                "source": str(self.camera_source),
                "source_display": self._source_display,
                "source_kind": self._source_kind,
                "source_badge": "Looped video",
                "source_note": f"Recorded playback from {self._source_display}",
            }
        return {
            "source": str(self.camera_source),
            "source_display": self._source_display,
            "source_kind": self._source_kind,
            "source_badge": "Live camera",
            "source_note": f"Live input source {self._source_display}",
        }

    # ── Status ────────────────────────────────────────────────────────────────

    @property
    def is_camera_on(self):
        with self._camera_lock:
            return self._camera is not None and self._camera.isOpened()

    @property
    def is_trip_active(self):
        return self._mode == self.MODE_TRIP

    @property
    def is_preview_active(self):
        return self._mode == self.MODE_PREVIEW

    def reload_known_faces(self, rebuild_cache=True):
        """Reload known face embeddings if recognizer is already initialized."""
        if self._recognizer is not None:
            self._recognizer.reload_faces(rebuild_cache=rebuild_cache)

    def shutdown(self):
        """Clean shutdown."""
        with self._mode_lock:
            self._mode = self.MODE_OFF
        self._stop_loop()
        self.stop_camera()
        print(f"[CCTV:{self.camera_label}] Shutdown complete.")


class MultiCameraManager:
    """Owns one CCTVRunner per configured camera source."""

    def __init__(self):
        self._runners = {}
        self._source_to_runner = {}
        self._camera_to_runner = {}

        for idx, source in enumerate(_camera_sources_from_config()):
            source_key = str(source)
            runner = self._source_to_runner.get(source_key)
            if runner is None:
                runner = CCTVRunner(
                    camera_source=source,
                    camera_id=idx,
                    camera_label=_camera_label(idx)
                )
                self._source_to_runner[source_key] = runner
            self._camera_to_runner[idx] = runner
            self._runners[idx] = runner

    def _unique_runners(self):
        return list({id(r): r for r in self._camera_to_runner.values()}.values())

    @property
    def primary_camera_id(self):
        return min(self._runners.keys()) if self._runners else 0

    def get_runner(self, camera_id=None):
        if camera_id is None:
            camera_id = self.primary_camera_id
        return self._camera_to_runner.get(camera_id)

    def list_cameras(self):
        return [
            {
                'id': camera_id,
                'label': _camera_label(camera_id),
                **runner.source_info(),
            }
            for camera_id, runner in sorted(self._runners.items())
        ]

    def get_camera_statuses(self):
        return [
            {
                'id': camera_id,
                'label': _camera_label(camera_id),
                'camera_on': runner.is_camera_on,
                'trip_active': runner.is_trip_active,
                'preview_active': runner.is_preview_active,
                **runner.source_info(),
            }
            for camera_id, runner in sorted(self._runners.items())
        ]

    def labels_for_camera(self, camera_id=None):
        """Return logical camera labels that share the same physical source."""
        if camera_id is None:
            camera_id = self.primary_camera_id
        runner = self._camera_to_runner.get(camera_id)
        if runner is None:
            return []

        source_key = str(runner.camera_source)
        labels = []
        for logical_id, logical_runner in sorted(self._camera_to_runner.items()):
            if str(logical_runner.camera_source) == source_key:
                labels.append(_camera_label(logical_id))
        return labels

    @property
    def is_camera_on(self):
        return any(r.is_camera_on for r in self._unique_runners())

    @property
    def is_trip_active(self):
        return any(r.is_trip_active for r in self._unique_runners())

    @property
    def is_preview_active(self):
        return any(r.is_preview_active for r in self._unique_runners())

    def reload_known_faces(self):
        runners = self._unique_runners()
        if not runners:
            return
        runners[0].reload_known_faces(rebuild_cache=True)
        for runner in runners[1:]:
            runner.reload_known_faces(rebuild_cache=False)

    def preload_recognizers_async(self):
        for runner in self._unique_runners():
            runner.preload_recognizer_async()

    def get_frame(self, camera_id=None):
        runner = self.get_runner(camera_id)
        return runner.get_frame() if runner else None

    def start_preview_all(self):
        threads = []
        for runner in self._unique_runners():
            t = threading.Thread(target=runner.start_preview, daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        self.preload_recognizers_async()

    def stop_all(self):
        for runner in self._unique_runners():
            runner.stop_preview()
            runner._stop_loop()
            runner.stop_camera()

    def set_familiar_seen_callback_all(self, callback):
        for runner in self._unique_runners():
            runner.set_familiar_seen_callback(callback)

    def start_trip_all(self, user_id, trip_session_id, on_alert_callback=None):
        return self.start_trip_selected(
            user_id,
            trip_session_id,
            selected_camera_ids=sorted(self._camera_to_runner.keys()),
            on_alert_callback=on_alert_callback,
        )

    def start_trip_selected(self, user_id, trip_session_id,
                            selected_camera_ids=None, on_alert_callback=None):
        started = []
        failed_ids = []
        runner_to_camera_ids = {}
        for camera_id, runner in self._camera_to_runner.items():
            runner_to_camera_ids.setdefault(id(runner), []).append(camera_id)

        selected_set = set(selected_camera_ids or [])
        if not selected_set:
            return False

        worker_threads = []
        started_lock = threading.Lock()

        def _start_for_runner(runner, chosen_id, selected_for_runner):
            runner.set_logical_camera_identity(chosen_id, _camera_label(chosen_id))
            ok = runner.start_trip_mode(
                user_id,
                trip_session_id,
                on_alert_callback=on_alert_callback
            )
            with started_lock:
                if ok:
                    started.append(runner)
                else:
                    failed_ids.extend(selected_for_runner)

        for runner in self._unique_runners():
            logical_ids = runner_to_camera_ids.get(id(runner), [])
            selected_for_runner = [cid for cid in logical_ids if cid in selected_set]
            if not selected_for_runner:
                # Keep non-selected cameras in preview mode.
                runner.stop_trip_mode()
                continue

            chosen_id = selected_for_runner[0]
            t = threading.Thread(
                target=_start_for_runner,
                args=(runner, chosen_id, selected_for_runner),
                daemon=True
            )
            t.start()
            worker_threads.append(t)

        for t in worker_threads:
            t.join()

        if failed_ids:
            print(f"[CCTV] Trip mode unavailable for camera IDs: {failed_ids}")

        return len(started) > 0

    def stop_trip_all(self):
        for runner in self._unique_runners():
            runner.stop_trip_mode()

    def shutdown(self):
        for runner in self._unique_runners():
            runner.shutdown()


# Singleton manager + backward-compatible primary runner alias
cctv_manager = MultiCameraManager()
cctv_runner = cctv_manager.get_runner()
