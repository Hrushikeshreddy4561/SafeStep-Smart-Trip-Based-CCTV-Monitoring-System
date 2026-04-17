# =============================================================================
# config.py — Central Configuration File
# All system-wide settings are defined here.
# To customize the system, edit values in this file only.
# =============================================================================

import os

# ─── PATHS ────────────────────────────────────────────────────────────────────
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
KNOWN_FACES_DIR  = os.path.join(BASE_DIR, "known_faces")
EVIDENCE_DIR     = os.path.join(BASE_DIR, "evidence")
ALERTS_LOG       = os.path.join(BASE_DIR, "alerts", "alert_log.txt")
EMBEDDINGS_CACHE = os.path.join(BASE_DIR, "embeddings.pkl")

# ─── CAMERA ───────────────────────────────────────────────────────────────────
# CAMERA_INDEX options:
#   0                         -> default webcam
#   1, 2 ...                  -> other connected cameras
#   "rtsp://..."              -> IP/CCTV stream
#   "C:/path/to/clip.mp4"    -> recorded video file
CAMERA_INDEX  = 0

# For parallel multi-camera processing, define a list of sources.
# If left empty/undefined, the system falls back to CAMERA_INDEX.
# Example:
# CAMERA_SOURCES = [0, 1]
# CAMERA_SOURCES = ["rtsp://cam-1", "rtsp://cam-2"]
# Using [0, 0] mirrors the same physical camera in two windows.
CAMERA_SOURCES = [0, os.path.join(BASE_DIR, "test3.mp4")]

FRAME_WIDTH   = 640
FRAME_HEIGHT  = 480
FPS_TARGET    = 20

# --- VIDEO FILE MODE ----------------------------------------------------------
# Only used when CAMERA_INDEX is a file path string, not a webcam integer.
#
# VIDEO_LOOP  True  -> restart from beginning when video ends
#             False -> exit cleanly when video ends
VIDEO_LOOP  = True

# VIDEO_SPEED 1.0 -> real-time   2.0 -> double speed   0.5 -> half speed
VIDEO_SPEED = 1.0

# Minimum playback FPS for recorded video sources. Some backends report
# extremely low or zero FPS which causes the loop to fall back to 1 FPS.
# Raising this to 5 or 10 gives a usable preview for looped test clips.
VIDEO_MIN_FPS = 5.0

# ─── VIDEO FILE DETECTION PERFORMANCE ─────────────────────────────────────
# Process detection pipeline every Nth frame for video file sources.
# Frames NOT processed are still displayed smoothly (no visual stutter).
# 1 = every frame (slow), 3 = every 3rd frame (good balance), 5 = very fast
VIDEO_DETECT_EVERY_N_FRAMES = 3

# Scale factor for motion detection input (video files only).
# 0.5 = half resolution → 4x fewer pixels → ~2x faster motion detection.
# Live cameras are NOT affected by this setting.
VIDEO_DETECTION_SCALE = 0.5

# ─── MOTION DETECTION ─────────────────────────────────────────────────────────
MOTION_THRESHOLD        = 25
MIN_CONTOUR_AREA        = 2000   # raised: 500 was too small, picked up glass reflections
GAUSSIAN_BLUR_SIZE      = (21, 21)
BACKGROUND_ACCUM_WEIGHT = 0.05

# ─── BACKGROUND WARM-UP ───────────────────────────────────────────────────────
# Ignore motion for this many frames on startup so the background model
# can stabilise before we start reacting.  30 frames ≈ 1.5 s at 20 FPS.
BACKGROUND_WARMUP_FRAMES = 45

# ─── PET FILTER ───────────────────────────────────────────────────────────────
PET_MAX_WIDTH  = 120
PET_MAX_HEIGHT = 120
PET_MAX_AREA   = 8000

# ─── FACE RECOGNITION (InsightFace — RetinaFace + ArcFace) ───────────────────
#
# FACE_RECOGNITION_TOLERANCE
#   Cosine similarity threshold for ArcFace embeddings.
#   Range: 0.0 – 1.0  (higher = stricter)
#   Recommended: 0.45 – 0.55
FACE_RECOGNITION_TOLERANCE = 0.45

