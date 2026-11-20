#!/usr/bin/env python3
"""
Conservative read-only probe for possible MixDrive firmware history endpoints.

Purpose:
- test a small fixed set of plausible GET routes using the already-confirmed
  G3V2 product id (8) and firmware id (11);
- report status/content without authentication, writes, HID/USB access, or
  updater execution.

This is NOT a brute-force scanner. The candidate list is intentionally small.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://mixdriver.thunderobot.com/api"
UA = "Machenike-G3V2-research/1.0"

def get(url: str):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(128 * 1024)
            ctype = r.headers.get("content-type", "")
            return r.status, ctype, raw
    except urllib.error.HTTPError as e:
        raw = e.read(64 * 1024)
        return e.code, e.headers.get("content-type", ""), raw

def pretty(raw: bytes, ctype: str) -> str:
    text = raw.decode("utf-8", "replace")
    if "json" in ctype.lower():
        try:
            return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except Exception:
            pass
    return text

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product-id", type=int, default=8)
    ap.add_argument("--firmware-id", type=int, default=11)
    args = ap.parse_args()

    candidates = [
        f"/firmware/{args.firmware_id}",
        f"/firmware?id={args.firmware_id}",
        f"/firmware?product_id={args.product_id}",
        f"/firmware/list?product_id={args.product_id}",
        f"/firmware/history?product_id={args.product_id}",
        f"/firmware/by-product-id?product_id={args.product_id}",
        f"/firmware/by-product?product_id={args.product_id}",
        f"/firmware/all?product_id={args.product_id}",
    ]

    print("=" * 100)
    print("MIXDRIVE FIRMWARE HISTORY API PROBE — READ ONLY")
    print("=" * 100)
    print(f"Base       : {BASE}")
    print(f"Product id : {args.product_id}")
    print(f"Firmware id: {args.firmware_id}")
    print("Requests   : GET only")
    print("Device I/O : none")
    print()

    for path in candidates:
        url = BASE + path
        print("-" * 100)
        print(url)
        try:
            status, ctype, raw = get(url)
            print("HTTP        :", status)
            print("Content-Type:", ctype or "?")
            body = pretty(raw, ctype)
            if len(body) > 12000:
                body = body[:12000] + "\n...[truncated]"
            print(body if body.strip() else "(empty body)")
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
