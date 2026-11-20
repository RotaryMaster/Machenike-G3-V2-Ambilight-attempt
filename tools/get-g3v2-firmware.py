#!/usr/bin/env python3
"""
Safe MixDrive firmware catalog client for Machenike G3 V2.

This tool reproduces only the HTTP catalog/download stage recovered from
Machenik.HttpApi.dll.

It NEVER:
- opens HID/USB devices
- sends EnterUpdateMode
- modifies CustomData
- launches the updater

Default behavior is catalog-only. Pass --download explicitly to save the
returned updater executable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = "https://mixdriver.thunderobot.com/api"
ENDPOINT = "/firmware/by-product-info"

DEFAULT_BRAND = "MACHENIKE"
DEFAULT_PRODUCT_NAME = "G3V2"
DEFAULT_PRODUCT_TYPE = 2

UA = "Machenike-G3V2-research/1.0"


def find_key(obj: Any, names: set[str]):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in names and v not in (None, ""):
                return v
        for v in obj.values():
            found = find_key(v, names)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_key(v, names)
            if found not in (None, ""):
                return found
    return None


def normalize_download_url(value: str) -> str:
    if value.lower().startswith(("http://", "https://")):
        return value
    return BASE_URL.rstrip("/") + "/" + value.lstrip("/")


def safe_filename(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._+-]+", "_", name).strip("._")
    return name or "G3V2_firmware_updater.exe"


def request_json(url: str) -> tuple[Any, bytes, dict[str, str]]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        headers = {k.lower(): v for k, v in r.headers.items()}
        charset = r.headers.get_content_charset() or "utf-8"
        text = raw.decode(charset, "replace")
        return json.loads(text), raw, headers


def stream_download(url: str, destination: Path) -> tuple[str, int, dict[str, str]]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "*/*",
        },
        method="GET",
    )
    h = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(req, timeout=60) as r, destination.open("wb") as f:
        headers = {k.lower(): v for k, v in r.headers.items()}
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            total += len(chunk)
    return h.hexdigest(), total, headers


def filename_from_headers(headers: dict[str, str]) -> str | None:
    cd = headers.get("content-disposition", "")
    # RFC 5987 filename*=UTF-8''...
    m = re.search(r"filename\\*\\s*=\\s*UTF-8''([^;]+)", cd, re.I)
    if m:
        return safe_filename(urllib.parse.unquote(m.group(1)))
    m = re.search(r'filename\\s*=\\s*"([^"]+)"', cd, re.I)
    if not m:
        m = re.search(r"filename\\s*=\\s*([^;]+)", cd, re.I)
    if m:
        return safe_filename(m.group(1).strip())
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Query the official MixDrive G3V2 firmware catalog safely."
    )
    ap.add_argument("--brand", default=DEFAULT_BRAND)
    ap.add_argument("--product-name", default=DEFAULT_PRODUCT_NAME)
    ap.add_argument("--product-type", type=int, default=DEFAULT_PRODUCT_TYPE)
    ap.add_argument(
        "--download",
        action="store_true",
        help="Download the returned updater EXE. Never executes it.",
    )
    ap.add_argument(
        "--outdir",
        default="firmware-downloads",
        help="Directory for catalog JSON and optional updater.",
    )
    args = ap.parse_args()

    params = {
        "brand": args.brand,
        "product_name": args.product_name,
        "product_type": str(args.product_type),
    }
    catalog_url = BASE_URL + ENDPOINT + "?" + urllib.parse.urlencode(params)

    print("=" * 96)
    print("MIXDRIVE G3V2 SAFE FIRMWARE CATALOG")
    print("=" * 96)
    print("Endpoint :", BASE_URL + ENDPOINT)
    print("brand    :", args.brand)
    print("product  :", args.product_name)
    print("type     :", args.product_type)
    print("URL      :", catalog_url)
    print("Hardware : NOT ACCESSED")
    print("Updater  : WILL NOT BE EXECUTED")
    print()

    try:
        data, raw, headers = request_json(catalog_url)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print(f"HTTP {e.code} {e.reason}", file=sys.stderr)
        print(body, file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Catalog request failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    outdir = Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    catalog_path = outdir / "G3V2-firmware-catalog.json"
    catalog_path.write_bytes(raw)

    print("HTTP content-type:", headers.get("content-type", "?"))
    print("Catalog saved    :", catalog_path)
    print()
    print(json.dumps(data, ensure_ascii=False, indent=2))

    version = find_key(data, {"version"})
    published = find_key(data, {"published_at", "publishedat"})
    file_size = find_key(data, {"file_size", "filesize"})
    dl = find_key(data, {"download_url", "downloadurl"})

    print()
    print("=== EXTRACTED ===")
    print("Version      :", version if version is not None else "(not found)")
    print("Published at :", published if published is not None else "(not found)")
    print("File size    :", file_size if file_size is not None else "(not found)")
    print("Download URL :", dl if dl is not None else "(not found)")

    if dl is None:
        print("\nNo download_url found; nothing else to do.")
        return 0

    download_url = normalize_download_url(str(dl))
    print("Resolved URL :", download_url)

    if not args.download:
        print("\nCatalog-only mode. Re-run with --download to save the updater EXE.")
        return 0

    # First choose a deterministic local filename. We intentionally do not
    # execute or chmod the result.
    url_name = Path(urllib.parse.urlparse(download_url).path).name
    if url_name and "." in url_name:
        filename = safe_filename(url_name)
    else:
        ver = safe_filename(str(version)) if version is not None else "latest"
        filename = f"ZXD2267_G3V2_{ver}_downloaded.exe"

    dest = outdir / filename
    tmp = outdir / (filename + ".part")

    try:
        sha256, total, dl_headers = stream_download(download_url, tmp)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print(f"Download HTTP {e.code} {e.reason}", file=sys.stderr)
        print(body, file=sys.stderr)
        return 3
    except Exception as e:
        print(f"Download failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 3

    header_name = filename_from_headers(dl_headers)
    if header_name and header_name != filename:
        dest = outdir / header_name

    os.replace(tmp, dest)

    print()
    print("=== DOWNLOADED — NOT EXECUTED ===")
    print("File   :", dest)
    print("Bytes  :", total)
    print("SHA256 :", sha256)
    print("Type   :", dl_headers.get("content-type", "?"))

    # Small safety sanity check only; no parsing/execution.
    head = dest.read_bytes()[:2]
    print("MZ EXE :", head == b"MZ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
