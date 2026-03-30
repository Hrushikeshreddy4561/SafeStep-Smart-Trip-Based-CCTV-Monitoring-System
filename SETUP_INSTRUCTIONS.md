# 🚀 Quick Setup Guide

Follow this guide to get the CCTV surveillance system up and running in **5 minutes**.

---

## Step 1: Install Python (if not already installed)

Download Python 3.11 from [python.org](https://www.python.org/) and install it.  
Make sure to check **"Add Python to PATH"** during installation.

---

## Step 2: Clone the Repository

Open Command Prompt/Terminal and run:

```bash
git clone https://github.com/yourusername/cctv-surveillance.git
cd cctv-surveillance
```

---

## Step 3: Create Virtual Environment

**Windows:**
```bash
python -m venv venv
.\venv\Scripts\activate
```

**Mac/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

---

## Step 4: Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

**Windows Only** - Install pre-built dlib wheel:
```bash
pip install dlib-19.24.1-cp311-cp311-win_amd64.whl
```

---

## Step 5: Test the Installation

```bash
python diagnose.py
```

You should see:
- ✅ OpenCV version
- ✅ Camera detected
- ✅ InsightFace installed
- ✅ All folders created

---

## Step 6: Add Your Face

```bash
python add_face.py
```

1. Press `c` to capture a photo of yourself
2. Press `s` to save
3. Press `q` to quit

Your face will be saved to `known_faces/` folder.

---

## Step 7: Start Surveillance

```bash
python main.py
```

You should see the camera feed with a timestamp bar at the bottom.

**Controls:**
- `q` - Quit
- `p` - Pause/Resume
- `f` - Reload known faces

---

## 🎉 Done!

The system is now monitoring for motion and unknown faces!

---

## 📁 Adding More People

To add colleagues or family members:

```bash
python add_face.py
# Capture their face
```

They will be automatically recognized next time they appear on camera.

---

## 🔍 Checking Alerts

```bash
python view_alerts.py
```

---

## ⚙️ Customizing Settings

Edit `config.py` to:
- Change camera sensitivity
- Use an RTSP camera
- Adjust alert levels
- Enable/disable pet filtering

**Example - Use RTSP Camera:**
```python
# In config.py, change:
CAMERA_INDEX = "rtsp://admin:password@192.168.1.100:554/stream"
```

<!-- Issues? -->

Run the diagnostics:
```bash
python diagnose.py
python diagnose_face.py

