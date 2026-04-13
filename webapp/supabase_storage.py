"""
supabase_storage.py — Supabase Cloud Storage for Evidence Images (v2)

Uploads evidence images (face crops + body scenes) to Supabase Storage
and returns public URLs that can be embedded in emails and viewed anywhere.

Uses direct REST API calls for maximum compatibility with free-tier Supabase.
The bucket must be created manually in the Supabase Dashboard:
  1. Go to Storage → New Bucket
  2. Name: evidence-images
  3. Toggle "Public bucket" ON
  4. Click Create
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
import requests
from dotenv import load_dotenv

# Load env
WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(WEBAPP_DIR)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=True)
load_dotenv(os.path.join(WEBAPP_DIR, ".env"), override=False)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "evidence-images")

# Thread pool for non-blocking uploads (max 3 concurrent uploads)
_upload_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="supabase-upload")




def upload_evidence_image(local_path: str) -> str:
    """
    Upload a local image file to Supabase Storage via REST API.

    Parameters
    ----------
    local_path : str
        Full path to the local evidence image file.

    Returns
    -------
    str
        Public URL of the uploaded image, or empty string on failure.
    """
    token = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
    if not SUPABASE_URL or not token:
        return ""

    if not os.path.exists(local_path):
        print(f"[SUPABASE] File not found: {local_path}")
        return ""

    filename = os.path.basename(local_path)

    try:
        with open(local_path, "rb") as f:
            file_data = f.read()

        # Upload via REST API (upsert mode)
        upload_url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{filename}"
        headers = {
            "apikey": token,
            "Authorization": f"Bearer {token}",
            "Content-Type": "image/jpeg",
            "x-upsert": "true"
        }

        resp = requests.post(upload_url, headers=headers, data=file_data, timeout=15)

        if resp.status_code in (200, 201):
            # Build public URL
            public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{filename}"
            print(f"[SUPABASE] Uploaded: {filename}")
            return public_url
        else:
            print(
                f"[SUPABASE] Upload failed ({resp.status_code}) for {filename}: "
                f"{resp.text[:200]}"
            )
            return ""

    except Exception as e:
        print(f"[SUPABASE] Upload error for {filename}: {e}")
        return ""


def upload_evidence_images_async(local_paths: list, callback=None):
    """
    Upload multiple images in background threads.

    Parameters
    ----------
    local_paths : list of str
        List of full paths to local image files.
    callback : callable, optional
        Called with (original_paths, cloud_urls) when all uploads complete.
        cloud_urls is a dict mapping local_path -> public_url.
    """
    def _do_uploads():
        results = {}
        for path in local_paths:
            url = upload_evidence_image(path)
            results[path] = url
        if callback:
            try:
                callback(local_paths, results)
            except Exception as e:
                print(f"[SUPABASE] Callback error: {e}")

    _upload_pool.submit(_do_uploads)


def get_public_url(filename: str) -> str:
    """Get the public URL for an already-uploaded file."""
    if not SUPABASE_URL:
        return ""
    return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{filename}"
