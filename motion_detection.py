# =============================================================================
# motion_detection.py — Background Subtraction Motion Detector
#
# HOW IT WORKS:
#   1. We maintain a "background model" — a running average of recent frames.
#   2. Each new frame is compared against this model.
#   3. Pixels that differ significantly are marked as "changed" (foreground).
#   4. We find contiguous regions of change (contours) and return them.
#
# WHY RUNNING AVERAGE INSTEAD OF FRAME DIFFERENCING?
#   Frame differencing compares two consecutive frames, so slow-moving objects
#   can go undetected. A running average adapts gradually, giving more stable
#   results across lighting changes.
# =============================================================================

import cv2
import numpy as np
import config


class MotionDetector:
    """
    Detects motion by comparing each frame against an adaptive background model.
    """

    def __init__(self):
        # background_model stores the long-term average of frames as float32
        # (None until the first frame arrives)
        self.background_model = None
        # Warm-up counter: suppress detections for the first N frames so the
        # background model can stabilise (eliminates startup false positives).
        self._warmup_count = 0

    def _initialize_background(self, frame_gray):
        """Set the initial background to the very first gray frame."""
        self.background_model = frame_gray.astype(np.float32)

    def detect(self, frame):
        """
        Analyse one frame for motion.

        Parameters
        ----------
        frame : np.ndarray
            Raw BGR frame from OpenCV.

        Returns
        -------
        contours : list
            List of OpenCV contours representing motion regions.
            Empty list means no significant motion was found.
        fg_mask : np.ndarray
            Binary (0/255) foreground mask — useful for debugging / display.
        """
        # ── Step 1: Pre-process ──────────────────────────────────────────────
        gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray  = cv2.GaussianBlur(gray, config.GAUSSIAN_BLUR_SIZE, 0)
        # Blur removes high-frequency noise (dust, camera grain) so we don't
        # react to every tiny pixel change.

        # ── Step 2: Initialise background on first frame ─────────────────────
        if self.background_model is None:
            self._initialize_background(gray)
            return [], np.zeros_like(gray)

        # ── Step 2b: Warm-up — accumulate background, return no motion ────────
        # During warm-up the model is learning what "empty scene" looks like.
        # Any detections here are almost certainly glass reflections or sensor
        # noise, so we suppress them entirely.
        if self._warmup_count < config.BACKGROUND_WARMUP_FRAMES:
            self._warmup_count += 1
            cv2.accumulateWeighted(gray, self.background_model,
                                   config.BACKGROUND_ACCUM_WEIGHT)
            return [], np.zeros_like(gray)

        # ── Step 3: Compute absolute difference from background ──────────────
        diff = cv2.absdiff(self.background_model.astype(np.uint8), gray)

        # ── Step 4: Threshold — pixels above MOTION_THRESHOLD → white (255) ──
        _, fg_mask = cv2.threshold(diff, config.MOTION_THRESHOLD, 255,
                                   cv2.THRESH_BINARY)

        # ── Step 5: Morphological cleanup ────────────────────────────────────
        # Dilation then erosion (closing) fills small holes in detected blobs
        kernel   = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        fg_mask  = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)
        fg_mask  = cv2.dilate(fg_mask, kernel, iterations=2)

        # ── Step 6: Find contours (connected motion blobs) ───────────────────
        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)

        # ── Step 7: Filter out tiny specks ───────────────────────────────────
        significant = [c for c in contours
                       if cv2.contourArea(c) >= config.MIN_CONTOUR_AREA]

        # ── Step 8: Update background model (weighted running average) ───────
        # BACKGROUND_ACCUM_WEIGHT controls how fast the background adapts.
        # A small value (0.05) means the background changes slowly — good for
        # detecting stationary intruders. A higher value reacts to lighting changes faster.
        cv2.accumulateWeighted(gray, self.background_model,
                               config.BACKGROUND_ACCUM_WEIGHT)

        return significant, fg_mask

    def reset_background(self):
        """Force a background reset (e.g., after a lighting change)."""
        self.background_model = None
        self._warmup_count    = 0   # restart warmup so reflections don't re-trigger