# INSIGHTFACE_DET_SIZE
#   (320, 320) → faster, good for normal CCTV
#   (640, 640) → more accurate, higher CPU cost
INSIGHTFACE_DET_SIZE = (320, 320)

# FACE_DETECT_EVERY_N_FRAMES
#   Run InsightFace only once every N frames instead of every frame.
#   This is the single biggest FPS improvement for CPU-only systems.
#
#   1  → every frame   (high accuracy, heavy CPU — original behaviour)
#   3  → every 3rd     (good balance for 20 FPS target)
#   5  → every 5th     (very light — fine for slow-moving scenes)
#
#   At 20 FPS, value=4 means face detection runs ~5 times per second —
#   more than enough to catch anyone entering the zone.
FACE_DETECT_EVERY_N_FRAMES = 4

# ─── FACE CROP EVIDENCE (Fix 1) ───────────────────────────────────────────────
#
# FACE_CROP_PADDING
#   Pixels of padding added around the face bounding box when cropping.
#   50px gives enough context (forehead, chin, shoulders) without too much
#   background clutter.
#   Increase if faces are being cropped too tightly.
FACE_CROP_PADDING = 50

# ─── OPTION D: ZONE-ENTRY + RE-ENTRY CAPTURE ──────────────────────────────────
ENTRY_ZONE_END_X  = 1.0    # Full 100% of frame = entry zone (for testing)
ABSENCE_TIMEOUT   = 30     # Seconds person must be GONE before re-capture
ABSENCE_GRACE_SEC = 3      # Seconds without detection before marking GONE

# ─── GRACE TIMER — Known Person Face Not Visible (Fix 2) ─────────────────────
#
# KNOWN_FACE_GRACE_SEC
#   If a familiar face was confirmed within this many seconds, suppress
#   HIGH/CRITICAL alerts when the face is temporarily not visible
#   (person turned away, crouching, partially occluded).
#
#   300 = 5 minutes grace period.
#   If the person is still around after 5 min without showing their face,
#   the system escalates — this is unusual behaviour worth flagging.
#
#   Set lower (e.g. 120) for stricter security.
#   Set higher (e.g. 600) if your known person often has back to camera.
KNOWN_FACE_GRACE_SEC = 60

# ─── EVIDENCE CLEANUP ─────────────────────────────────────────────────────────
# Files older than this many days are deleted automatically on startup.
# Set to 0 to disable age-based cleanup.
EVIDENCE_MAX_AGE_DAYS  = 30

# If the evidence folder exceeds this size (MB), oldest files are deleted
# until it fits. Set to 0 to disable size-based cleanup.
EVIDENCE_MAX_SIZE_MB   = 500

# ─── ALERT SYSTEM ─────────────────────────────────────────────────────────────
ALERT_LOW      = "LOW"
ALERT_MEDIUM   = "MEDIUM"
ALERT_HIGH     = "HIGH"
ALERT_CRITICAL = "CRITICAL"

CRITICAL_THRESHOLD = 3     # Frames of unknown presence before CRITICAL
ALERT_COOLDOWN_SEC = 3
NO_FACE_WAIT_SEC   = 4.0   # Seconds to wait for a face before settling for a BODY-ONLY capture

# ─── DISPLAY ──────────────────────────────────────────────────────────────────
SHOW_MOTION_BOXES = False
SHOW_FACE_BOXES   = False
SHOW_FPS          = True
SHOW_ALERT_STATUS = True

# ─── COLORS (BGR format for OpenCV) ───────────────────────────────────────────
COLOR_LOW      = (0,   0,   255)   # Green   — familiar face
COLOR_MEDIUM   = (0,   165, 255)   # Orange
COLOR_HIGH     = (0,     0, 255)   # Red     — unknown face
COLOR_CRITICAL = (0,     0, 180)   # Dark Red
COLOR_INFO     = (255, 255, 255)   # White

# ─── CAMERA LABEL ─────────────────────────────────────────────────────────────
CAMERA_LABEL = "CAM-01"
