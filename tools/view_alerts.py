#!/usr/bin/env python3
# =============================================================================
# view_alerts.py — View alert log and browse evidence snapshots
#
# USAGE:
#   python view_alerts.py         ← print today's alert log
#   python view_alerts.py --all   ← print full log
#   python view_alerts.py --view  ← slide-show of evidence images
# =============================================================================

import os
import cv2
import argparse
import config


def print_log(all_entries=False):
    if not os.path.isfile(config.ALERTS_LOG):
        print("[INFO] No alert log found yet.")
        return

    with open(config.ALERTS_LOG, 'r') as f:
        lines = f.readlines()

    if all_entries:
        print("".join(lines))
    else:
        # Show last 50 lines
        print("".join(lines[-50:]))
    print(f"\nTotal log lines: {len(lines)}")


def slideshow_evidence():
    if not os.path.isdir(config.EVIDENCE_DIR):
        print("[INFO] No evidence directory found.")
        return

    images = sorted([
        os.path.join(config.EVIDENCE_DIR, f)
        for f in os.listdir(config.EVIDENCE_DIR)
        if f.lower().endswith(('.jpg', '.png'))
    ])

    if not images:
        print("[INFO] No evidence images found.")
        return

    print(f"[INFO] Found {len(images)} evidence images.")
    print("[INFO] Press any key to advance, ESC to quit slideshow.")

    for i, path in enumerate(images):
        img = cv2.imread(path)
        if img is None:
            continue
        label = os.path.basename(path)
        cv2.putText(img, f"{i+1}/{len(images)}: {label}",
                    (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,255), 2)
        cv2.imshow("Evidence Viewer", img)
        key = cv2.waitKey(0) & 0xFF
        if key == 27:
            break

    cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(description="View alert log and evidence.")
    parser.add_argument("--all",  action="store_true", help="Show full log")
    parser.add_argument("--view", action="store_true", help="Slideshow of evidence images")
    args = parser.parse_args()

    if args.view:
        slideshow_evidence()
    else:
        print_log(all_entries=args.all)


if __name__ == "__main__":
    main()
