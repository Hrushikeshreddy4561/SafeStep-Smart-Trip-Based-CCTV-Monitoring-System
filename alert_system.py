# =============================================================================
# alert_system.py  (v8)
#
# ROOT CAUSE OF v7 MULTIPLE CAPTURES
# ====================================
# v7 used a frame-counter (_out_of_zone_frames >= 10) to detect zone exits.
# A sitting person on a bean bag generates body motion that constantly
# flickers in/out of the entry zone bounding check. Each flicker that
# crossed 10 frames called _mark_zone_exit(), which:
#   1. Set _last_zone_exit_time = now (exit clock restarted)
#   2. Reset _capture_done_this_entry = False (capture lock released)
# So within 30s the person could: enter → capture → flicker-exit → flicker-
# re-enter → capture again. The 30s timer was being measured from the
# most recent micro-flicker, not from the real departure.
#
# v8 FIX — TIME-BASED EXIT DETECTION
# =====================================
# Instead of counting frames, we track WHEN the person was last seen in zone.
# Exit is only confirmed after they have been continuously absent for
# ABSENCE_GRACE_SEC seconds (default 3s from config).
# This is immune to frame-by-frame flicker.
#
#   _last_seen_in_zone  : timestamp of last frame where person was in zone
#   _session_exit_time  : timestamp when exit was confirmed (after grace)
#   _session_captured   : True once FACE+BODY saved for current session
#
# SESSION LIFECYCLE:
#   1. Person enters zone → session starts
#   2. Unknown face confirmed → save FACE+BODY once → _session_captured=True
#   3. Person leaves zone → after ABSENCE_GRACE_SEC, exit confirmed
#      → _session_exit_time recorded, _session_captured reset
#   4. Person re-enters → check (now - _session_exit_time) >= ABSENCE_TIMEOUT
#      → Yes (>= 30s): new session, capture again
#      → No  (< 30s): same visit, no capture
#
# FACE IMAGE FIX
# ==============
# The face crop is saved from `clean_frame` — a copy of the frame taken
# BEFORE draw_face_boxes() is called in main.py. This means no bounding
# boxes or "Unknown" labels appear on the saved face image.
# main.py must pass clean_frame to alerter.evaluate() — see main.py.
# =============================================================================

import time
import os
import datetime

import cv2
import numpy as np

import config
from utils.helpers import readable_time


# ─── Geometry ────────────────────────────────────────────────────────────────

def _contours_in_entry_zone(person_contours, frame_width):
    """True if ANY person contour centre-X is inside the entry zone."""
    zone_x = frame_width * config.ENTRY_ZONE_END_X
    for c in person_contours:
        x, y, w, h = cv2.boundingRect(c)
        if (x + w / 2.0) < zone_x:
            return True
    return False


# ─── Image savers ────────────────────────────────────────────────────────────

