"""
diagnose_face.py — Check why your known face is not being recognised.
Run this while sitting in front of your webcam.

Usage:
    python diagnose_face.py
"""
import cv2
import os
import numpy as np
import config

print("=" * 55)
print("Face Recognition Diagnostic")
print("=" * 55)

# ── Check known face image ────────────────────────────────
path = os.path.join(config.KNOWN_FACES_DIR, "hrushikesh.jpg")
if not os.path.exists(path):
    # try any jpg in known_faces
    files = [f for f in os.listdir(config.KNOWN_FACES_DIR)
             if f.lower().endswith(('.jpg','.png','.jpeg'))]
    if not files:
        print("[ERROR] No images in known_faces/ folder!")
        exit()
    path = os.path.join(config.KNOWN_FACES_DIR, files[0])

print(f"\n[1] Known face image: {path}")
bgr = cv2.imread(path)
print(f"    Size: {bgr.shape[1]}x{bgr.shape[0]} pixels")

import face_recognition

def to_rgb(bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(rgb, dtype=np.uint8)

known_rgb = to_rgb(bgr)
known_locs = face_recognition.face_locations(known_rgb)
print(f"    Faces detected in saved photo: {len(known_locs)}")

if not known_locs:
    print("\n    *** PROBLEM: No face found in your known_faces image!")
    print("    The photo captured by add_face.py is the full webcam frame.")
    print("    face_recognition needs a clear, close-up face.")
    print("    FIX: Run  python fix_known_face.py  to crop it properly.")
else:
    known_enc = face_recognition.face_encodings(known_rgb, known_locs)[0]
    print(f"    Face location: {known_locs[0]}")
    top, right, bottom, left = known_locs[0]
    face_h = bottom - top
    face_w = right - left
    print(f"    Face size in photo: {face_w}x{face_h}px")
    if face_w < 80:
        print("    *** WARNING: Face is very small in the photo — recognition will be poor.")
        print("    FIX: Run  python fix_known_face.py  to recapture a close-up.")

    # ── Live test ─────────────────────────────────────────
    print("\n[2] Live camera test — sit in front of camera...")
    print("    Press SPACE to capture and compare. ESC to quit.")

    cap = cv2.VideoCapture(config.CAMERA_INDEX)
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Show the frame
        disp = frame.copy()
        cv2.putText(disp, "SPACE=Test  ESC=Quit", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,255), 2)
        cv2.imshow("Face Diagnostic", disp)

        key = cv2.waitKey(1) & 0xFF
        if key == 27:
            break
        elif key == 32:  # SPACE
            rgb = to_rgb(frame)
            locs = face_recognition.face_locations(rgb)
            print(f"\n    Faces found in live frame: {len(locs)}")

            if not locs:
                print("    No face detected — move closer to camera, improve lighting.")
                continue

            encs = face_recognition.face_encodings(rgb, locs)
            for i, enc in enumerate(encs):
                dist = face_recognition.face_distance([known_enc], enc)[0]
                match = dist < config.FACE_RECOGNITION_TOLERANCE
                print(f"    Face {i+1}: distance={dist:.4f}  "
                      f"tolerance={config.FACE_RECOGNITION_TOLERANCE}  "
                      f"match={'YES ✓' if match else 'NO ✗'}")
                if not match:
                    needed = dist - config.FACE_RECOGNITION_TOLERANCE
                    print(f"    → Tolerance needs to increase by {needed:.3f} to match.")
                    print(f"    → Set FACE_RECOGNITION_TOLERANCE = {dist+0.02:.2f} in config.py")

    cap.release()
    cv2.destroyAllWindows()

print("\n" + "=" * 55)
