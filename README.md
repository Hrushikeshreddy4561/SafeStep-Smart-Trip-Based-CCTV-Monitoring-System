# SafeStep -- Smart Trip-Based CCTV Monitoring System

SafeStep is a real-time home surveillance system that activates intelligent monitoring while the homeowner is away. It combines motion detection, face recognition, and automated email alerts into a single web-controlled application.

Built with OpenCV, InsightFace (RetinaFace + ArcFace), Flask, and EmailJS.

---

## Table of Contents

- [Features](#features)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Adding Known Faces](#adding-known-faces)
- [Email Alerts](#email-alerts)
- [License](#license)

---

## Features

- **Trip Mode** -- Toggle surveillance on/off through the web dashboard. The system monitors your home only when you activate it.
- **Motion Detection** -- Adaptive background subtraction identifies movement and filters out noise, lighting changes, and pets.
- **Face Recognition** -- InsightFace (RetinaFace + ArcFace) identifies known household members and flags unknown persons.
- **Zone-Based Capture** -- Evidence snapshots are taken only when a person enters a defined entry zone, preventing duplicate captures.
- **Automated Email Alerts** -- Sends real-time email notifications via EmailJS when an unknown person is detected.
- **Daily Summary** -- Scheduled summary email sent at 11 PM with the day's activity report.
- **Evidence Management** -- Face crops and full-scene images are saved locally with automatic age/size-based cleanup.
- **Web Dashboard** -- Real-time camera feed, trip controls, alert history, and evidence review from any browser on the local network.

---

## Project Structure

```
SafeStep/
|
|-- config.py                  # Central configuration (camera, thresholds, paths)
|-- main.py                    # Standalone CLI surveillance runner
|-- requirements.txt           # Python dependencies
|-- README.md
|
|-- motion_detection.py        # Background subtraction motion detector
|-- pet_filter.py              # Filters small contours (pets, noise)
|-- face_recognition_module.py # InsightFace face detection and recognition
|-- alert_system.py            # Alert level evaluation and evidence capture
|-- add_face.py                # CLI tool to register known faces
|
|-- utils/
|   |-- __init__.py
|   |-- helpers.py             # Shared utilities (FPS counter, evidence save, cleanup)
|
|-- tools/
|   |-- test_email.py          # EmailJS integration diagnostic
|   |-- diagnose.py            # System diagnostic script
|   |-- diagnose_face.py       # Face recognition diagnostic
|   |-- fix_known_face.py      # Utility to fix/re-encode known face embeddings
|   |-- view_alerts.py         # CLI alert log viewer
|
|-- webapp/
|   |-- __init__.py
|   |-- app.py                 # Flask application entry point
|   |-- auth.py                # Authentication (login, register, session management)
|   |-- routes.py              # Dashboard, API endpoints, video streaming
|   |-- models.py              # SQLite database operations
|   |-- cctv_runner.py         # Background CCTV engine thread
|   |-- email_service.py       # EmailJS REST API integration
|   |-- daily_summary.py       # Scheduled daily summary job
|   |-- .env.example           # Environment variable template
|   |
|   |-- static/
|   |   |-- css/style.css      # Application stylesheet
|   |   |-- js/app.js          # Dashboard client-side logic
|   |
|   |-- templates/
|       |-- base.html          # Base layout template
|       |-- login.html         # Login page
|       |-- register.html      # Registration page
|       |-- dashboard.html     # Main dashboard with camera feed
|       |-- alerts.html        # Alert listing page
|       |-- alert_detail.html  # Individual alert review page
|
|-- known_faces/               # Store known face images here (not tracked by git)
|-- evidence/                  # Auto-generated evidence captures (not tracked by git)
|-- alerts/                    # Runtime alert logs (not tracked by git)
```

---

## Prerequisites

- Python 3.10 or later
- A webcam or IP camera
- An [EmailJS](https://www.emailjs.com/) account (free tier is sufficient)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/Hrushikeshreddy4561/SafeStep-Smart-Trip-Based-CCTV-Monitoring-System.git
cd SafeStep-Smart-Trip-Based-CCTV-Monitoring-System
```

### 2. Create a virtual environment

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> Note: On some systems you may need to install `onnxruntime` separately if the default build does not match your platform.

### 4. Create the environment file

Copy the example and fill in your EmailJS credentials:

**Windows:**
```bash
copy webapp\.env.example webapp\.env
```

**macOS / Linux:**
```bash
cp webapp/.env.example webapp/.env
```

Edit `webapp/.env` with your values:

```
SECRET_KEY=your-random-secret-key
EMAILJS_PUBLIC_KEY=your_public_key
EMAILJS_PRIVATE_KEY=your_private_key
EMAILJS_SERVICE_ID=your_service_id
EMAILJS_ALERT_TEMPLATE_ID=your_alert_template_id
EMAILJS_SUMMARY_TEMPLATE_ID=your_summary_template_id
APP_URL=http://localhost:5000
```

---

## Configuration

All configurable parameters are in `config.py`. Key settings:

| Parameter | Default | Description |
|---|---|---|
| `CAMERA_INDEX` | `0` | Webcam index, RTSP URL, or video file path |
| `FRAME_WIDTH` / `FRAME_HEIGHT` | 640 / 480 | Camera resolution |
| `FPS_TARGET` | 20 | Target frames per second |
| `FACE_RECOGNITION_TOLERANCE` | 0.45 | ArcFace cosine similarity threshold (higher = stricter) |
| `FACE_DETECT_EVERY_N_FRAMES` | 4 | Run face detection every Nth frame (performance vs accuracy) |
| `ABSENCE_TIMEOUT` | 30 | Seconds a person must be gone before re-capture triggers |
| `EVIDENCE_MAX_AGE_DAYS` | 30 | Auto-delete evidence older than this (0 to disable) |
| `EVIDENCE_MAX_SIZE_MB` | 500 | Max evidence folder size before oldest files are pruned |

---

## Usage

### Web Application (recommended)

Start the Flask server:

```bash
python -m webapp.app
```

Open your browser and navigate to:

```
http://localhost:5000
```

Register an account, log in, and use the dashboard to control the camera and trip mode.

The application is also accessible from other devices on the same local network using:

```
http://<your-local-ip>:5000
```

### Standalone CLI Mode

For running the surveillance system without the web interface:

```bash
python main.py
```

Keyboard controls in CLI mode:

| Key | Action |
|---|---|
| `Q` / `ESC` | Quit |
| `R` | Reset background model |
| `A` | Acknowledge and reset alert state |
| `S` | Take a manual snapshot |
| `F` | Reload known faces from disk |

---

## Adding Known Faces

Place clear, front-facing photos in the `known_faces/` directory. The filename (without extension) becomes the display name.

```
known_faces/
  hrushikesh.jpg      -->  "Hrushikesh"
  john_doe.png        -->  "John Doe"
```

After adding images, either restart the application or press `F` in CLI mode to reload.

To use the interactive face registration tool:

```bash
python add_face.py
```

---

## Email Alerts

Email notifications are sent through the [EmailJS](https://www.emailjs.com/) REST API.

### Setup

1. Create a free account at [emailjs.com](https://www.emailjs.com/).
2. Add an email service (Gmail, Outlook, etc.) under **Email Services**.
3. Create an **Alert Template** with the following variables in the template body:
   - `{{to_name}}`, `{{to_email}}`, `{{alert_level}}`, `{{timestamp}}`
   - `{{face_count}}`, `{{alert_details}}`, `{{review_link}}`
4. Set the template **To Email** field to `{{to_email}}`.
5. Copy your Public Key, Private Key, Service ID, and Template ID into `webapp/.env`.

### Testing

Run the diagnostic script to verify your EmailJS configuration:

```bash
python tools/test_email.py
```

---

## License

This project is developed for academic and personal use.
