#!/usr/bin/env python3
"""Upload StealFeed source to Railway via the direct code-upload endpoint.

Usage: stealfeed_upload.py
Prints the deployment id on success. Uses the stored custom.railway
credential via authd surrogates (same pattern as bin/railway-api).
"""
from __future__ import annotations

import io
import json
import sys
import tarfile
import urllib.error
import urllib.request

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import (  # noqa: E402
    add_surrogate_to_request,
    read_json_response,
)

PROJECT_ID = "3efb412a-c927-480e-8b1d-ddb1e0ca87f5"
ENV_ID = "588b9029-7327-4a4c-aab3-899f2a67b440"
SERVICE_ID = "72cad1db-e58f-4e90-90fa-953e11f05a50"
UPLOAD_URL = (
    f"https://backboard.railway.com/project/{PROJECT_ID}/environment/{ENV_ID}/up"
    f"?serviceId={SERVICE_ID}"
)
ALLOWED_HOSTS = ["backboard.railway.com"]
FILES = ["main.py", "requirements.txt", "Procfile"]
SRC_DIR = "/home/hatch/workspace/stealfeed"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def build_tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in FILES:
            tar.add(f"{SRC_DIR}/{name}", arcname=name)
    return buf.getvalue()


def main() -> int:
    body = build_tarball()
    request = urllib.request.Request(UPLOAD_URL, data=body, method="POST")
    request.add_header("Content-Type", "application/gzip")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", UA)
    request.add_header("Origin", "https://railway.com")
    request.add_header("Referer", "https://railway.com/")
    add_surrogate_to_request(request, "custom.railway", allowed_hosts=ALLOWED_HOSTS)
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            payload = read_json_response(resp)
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", errors="replace")[:2000]
        except Exception:
            text = "<unreadable>"
        print(f"error: HTTP {exc.code}: {text}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: request failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
