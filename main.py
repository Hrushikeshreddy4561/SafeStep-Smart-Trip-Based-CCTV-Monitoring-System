# =============================================================================
# main.py — System Orchestrator  (v3 — Live CCTV Timestamp Edition)
# =============================================================================

import cv2
import sys
import datetime
import config

from motion_detection        import MotionDetector
from pet_filter              import PetFilter
from face_recognition_module import FaceRecognizer
from alert_system            import AlertSystem
from utils.helpers           import (ensure_dirs, cleanup_evidence, draw_text,
                                     get_alert_color, save_evidence, FPSCounter)


def draw_cctv_timestamp(frame):
    """
    Burn a real-CCTV-style timestamp bar onto the LIVE frame.

    Layout (bottom of frame):
    ┌─────────────────────────────────────────────────────┐
    │  CAM-01   2024-06-01   14:30:05          REC ●      │
    └─────────────────────────────────────────────────────┘

    - Semi-transparent dark bar (like real CCTV monitors)
    - Blinking REC indicator (toggles every second)
    - Camera ID, date, and time
    """
    h, w = frame.shape[:2]
    now  = datetime.datetime.now()

    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    cam_str  = config.CAMERA_LABEL      # e.g. "CAM-01"

    # ── Semi-transparent bottom bar ───────────────────────────────────────────
    bar_h   = 32
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, h - bar_h), (w, h), (0, 0, 0), cv2.FILLED)
    # 0.55 opacity — dark enough to read, still see the scene behind
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

    y = h - 8  # text baseline inside the bar

    # ── Camera label (left) ───────────────────────────────────────────────────
    cv2.putText(frame, cam_str, (8, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1, cv2.LINE_AA)

    # ── Date + Time (centre-left) ─────────────────────────────────────────────
    datetime_str = f"{date_str}   {time_str}"
    cv2.putText(frame, datetime_str, (100, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

    # ── Blinking REC indicator (right) ───────────────────────────────────────
    # Blinks once per second by checking if current second is even/odd
    if now.second % 2 == 0:
        # Red filled circle
        cv2.circle(frame, (w - 45, h - bar_h // 2), 6, (0, 0, 220), cv2.FILLED)
        cv2.putText(frame, "REC", (w - 35, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 220), 1, cv2.LINE_AA)

    return frame


def draw_overlay(frame, fps, alert_level, message, high_count, in_zone):
    """Draw the top status bar and threat level bar."""
    h, w  = frame.shape[:2]
    color = get_alert_color(alert_level)

    # ── Top status bar ────────────────────────────────────────────────────────
    cv2.rectangle(frame, (0, 0), (w, 44), (20, 20, 20), cv2.FILLED)

    if config.SHOW_FPS:
        draw_text(frame, f"FPS:{fps}", (8, 30), config.COLOR_INFO, scale=0.52)

    # Zone status
    zone_color = (0, 210, 0) if not in_zone else color
    zone_label = "ZONE: CLEAR" if not in_zone else "ZONE: OCCUPIED"
    draw_text(frame, zone_label, (95, 30), zone_color, scale=0.52)

    # Alert level + message
    if config.SHOW_ALERT_STATUS:
        draw_text(frame, f"[{alert_level}]  {message}", (275, 30), color, scale=0.52)

    # ── Threat escalation bar (above timestamp bar) ───────────────────────────
    if high_count > 0:
        bar_w = int((high_count / config.CRITICAL_THRESHOLD) * w)
        cv2.rectangle(frame, (0, h - 38), (bar_w, h - 32), config.COLOR_HIGH, cv2.FILLED)
        draw_text(frame, f"Threat: {high_count}/{config.CRITICAL_THRESHOLD}",
                  (w - 160, h - 35), config.COLOR_HIGH, scale=0.42, thickness=1)

    # ── Controls hint (sits just above the timestamp bar) ────────────────────
    video_keys = "  P=Pause  [/]=Speed" if isinstance(config.CAMERA_INDEX, str) else ""
    draw_text(frame, f"Q=Quit  R=Reset  A=Ack  S=Snap  F=Reload{video_keys}",
              (5, h - 36),
              config.COLOR_INFO, scale=0.36, thickness=1)

    return frame


def main():
    ensure_dirs()
    cleanup_evidence()   # auto-delete old evidence files per config limits

    print("[INFO] Initialising modules...")
    detector    = MotionDetector()
    pet_filter  = PetFilter()
    recognizer  = FaceRecognizer()
    alerter     = AlertSystem()
    fps_counter = FPSCounter()

    # ── Source detection: webcam vs video file ───────────────────────────────
    is_video_file = isinstance(config.CAMERA_INDEX, str) and \
                    not config.CAMERA_INDEX.startswith("rtsp")
    source_label  = "VIDEO" if is_video_file else "WEBCAM"

    print(f"[INFO] Opening {source_label}: {config.CAMERA_INDEX}...")
    cap = cv2.VideoCapture(config.CAMERA_INDEX)
    if not cap.isOpened():
        print(f"[ERROR] Cannot open {source_label}: {config.CAMERA_INDEX}.")
        sys.exit(1)

    if is_video_file:
        # Read native FPS from the file to compute correct frame delay
        native_fps  = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_delay = max(1, int((1000 / native_fps) / max(config.VIDEO_SPEED, 0.1)))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        print(f"[INFO] Video: {native_fps:.1f} fps  |  "
              f"{total_frames} frames  |  "
              f"speed x{config.VIDEO_SPEED}  |  "
              f"loop={'on' if config.VIDEO_LOOP else 'off'}")
        print("[INFO] Extra keys: P=Pause/Resume  [ =slow down  ] =speed up")
    else:
        frame_delay = 1
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  config.FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS,          config.FPS_TARGET)

    print("[INFO] System running. Press Q or ESC to quit.")
    print(f"[INFO] Evidence -> {config.EVIDENCE_DIR}")
    print(f"[INFO] Log      -> {config.ALERTS_LOG}")
    print("[INFO] Capture mode: ZONE ENTRY (one snapshot per entry event)\n")

    paused             = False
    video_speed        = config.VIDEO_SPEED
    _frame_counter     = 0       # increments every frame
    _last_face_results = []      # cached result from last InsightFace call

    while True:
        if paused:
            key = cv2.waitKey(50) & 0xFF
            if key in (ord('p'), ord('P')):
                paused = False
            elif key in (ord('q'), 27):
                break
            continue

        ret, frame = cap.read()
        if not ret:
            if is_video_file:
                if config.VIDEO_LOOP:
                    print("[INFO] Video ended — looping.")
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    detector.reset_background()   # fresh background for new loop
                    alerter.reset()
                    continue
                else:
                    print("[INFO] Video ended.")
                    break
            else:
                import time; time.sleep(0.05)
            continue

        # ── Detection pipeline ────────────────────────────────────────────────
        contours, _           = detector.detect(frame)
        motion_detected       = len(contours) > 0
        person_contours, pet_contours = pet_filter.filter(contours)

        # Bounding boxes
        if config.SHOW_MOTION_BOXES:
            for c in person_contours:
                x, y, bw, bh = pet_filter.get_bounding_box(c)
                cv2.rectangle(frame, (x, y), (x+bw, y+bh), config.COLOR_HIGH, 2)
            for c in pet_contours:
                x, y, bw, bh = pet_filter.get_bounding_box(c)
                cv2.rectangle(frame, (x, y), (x+bw, y+bh), config.COLOR_LOW, 1)

        # Face recognition — frame-skip optimisation
        # InsightFace (RetinaFace + ArcFace) is expensive on CPU.
        # We run it only every FACE_DETECT_EVERY_N_FRAMES frames and reuse
        # the last result in between. At 20 FPS with value=4, that is still
        # ~5 detections/sec — more than enough for zone entry capture.
        #
        # clean_frame is copied BEFORE drawing boxes so the saved face crop
        # has no bounding boxes or labels on it.
        clean_frame = frame.copy()

        _frame_counter += 1
        run_face_detect = (bool(person_contours) and
                           _frame_counter % config.FACE_DETECT_EVERY_N_FRAMES == 0)

        if run_face_detect:
            _last_face_results = recognizer.identify_faces(frame)

        face_results = _last_face_results

        # Always redraw boxes (even on skipped frames) using the cached result
        if face_results:
            recognizer.draw_face_boxes(frame, face_results)

        # Clear cache when no person is in frame so stale boxes don't persist
        if not person_contours:
            _last_face_results = []

        # Alert logic — pass clean (for face crop) AND annotated frame (for body)
        alert_level, message = alerter.evaluate(
            clean_frame, frame, motion_detected, person_contours, face_results
        )

        fps_counter.tick()

        # ── Draw overlays — ORDER MATTERS ─────────────────────────────────────
        # 1. Status bar + threat bar (top + near-bottom)
        frame = draw_overlay(frame, fps_counter.get(),
                             alert_level, message,
                             alerter.high_alert_count,
                             alerter.in_zone)

        # 2. CCTV timestamp bar (very bottom — drawn last so it's always visible)
        frame = draw_cctv_timestamp(frame)

        cv2.imshow("Intelligent CCTV Surveillance", frame)

        # ── Keyboard controls ─────────────────────────────────────────────────
        key = cv2.waitKey(frame_delay) & 0xFF
        if key in (ord('q'), 27):
            break
        elif key == ord('r'):
            detector.reset_background()
            print("[INFO] Background reset.")
        elif key == ord('a'):
            alerter.reset()
        elif key == ord('s'):
            from alert_system import save_evidence_timestamped
            path = save_evidence_timestamped(frame, "MANUAL", "operator")
            print(f"[INFO] Manual snapshot: {path}")
        elif key == ord('f'):
            recognizer.reload_faces()
        # Video-only controls
        elif key in (ord('p'), ord('P')) and is_video_file:
            paused = not paused
            print(f"[INFO] {'Paused' if paused else 'Resumed'}.")
        elif key == ord(']') and is_video_file:
            video_speed = min(video_speed + 0.25, 4.0)
            native_fps  = cap.get(cv2.CAP_PROP_FPS) or 25.0
            frame_delay = max(1, int((1000 / native_fps) / video_speed))
            print(f"[INFO] Speed: x{video_speed:.2f}")
        elif key == ord('[') and is_video_file:
            video_speed = max(video_speed - 0.25, 0.25)
            native_fps  = cap.get(cv2.CAP_PROP_FPS) or 25.0
            frame_delay = max(1, int((1000 / native_fps) / video_speed))
            print(f"[INFO] Speed: x{video_speed:.2f}")

    cap.release()
    cv2.destroyAllWindows()
    print("[INFO] Shut down cleanly.")


if __name__ == "__main__":
    main()