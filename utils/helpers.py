# =============================================================================
# utils/helpers.py — Shared Utility Functions
# Small helper functions reused across multiple modules.
# =============================================================================

import os
import cv2
import time
import datetime
import config

def ensure_dirs():
    """Create required directories if they don't already exist."""
    for d in [config.KNOWN_FACES_DIR, config.EVIDENCE_DIR,
              os.path.dirname(config.ALERTS_LOG)]:
        os.makedirs(d, exist_ok=True)


def cleanup_evidence():
    """
    Auto-clean the evidence folder on startup.
    Two passes, both controlled by config.py settings:

    Pass 1 — Age-based  (EVIDENCE_MAX_AGE_DAYS)
        Delete any .jpg/.png older than N days.
        Set EVIDENCE_MAX_AGE_DAYS = 0 to skip.

    Pass 2 — Size-based  (EVIDENCE_MAX_SIZE_MB)
        If folder still exceeds the size limit after age cleanup,
        delete the OLDEST files first until it fits.
        Set EVIDENCE_MAX_SIZE_MB = 0 to skip.

    Prints a summary of what was deleted.
    Called once from main.py at startup (inside ensure_dirs → main).
    """
    if not os.path.isdir(config.EVIDENCE_DIR):
        return

    image_exts = (".jpg", ".jpeg", ".png")

    def _get_files():
        files = []
        for fn in os.listdir(config.EVIDENCE_DIR):
            if fn.lower().endswith(image_exts):
                fp = os.path.join(config.EVIDENCE_DIR, fn)
                files.append((fp, os.path.getmtime(fp), os.path.getsize(fp)))
        return sorted(files, key=lambda x: x[1])   # oldest first

    deleted = 0
    freed   = 0   # bytes

    # ── Pass 1: age-based ─────────────────────────────────────────────────────
    if config.EVIDENCE_MAX_AGE_DAYS > 0:
        cutoff = time.time() - config.EVIDENCE_MAX_AGE_DAYS * 86400
        for fp, mtime, size in _get_files():
            if mtime < cutoff:
                try:
                    os.remove(fp)
                    deleted += 1
                    freed   += size
                except OSError:
                    pass

    # ── Pass 2: size-based ────────────────────────────────────────────────────
    if config.EVIDENCE_MAX_SIZE_MB > 0:
        limit_bytes = config.EVIDENCE_MAX_SIZE_MB * 1024 * 1024
        files = _get_files()
        total = sum(s for _, _, s in files)
        for fp, _, size in files:
            if total <= limit_bytes:
                break
            try:
                os.remove(fp)
                deleted += 1
                freed   += size
                total   -= size
            except OSError:
                pass

    if deleted:
        freed_mb = freed / (1024 * 1024)
        print(f"[CLEANUP] Removed {deleted} old evidence file(s) "
              f"({freed_mb:.1f} MB freed).")
    else:
        # Count remaining files for info
        remaining = len(_get_files())
        if remaining:
            total_mb = sum(s for _, _, s in _get_files()) / (1024 * 1024)
            print(f"[CLEANUP] Evidence folder: {remaining} file(s), "
                  f"{total_mb:.1f} MB (within limits).")

def timestamp_str():
    """Return a filesystem-safe timestamp string e.g. 2024-06-01_14-30-05"""
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

def readable_time():
    """Return a human-readable timestamp string."""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def save_evidence(frame, alert_level):
    """
    Save a JPEG snapshot of the current frame into the evidence folder.
    Filename encodes the alert level and timestamp for easy sorting.
    Returns the saved file path.
    """
    filename = f"{alert_level}_{timestamp_str()}.jpg"
    filepath = os.path.join(config.EVIDENCE_DIR, filename)
    cv2.imwrite(filepath, frame)
    return filepath

def draw_text(frame, text, position, color=config.COLOR_INFO, scale=0.6, thickness=2):
    """
    Convenience wrapper for cv2.putText.
    Adds a dark shadow under the text so it is readable on any background.
    """
    x, y = position
    # Shadow (slightly offset, dark)
    cv2.putText(frame, text, (x+1, y+1),
                cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness + 1)
    # Main text
    cv2.putText(frame, text, (x, y),
                cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness)

def get_alert_color(alert_level):
    """Map an alert level string to its BGR display color."""
    mapping = {
        config.ALERT_LOW:      config.COLOR_LOW,
        config.ALERT_MEDIUM:   config.COLOR_MEDIUM,
        config.ALERT_HIGH:     config.COLOR_HIGH,
        config.ALERT_CRITICAL: config.COLOR_CRITICAL,
    }
    return mapping.get(alert_level, config.COLOR_INFO)

class FPSCounter:
    """
    Simple rolling-average FPS counter.
    Usage:
        fps = FPSCounter()
        while True:
            fps.tick()
            print(fps.get())
    """
    def __init__(self, window=30):
        self._times  = []
        self._window = window  # average over last N frames

    def tick(self):
        self._times.append(time.time())
        if len(self._times) > self._window:
            self._times.pop(0)

    def get(self):
        if len(self._times) < 2:
            return 0.0
        elapsed = self._times[-1] - self._times[0]
        return round((len(self._times) - 1) / elapsed, 1) if elapsed > 0 else 0.0