def _burn_bar(image, label):
    """Semi-transparent timestamp + label bar at bottom of image."""
    snap  = image.copy()
    h, w  = snap.shape[:2]
    ts    = datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    bar_h = 36
    ov    = snap.copy()
    cv2.rectangle(ov, (0, h - bar_h), (w, h), (0, 0, 0), cv2.FILLED)
    cv2.addWeighted(ov, 0.6, snap, 0.4, 0, snap)
    cv2.putText(snap, ts, (8, h - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    tw = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0][0]
    cv2.putText(snap, label, (w - tw - 8, h - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
    return snap


def _save_pair(clean_frame, annotated_frame, alert_level, face_boxes):
    """
    Save two evidence files sharing the same timestamp:

      FACE_<level>_unknown_<ts>.jpg
        Clean face crop from clean_frame (no bounding boxes, no labels).

      BODY_<level>_unknown_<ts>.jpg
        Full annotated frame (with bounding boxes) + timestamp bar.
        Shows the complete body and room context.

    Returns (face_path, body_path).
    """
    os.makedirs(config.EVIDENCE_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    def fp(prefix):
        return os.path.join(config.EVIDENCE_DIR,
                            f"{prefix}_{alert_level}_unknown_{ts}.jpg")

    # ── FACE: crop from the CLEAN frame (no drawn boxes) ─────────────────────
    face_path = None
    if face_boxes and clean_frame is not None:
        fh, fw = clean_frame.shape[:2]

        def area(b):
            t, r, b2, l = b
            return max(0, b2 - t) * max(0, r - l)

        top, right, bottom, left = max(face_boxes, key=area)
        pad    = config.FACE_CROP_PADDING
        top    = max(0,  top    - pad)
        left   = max(0,  left   - pad)
        bottom = min(fh, bottom + pad)
        right  = min(fw, right  + pad)

        crop = clean_frame[top:bottom, left:right]
        if crop.size == 0 or crop.shape[0] < 20 or crop.shape[1] < 20:
            crop = clean_frame

        face_path = fp("FACE")
        cv2.imwrite(face_path, crop)   # clean — no text overlaid

    # ── BODY: full annotated frame + timestamp bar ────────────────────────────
    body_path = fp("BODY")
    cv2.imwrite(body_path,
                _burn_bar(annotated_frame, f"BODY | {alert_level} | unknown"))

    return face_path, body_path


def save_evidence_timestamped(frame, alert_level, name="", face_boxes=None):
    """Manual snapshot via 'S' key in main.py."""
    os.makedirs(config.EVIDENCE_DIR, exist_ok=True)
    ts   = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    name = name.replace(" ", "_") if name else "manual"
    fp   = os.path.join(config.EVIDENCE_DIR, f"{alert_level}_{name}_{ts}.jpg")
    cv2.imwrite(fp, _burn_bar(frame, f"{alert_level} | {name}"))
    return fp


# ─── Main Alert System ────────────────────────────────────────────────────────

class AlertSystem:
    """
    v8 — Time-based zone exit, bulletproof single capture per session.

    Key state variables:
      _last_seen_in_zone  : last time person/face was detected in entry zone
      _session_exit_time  : when exit was confirmed (after ABSENCE_GRACE_SEC)
      _session_captured   : True once image pair saved for this session
      _session_active     : True while person is in zone
    """

    def __init__(self):
        self._current_level     = config.ALERT_LOW
        self._log_file          = config.ALERTS_LOG

        # Time-based zone tracking
        self._last_seen_in_zone = 0.0
        self._session_active    = False
        self._session_start     = 0.0

        # Session capture state
        # _session_exit_time starts far enough in the past so first entry qualifies
        self._session_exit_time = time.time() - config.ABSENCE_TIMEOUT - 1
        self._session_captured  = False

        # Escalation display counter
        self._high_count        = 0

        # Grace timer for known-person suppression
        self._last_familiar_seen = 0.0

        self._ensure_log()

    # ─────────────────────────────────────────────────────────────────────────

    def _ensure_log(self):
        os.makedirs(os.path.dirname(self._log_file), exist_ok=True)
        with open(self._log_file, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"  SESSION STARTED: {readable_time()}\n")
            f.write(f"{'='*60}\n")

    def _log(self, level, message, path=None):
        line = f"[{readable_time()}] [{level:8s}] {message}"
        if path:
            line += f"  -> {path}"
        try:
            with open(self._log_file, 'a', encoding='utf-8') as f:
                f.write(line + "\n")
        except Exception as e:
            print(f"[ERROR] log: {e}")

    # ─────────────────────────────────────────────────────────────────────────

    def evaluate(self, clean_frame, annotated_frame,
                 motion_detected, person_contours, face_results):
        """
        Called every frame.

        Parameters
        ----------
        clean_frame     : frame BEFORE draw_face_boxes() — used for face crop
        annotated_frame : frame AFTER draw_face_boxes() — used for body image
        motion_detected : bool
        person_contours : list of contours from MotionDetector
        face_results    : list of face dicts from FaceRecognizer

        Returns (alert_level, message)
        """
        now  = time.time()
        h, w = annotated_frame.shape[:2]

        # ── Is person currently in entry zone? ────────────────────────────────
        faces_in_zone  = any(f.get("in_zone") for f in face_results)
        motion_in_zone = (bool(person_contours) and
                          _contours_in_entry_zone(person_contours, w))
        person_in_zone_now = faces_in_zone or motion_in_zone

        if person_in_zone_now:
            self._last_seen_in_zone = now   # heartbeat

        # ── Check for confirmed zone exit (time-based, not frame-based) ───────
        time_since_seen = now - self._last_seen_in_zone
        exit_confirmed  = (self._session_active and
                           time_since_seen >= config.ABSENCE_GRACE_SEC)

        if exit_confirmed:
            duration = int(now - self._session_start)
            self._log(config.ALERT_LOW,
                      f"Zone exit confirmed (present {duration}s, "
                      f"absent {time_since_seen:.1f}s >= "
                      f"{config.ABSENCE_GRACE_SEC}s grace)")
            self._session_exit_time = self._last_seen_in_zone  # exit = last seen
            self._session_active    = False
            self._session_captured  = False
            self._high_count        = 0
            self._current_level     = config.ALERT_LOW

        # ── No person at all ──────────────────────────────────────────────────
        if not motion_detected or not person_contours:
            self._current_level = config.ALERT_LOW
            return config.ALERT_LOW, "No person detected"

        # ── Person in frame but not in entry zone ─────────────────────────────
        if not person_in_zone_now and not self._session_active:
            self._current_level = config.ALERT_LOW
            return config.ALERT_LOW, "Person outside entry zone"

        # ── No face detected ──────────────────────────────────────────────────
        if not face_results:
            familiar_age = now - self._last_familiar_seen
            if familiar_age < config.KNOWN_FACE_GRACE_SEC:
                self._current_level = config.ALERT_MEDIUM
                return config.ALERT_MEDIUM, "Known person nearby (face not visible)"
            self._high_count += 1
            level = (config.ALERT_CRITICAL
                     if self._high_count >= config.CRITICAL_THRESHOLD
                     else config.ALERT_HIGH)
            self._current_level = level
            return level, "Person in zone - face not visible (no capture)"

        # ── Face(s) detected — classify ───────────────────────────────────────
        has_familiar = any(f['familiar'] for f in face_results)
        has_unknown  = any(not f['familiar'] for f in face_results)

        if has_familiar:
            self._last_familiar_seen = now

        # ── Only known faces → MEDIUM, log once, no images ───────────────────
        if has_familiar and not has_unknown:
            self._high_count    = 0
            self._current_level = config.ALERT_MEDIUM
            names   = [f['name'] for f in face_results if f['familiar']]
            message = f"Familiar: {', '.join(names)}"
            if not self._session_active:
                self._session_active = True
                self._session_start  = now
                self._log(config.ALERT_MEDIUM,
                          f"ZONE ENTRY (known - no image): {message}")
                print(f"[ZONE ENTRY - known]  {readable_time()}  {message}")
            return config.ALERT_MEDIUM, message

        # ── Unknown face → HIGH / CRITICAL ────────────────────────────────────
        if has_unknown:
            self._high_count += 1
            level = (config.ALERT_CRITICAL
                     if self._high_count >= config.CRITICAL_THRESHOLD
                     else config.ALERT_HIGH)
            self._current_level = level

            # Start session on first detection
            if not self._session_active:
                self._session_active = True
                self._session_start  = now

            # ── Capture gate ──────────────────────────────────────────────────
            # Conditions (ALL must be true):
            #   1. Not already captured this session
            #   2. Person was absent long enough before this session
            if not self._session_captured:
                absent = now - self._session_exit_time

                if absent >= config.ABSENCE_TIMEOUT:
                    # ✓ Capture
                    self._session_captured = True
                    unknown_boxes = [f['box'] for f in face_results
                                     if not f['familiar']]
                    face_path, body_path = _save_pair(
                        clean_frame, annotated_frame, level, unknown_boxes
                    )
                    self._log(level, f"CAPTURED FACE: {face_path}")
                    self._log(level, f"CAPTURED BODY: {body_path}")
                    print(f"\n{'!'*50}")
                    print(f"  {level} - {readable_time()}")
                    print(f"  Absent {int(absent)}s >= {config.ABSENCE_TIMEOUT}s")
                    print(f"  FACE -> {face_path}")
                    print(f"  BODY -> {body_path}")
                    print(f"{'!'*50}\n")

                else:
                    # ✗ Re-entry too soon — block and suppress for rest of session
                    secs_left = int(config.ABSENCE_TIMEOUT - absent)
                    msg = (f"Re-entry too soon: absent {int(absent)}s "
                           f"(need {config.ABSENCE_TIMEOUT}s, "
                           f"{secs_left}s remaining) - no capture")
                    print(f"[{msg}]")
                    self._log(level, msg)
                    self._session_captured = True  # prevent repeated log spam

            return level, "UNKNOWN PERSON DETECTED"

        self._current_level = config.ALERT_LOW
        return config.ALERT_LOW, "Monitoring"

    # ── Accessors ─────────────────────────────────────────────────────────────

    @property
    def current_level(self):
        return self._current_level

    @property
    def high_alert_count(self):
        return self._high_count

    @property
    def in_zone(self):
        return self._session_active

    def reset(self):
        now = time.time()
        self._session_active    = False
        self._session_captured  = False
        self._session_exit_time = now - config.ABSENCE_TIMEOUT - 1
        self._last_seen_in_zone = 0.0
        self._last_familiar_seen = 0.0
        self._high_count        = 0
        self._current_level     = config.ALERT_LOW
        print("[INFO] Alert system reset.")