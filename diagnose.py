"""
diagnose.py — Run this FIRST to see exactly what your dlib/face_recognition
needs. It will tell us the exact fix required for your system.

Usage:
    python diagnose.py
"""
import cv2
import numpy as np

print("=" * 55)
print("CCTV Diagnostic Tool")
print("=" * 55)

# ── 1. Check versions ─────────────────────────────────────
print("\n[1] Library versions:")
print(f"    numpy   : {np.__version__}")
print(f"    opencv  : {cv2.__version__}")

try:
    import dlib
    print(f"    dlib    : {dlib.__version__}")
except Exception as e:
    print(f"    dlib    : ERROR — {e}")

try:
    import face_recognition
    print(f"    face_recognition : installed OK")
except Exception as e:
    print(f"    face_recognition : ERROR — {e}")

# ── 2. Test a synthetic image through dlib directly ───────
print("\n[2] Testing dlib face_detector with synthetic images...")
try:
    import dlib
    detector = dlib.get_frontal_face_detector()

    for dtype in [np.uint8, np.uint16, np.float32]:
        for order in ['C', 'F']:
            img = np.zeros((100, 100, 3), dtype=dtype, order=order)
            try:
                detector(img, 0)
                print(f"    dtype={dtype.__name__:8s}  order={order}  → OK")
            except RuntimeError as e:
                print(f"    dtype={dtype.__name__:8s}  order={order}  → FAIL: {e}")
except Exception as e:
    print(f"    Could not test dlib directly: {e}")

# ── 3. Test webcam frame through the full pipeline ────────
print("\n[3] Testing live webcam frame...")
cap = cv2.VideoCapture(0)
ret, frame = cap.read()
cap.release()

if not ret:
    print("    Could not read webcam frame.")
else:
    print(f"    Raw frame: dtype={frame.dtype}  shape={frame.shape}  "
          f"contiguous={frame.flags['C_CONTIGUOUS']}")

    # Try progressively safer conversions
    candidates = {
        "raw BGR"              : frame,
        "BGR→RGB"              : cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
        "ascontiguousarray"    : np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), dtype=np.uint8),
        "np.array copy"        : np.array(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), dtype=np.uint8),
        "manual copy()"        : cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).copy(),
    }

    try:
        import dlib
        detector = dlib.get_frontal_face_detector()
        for label, img in candidates.items():
            try:
                detector(img, 0)
                print(f"    [{label:25s}] → WORKS ✓")
            except RuntimeError as e:
                print(f"    [{label:25s}] → FAIL: {e}")
    except Exception as e:
        print(f"    dlib import failed: {e}")

# ── 4. Test known face image ──────────────────────────────
print("\n[4] Testing known_faces/hrushikesh.jpg...")
import os
path = os.path.join(os.path.dirname(__file__), "known_faces", "hrushikesh.jpg")
if not os.path.exists(path):
    print("    File not found. Run add_face.py first.")
else:
    bgr = cv2.imread(path)
    print(f"    Loaded: dtype={bgr.dtype}  shape={bgr.shape}  "
          f"contiguous={bgr.flags['C_CONTIGUOUS']}")
    try:
        import face_recognition
        candidates = {
            "ascontiguousarray RGB" : np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), dtype=np.uint8),
            "np.array copy RGB"     : np.array(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), dtype=np.uint8),
            "manual .copy() RGB"    : cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).copy(),
        }
        for label, img in candidates.items():
            try:
                encs = face_recognition.face_encodings(img)
                print(f"    [{label:30s}] → WORKS ✓  (faces found: {len(encs)})")
            except RuntimeError as e:
                print(f"    [{label:30s}] → FAIL: {e}")
    except Exception as e:
        print(f"    face_recognition test error: {e}")

print("\n" + "=" * 55)
print("Share the output above so we can apply the exact fix.")
print("=" * 55)