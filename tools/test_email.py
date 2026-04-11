"""
Quick test to debug EmailJS integration.
Run: python test_email.py
"""
import os
import sys
import requests
from dotenv import load_dotenv

# Load .env from project root first, then optional webapp/.env fallback
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TOOLS_DIR)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
load_dotenv(os.path.join(PROJECT_ROOT, "webapp", ".env"))

PUBLIC_KEY = os.getenv("EMAILJS_PUBLIC_KEY", "")
SERVICE_ID = os.getenv("EMAILJS_SERVICE_ID", "")
ALERT_TEMPLATE_ID = os.getenv("EMAILJS_ALERT_TEMPLATE_ID", "")

print("=" * 50)
print("  EmailJS Configuration Test")
print("=" * 50)
print(f"  Public Key:     {PUBLIC_KEY}")
print(f"  Service ID:     {SERVICE_ID}")
print(f"  Template ID:    {ALERT_TEMPLATE_ID}")
print("=" * 50)

if not all([PUBLIC_KEY, SERVICE_ID, ALERT_TEMPLATE_ID]):
    print("\n[ERROR] One or more keys are EMPTY! Check your webapp/.env file.")
    sys.exit(1)

# Ask for email to test
to_email = input("\nEnter your email address to send a test alert: ").strip()
if not to_email:
    print("[ERROR] No email entered. Exiting.")
    sys.exit(1)

print(f"\nSending test email to: {to_email}")

# --- Test 1: Using public key only (user_id field) ---
print("\n--- Test 1: Public Key in 'user_id' field ---")
payload = {
    "service_id": SERVICE_ID,
    "template_id": ALERT_TEMPLATE_ID,
    "user_id": PUBLIC_KEY,
    "template_params": {
        "to_name": "Test User",
        "to_email": to_email,
        "alert_level": "HIGH",
        "timestamp": "April 10, 2026 at 03:00 PM",
        "face_count": "1",
        "alert_details": "This is a TEST alert from SafeStep.",
        "review_link": "http://localhost:5000/dashboard",
        "face_image_url": "",
        "body_image_url": "",
    },
}

headers = {"Content-Type": "application/json"}

try:
    resp = requests.post(
        "https://api.emailjs.com/api/v1.0/email/send",
        json=payload,
        headers=headers,
        timeout=15,
    )
    print(f"  Status Code: {resp.status_code}")
    print(f"  Response:    {resp.text}")

    if resp.status_code == 200:
        print("\n  ✅ SUCCESS! Check your inbox (and spam folder).")
    elif resp.status_code == 403:
        print("\n  ❌ FORBIDDEN — EmailJS blocked the request.")
        print("     This usually means you need to add a PRIVATE KEY (accessToken).")
        print("     Go to EmailJS -> Account -> API Keys -> copy Private Key.")

        private_key = input("\n  Enter your EmailJS Private Key (or press Enter to skip): ").strip()
        if private_key:
            print("\n--- Test 2: With Private Key (accessToken) ---")
            payload["accessToken"] = private_key
            resp2 = requests.post(
                "https://api.emailjs.com/api/v1.0/email/send",
                json=payload,
                headers=headers,
                timeout=15,
            )
            print(f"  Status Code: {resp2.status_code}")
            print(f"  Response:    {resp2.text}")
            if resp2.status_code == 200:
                print("\n  ✅ SUCCESS with Private Key! You need to add it to your .env file.")
                print(f"     Add this line to webapp/.env:")
                print(f"     EMAILJS_PRIVATE_KEY={private_key}")
    elif resp.status_code == 400:
        print("\n  ❌ BAD REQUEST — Check your Service ID and Template ID.")
        print("     Make sure the template has {{to_email}} in the 'To Email' field.")
    elif resp.status_code == 422:
        print("\n  ❌ UNPROCESSABLE — Template variables might be wrong.")
        print("     Check that your template uses variables like {{to_name}}, {{to_email}}, etc.")
    else:
        print(f"\n  ❌ Unexpected error. Full response: {resp.text}")

except Exception as e:
    print(f"\n  ❌ Connection Error: {e}")
