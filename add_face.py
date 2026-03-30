#!/usr/bin/env python3
# =============================================================================
# add_face.py — Register a Known Face  (v2 — Optimised)
#
# IMPROVEMENTS OVER v1
# ====================
# 1. InsightFace loads ONCE at startup — not separately per function.
#    The model takes ~3s to load; doing it twice per run was wasteful.
#
# 2. Blur detection — Laplacian variance catches blurry captures before
#    saving them. A blurry reference photo is the #1 cause of false
#    "Unknown" results at runtime.
#
# 3. Best-of-3 capture — on SPACE, captures 3 quick frames and picks the
#    sharpest one. Eliminates motion blur from the button-press itself.
#
# 4. Quality score display — shows face width, sharpness, and detection
#    confidence live in the preview so you know before you shoot.
#
# 5. --test mode — compares a test photo / webcam capture against ALL
#    known faces and prints cosine similarity scores. Use this to tune
#    FACE_RECOGNITION_TOLERANCE in config.py.
#    Run:  python add_face.py --test
#          python add_face.py --test --image your_photo.jpg
#
# USAGE
# -----
#   python add_face.py                   ← webcam capture
#   python add_face.py --image path.jpg  ← import existing photo
#   python add_face.py --test            ← tolerance calibration (webcam)
#   python add_face.py --test --image p  ← tolerance calibration (photo)
# =============================================================================

import cv2
import os
import sys
import pickle
import argparse
import numpy as np
import config
from utils.helpers import ensure_dirs

try:
    from insightface.app import FaceAnalysis
    INSIGHTFACE_AVAILABLE = True
except ImportError:
    INSIGHTFACE_AVAILABLE = False
    print("[WARN] insightface not installed.")
    print("[WARN] Run: pip install insightface onnxruntime")


# ─── Blur metric ──────────────────────────────────────────────────────────────

