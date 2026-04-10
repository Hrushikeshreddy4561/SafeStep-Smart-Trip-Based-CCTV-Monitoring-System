"""
fix_known_face.py — Recapture a proper close-up face photo.

The problem with add_face.py is it saves the FULL webcam frame (640x480).
Your face in that frame might only be 80x80 pixels — too small for reliable
recognition. This tool crops tightly around your face and saves that instead.

Usage:
    python fix_known_face.py
"""
import cv2
import os
import numpy as np
import face_recognition
import config
from utils.helpers import ensure_dirs

ensure_dirs()

def to_rgb(bgr):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(rgb, dtype=np.uint8)

print("Fix Known Face Tool")
print("=" * 40)
print("Sit close to the camera (face should fill ~1/3 of frame).")
print("Press SPACE to capture. Press ESC to cancel.\n")

name = input("Enter name (e.g. Hrushikesh): ").strip()
if not name:
    print("Name required.")
    exit()

cap = cv2.VideoCapture(config.CAMERA_INDEX)

while True:
    ret, frame = cap.read()
    if not ret:
        continue

    disp = frame.copy()
    h, w = frame.shape[:2]

    # Guide box — tell user to fill this area with their face
    cx, cy = w//2, h//2
    bx, by = 140, 170
    cv2.rectangle(disp, (cx-bx, cy-by), (cx+bx, cy+by), (0, 255, 0), 2)
    cv2.putText(disp, "Fill box with your face", (cx-bx, cy-by-10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 1)
    cv2.putText(disp, "SPACE=Capture  ESC=Cancel", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,255), 1)

    # Live face detection preview
    small = cv2.resize(frame, (0,0), fx=0.5, fy=0.5)
    locs  = face_recognition.face_locations(to_rgb(small))
    for (t,r,b,l) in locs:
        cv2.rectangle(disp, (l*2, t*2), (r*2, b*2), (255,0,0), 1)

    cv2.imshow("Fix Known Face", disp)
    key = cv2.waitKey(1) & 0xFF

    if key == 27:
        print("Cancelled.")
        break

    elif key == 32:  # SPACE — capture and process
        rgb  = to_rgb(frame)
        locs = face_recognition.face_locations(rgb)

        if not locs:
            print("  No face detected — move closer or improve lighting. Try again.")
            continue

        # Pick the largest face (closest to camera)
        locs_sorted = sorted(locs, key=lambda l: (l[2]-l[0])*(l[1]-l[3]), reverse=True)
        top, right, bottom, left = locs_sorted[0]

        # Add generous padding around the face
        pad   = 40
        top   = max(0, top - pad)
        left  = max(0, left - pad)
        bottom= min(frame.shape[0], bottom + pad)
        right = min(frame.shape[1], right + pad)

        face_crop = frame[top:bottom, left:right]
        face_h, face_w = face_crop.shape[:2]
        print(f"  Face detected: {face_w}x{face_h}px — good!")

        # Verify encoding works
        enc = face_recognition.face_encodings(to_rgb(face_crop))
        if not enc:
            print("  Could not encode face from crop — try again with better lighting.")
            continue

        # Save
        safe_name = name.lower().replace(" ", "_")
        dest      = os.path.join(config.KNOWN_FACES_DIR, f"{safe_name}.jpg")
        cv2.imwrite(dest, face_crop)
        print(f"\n  Saved close-up face to: {dest}")
        print(f"  Face size: {face_w}x{face_h}px")
        print(f"\n  Now run: python main.py")
        print(f"  (The system will auto-load the new photo)")
        break

cap.release()
cv2.destroyAllWindows()