def _sharpness(img):
    """
    Laplacian variance — higher = sharper.
    < 80  : noticeably blurry, avoid saving
    80-200: acceptable
    > 200 : good / sharp
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _sharpness_label(score):
    if score < 80:
        return "BLURRY", (0, 0, 255)
    if score < 200:
        return "OK", (0, 165, 255)
    return "SHARP", (0, 200, 0)


# ─── Model loader (called ONCE in main) ───────────────────────────────────────

def _load_insightface():
    """Load InsightFace once. Returns app or None if not available."""
    if not INSIGHTFACE_AVAILABLE:
        return None
    print("[INFO] Loading InsightFace model...")
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(320, 320))
    print("[INFO] Model ready.\n")
    return app


# ─── Validate + save ──────────────────────────────────────────────────────────

def _validate_and_save(app, img, name):
    """
    Ensure exactly one clear face, check sharpness, then save to known_faces/.
    Returns True on success.
    """
    sharp = _sharpness(img)
    label, _ = _sharpness_label(sharp)

    if sharp < 80:
        print(f"  [WARN] Image is blurry (sharpness={sharp:.0f}). "
              f"Move closer or hold still and try again.")
        return False

    if app is not None:
        faces = app.get(img)
        if not faces:
            print("  [WARN] No face detected — move closer or improve lighting.")
            return False
        if len(faces) > 1:
            print(f"  [WARN] {len(faces)} faces detected — one person at a time please.")
            return False

        face = faces[0]
        bbox = face.bbox.astype(int)
        fw, fh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        print(f"  Face size    : {fw}x{fh}px")
        print(f"  Sharpness    : {sharp:.0f}  ({label})")
        print(f"  Det. score   : {face.det_score:.3f}")

        if fw < 60 or fh < 60:
            print("  [WARN] Face is very small — move closer for better accuracy.")

    dest = os.path.join(config.KNOWN_FACES_DIR,
                        f"{name.lower().replace(' ', '_')}.jpg")
    cv2.imwrite(dest, img)
    print(f"  [SAVED] {dest}")

    # Clear embedding cache so system re-encodes on next load / F-key reload
    if os.path.exists(config.EMBEDDINGS_CACHE):
        os.remove(config.EMBEDDINGS_CACHE)
        print("  [INFO] Embedding cache cleared — system will re-encode on next start.")

    return True


# ─── Webcam capture ───────────────────────────────────────────────────────────

def capture_from_webcam(name, app):
    """
    Live preview with guide box and quality HUD.
    SPACE  → best-of-3 capture (picks sharpest of 3 quick frames)
    ESC    → cancel
    """
    print(f"\n[INFO] Webcam capture for: {name}")
    print("[INFO] Centre your face in the green box.")
    print("[INFO]  SPACE = capture   ESC = cancel\n")

    haar = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    cap = cv2.VideoCapture(config.CAMERA_INDEX)
    if not cap.isOpened():
        print("[ERROR] Cannot open webcam.")
        return False

    saved       = False
    frame_count = 0
    haar_boxes  = []
    live_sharp  = 0.0

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        frame_count += 1
        disp  = frame.copy()
        h, w  = disp.shape[:2]
        cx, cy = w // 2, h // 2

        # Haar every 3 frames for smooth face indicator
        if frame_count % 3 == 0:
            gray      = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            haar_boxes = haar.detectMultiScale(gray, 1.1, 5, minSize=(60, 60))
            live_sharp = _sharpness(frame)

        for (x, y, bw, bh) in haar_boxes:
            cv2.rectangle(disp, (x, y), (x+bw, y+bh), (255, 100, 0), 1)

        # Guide box
        cv2.rectangle(disp, (cx-110, cy-130), (cx+110, cy+130), (0, 255, 0), 2)
        cv2.putText(disp, "Centre face in box",
                    (cx - 90, cy - 145),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 0), 1)

        # Quality HUD
        s_label, s_color = _sharpness_label(live_sharp)
        face_count = len(haar_boxes)
        f_color    = (0, 200, 0) if face_count == 1 else (0, 100, 255)

        cv2.putText(disp, f"Faces: {face_count}",
                    (10, h - 52), cv2.FONT_HERSHEY_SIMPLEX, 0.48, f_color, 1)
        cv2.putText(disp, f"Sharp: {live_sharp:.0f}  {s_label}",
                    (10, h - 32), cv2.FONT_HERSHEY_SIMPLEX, 0.48, s_color, 1)
        cv2.putText(disp, "SPACE=Capture   ESC=Cancel",
                    (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (200, 200, 0), 1)

        cv2.imshow("Add Known Face", disp)
        key = cv2.waitKey(1) & 0xFF

        if key == 27:
            print("[INFO] Cancelled.")
            break

        elif key == 32:
            # Best-of-3: grab 3 quick frames, pick the sharpest
            print("[INFO] Capturing (best-of-3 frames)...")
            candidates = []
            for _ in range(3):
                ok, f = cap.read()
                if ok:
                    candidates.append((f, _sharpness(f)))

            if not candidates:
                print("  [WARN] Failed to read frame — try again.")
                continue

            best_frame, best_sharp = max(candidates, key=lambda x: x[1])
            print(f"  Best sharpness: {best_sharp:.0f}")

            saved = _validate_and_save(app, best_frame, name)
            if saved:
                break
            # Validation failed — keep preview open for retry

    cap.release()
    cv2.destroyAllWindows()
    return saved


# ─── Import from file ─────────────────────────────────────────────────────────

def import_from_file(name, image_path, app):
    """Validate and copy an existing image into known_faces/."""
    if not os.path.isfile(image_path):
        print(f"[ERROR] File not found: {image_path}")
        return False

    ext = os.path.splitext(image_path)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".bmp"):
        print("[ERROR] Supported formats: .jpg  .jpeg  .png  .bmp")
        return False

    img = cv2.imread(image_path)
    if img is None:
        print(f"[ERROR] Could not read image: {image_path}")
        return False

    return _validate_and_save(app, img, name)


# ─── Tolerance test / calibration mode ───────────────────────────────────────

def run_tolerance_test(app, image_path=None):
    """
    Compare a test photo against ALL known embeddings and print cosine
    similarity scores. Use this to find the right FACE_RECOGNITION_TOLERANCE.

    HOW TO USE:
      1. Run: python add_face.py --test
      2. Look at the score for YOUR face (should be highest).
      3. Set FACE_RECOGNITION_TOLERANCE to ~0.05 BELOW that score.
         E.g. if your score is 0.52, set tolerance = 0.47.
      4. Confirm strangers score below your new tolerance.
    """
    if app is None:
        print("[ERROR] InsightFace not available — cannot run test.")
        return

    # Load known embeddings
    if not os.path.exists(config.EMBEDDINGS_CACHE):
        print("[ERROR] No embedding cache found.")
        print("        Add at least one known face first, then run the main system")
        print("        once (or press F) so the cache is built.")
        return

    with open(config.EMBEDDINGS_CACHE, "rb") as f:
        data = pickle.load(f)
    known_embeddings = data["embeddings"]
    known_names      = data["names"]

    if not known_names:
        print("[ERROR] Embedding cache is empty.")
        return

    print("\n" + "="*55)
    print("  TOLERANCE CALIBRATION MODE")
    print("="*55)
    print(f"  Known faces loaded: {', '.join(known_names)}")
    print(f"  Current tolerance : {config.FACE_RECOGNITION_TOLERANCE}")
    print("="*55 + "\n")

    # Get test image
    if image_path:
        img = cv2.imread(image_path)
        if img is None:
            print(f"[ERROR] Cannot read: {image_path}")
            return
        test_faces = app.get(img)
    else:
        # Capture from webcam
        print("[INFO] Show your face to the webcam. Press SPACE to test, ESC to quit.")
        cap = cv2.VideoCapture(config.CAMERA_INDEX)
        test_faces = None
        img        = None

        while True:
            ret, frame = cap.read()
            if not ret:
                continue
            cv2.putText(frame, "SPACE=Test this frame   ESC=Quit",
                        (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
            cv2.imshow("Tolerance Test", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                cap.release()
                cv2.destroyAllWindows()
                return
            elif key == 32:
                img        = frame.copy()
                test_faces = app.get(frame)
                break

        cap.release()
        cv2.destroyAllWindows()

    if not test_faces:
        print("[WARN] No face detected in test image.")
        return

    if len(test_faces) > 1:
        print(f"[WARN] {len(test_faces)} faces found — using the largest.")

    test_face      = max(test_faces,
                         key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]))
    test_embedding = test_face.normed_embedding

    print("  Cosine similarity scores (higher = closer match):")
    print("-"*55)

    scores = []
    for name, emb in zip(known_names, known_embeddings):
        sim = float(np.dot(test_embedding, emb))
        scores.append((name, sim))
        match = "✓ MATCH" if sim >= config.FACE_RECOGNITION_TOLERANCE else "✗ no match"
        print(f"  {name:<20} {sim:.4f}   [{match}]")

    print("-"*55)
    best_name, best_score = max(scores, key=lambda x: x[1])
    print(f"  Best match : {best_name}  ({best_score:.4f})")
    print(f"  Tolerance  : {config.FACE_RECOGNITION_TOLERANCE}")

    # Tuning advice
    print("\n  TUNING ADVICE:")
    if best_score < config.FACE_RECOGNITION_TOLERANCE:
        gap = config.FACE_RECOGNITION_TOLERANCE - best_score
        print(f"  ⚠  Your face scored BELOW tolerance by {gap:.4f}.")
        suggested = round(best_score - 0.05, 2)
        print(f"     Suggested: set FACE_RECOGNITION_TOLERANCE = {suggested}")
        print(f"     in config.py  (current = {config.FACE_RECOGNITION_TOLERANCE})")
    else:
        headroom = best_score - config.FACE_RECOGNITION_TOLERANCE
        print(f"  ✓  Your face matched with {headroom:.4f} headroom above tolerance.")
        print(f"     Current tolerance ({config.FACE_RECOGNITION_TOLERANCE}) looks correct.")
        print(f"     If strangers are matching, raise tolerance slightly (e.g. +0.03).")
    print("="*55 + "\n")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    ensure_dirs()

    parser = argparse.ArgumentParser(
        description="Add or test known faces for the CCTV surveillance system."
    )
    parser.add_argument("--image", type=str, default=None,
                        help="Path to an existing face photo.")
    parser.add_argument("--test", action="store_true",
                        help="Run tolerance calibration instead of adding a face.")
    args = parser.parse_args()

    # Load InsightFace ONCE — shared by all functions below
    app = _load_insightface()

    if args.test:
        run_tolerance_test(app, image_path=args.image)
        return

    name = input("Enter person's name (e.g. Hrushikesh): ").strip()
    if not name:
        print("[ERROR] Name cannot be empty.")
        sys.exit(1)

    if args.image:
        success = import_from_file(name, args.image, app)
    else:
        success = capture_from_webcam(name, app)

    if success:
        print(f"\n[OK] '{name}' added successfully.")
        print("     Restart main.py or press F in the running window to reload faces.")
    else:
        print("\n[FAIL] Face not saved. Try again with better lighting or closer distance.")


if __name__ == "__main__":
    main()