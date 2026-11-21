#!/usr/bin/env python3
"""Machenike G3V2 historical download URL audit using Wayback (READ ONLY).

Fetches only text index metadata and archived HTML. Never downloads/executes
firmware installers, accesses USB/BLE, or modifies system files.
"""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PAGES = [
    "https://machenike.com/shoubing/download",
    "https://www.machenike.com/shoubing/download",
    "https://global.machenike.com/shoubing/download",
    "https://global.machenike.com/download",
]
INDEX = "https://web.archive.org/cdx/search/cdx"
AVAILABLE = "https://archive.org/wayback/available"
KEYWORDS = (
    "g3v2", "g3-v2", "g3_v2", "g3 v2", "zxd2267", "jrh_g3v2",
    "firmware", "download", "driver", "mixdriver", "keylinker",
    "固件", "下载", "手柄", ".exe", ".zip", ".rar", ".7z", ".fot",
)
FILES = re.compile(r"(?i)\.(?:exe|zip|rar|7z|bin|fot|apk)(?:\b|[?#])")
URLS = re.compile(r"""(?i)(?:https?:)?//[^\s"'<>\\]+|/(?:download|uploads?|files?)/[^\s"'<>\\]+""")
MAX = 1_250_000


def get(url, limit=MAX):
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; G3V2-Archive-Audit/1.0)",
        "Accept": "text/html,application/json,*/*",
    }, method="GET")
    try:
        with urlopen(req, timeout=18) as response:
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError(f"response exceeds cap {limit} bytes")
            return data.decode("utf-8", "replace"), None
    except (HTTPError, URLError, OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {str(e)[:190]}"


class LinkReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.last_anchor = -1

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for name in ("href", "src", "data-href", "data-url", "download"):
            if a.get(name):
                self.links.append((tag, a[name], ""))
                if tag == "a" and name == "href":
                    self.last_anchor = len(self.links) - 1
        if tag == "a" and not a.get("href"):
            self.last_anchor = -1

    def handle_data(self, data):
        if self.last_anchor >= 0:
            tag, href, label = self.links[self.last_anchor]
            self.links[self.last_anchor] = (tag, href, (label + data).strip()[:160])

    def handle_endtag(self, tag):
        if tag == "a":
            self.last_anchor = -1


def cdxcaptures(page):
    args = {
        "url": page, "output": "json",
        "fl": "timestamp,original,statuscode,mimetype",
        "filter": "statuscode:200",
        "collapse": "timestamp:6",
        "from": "2023", "to": "2026",
    }
    response, err = get(INDEX + "?" + urlencode(args), limit=300_000)
    if err:
        return [], err
    try:
        rows = json.loads(response)
        if not isinstance(rows, list) or not rows:
            return [], "CDX: empty response"
        header = rows[0]
        captures = [dict(zip(header, row)) for row in rows[1:]]
        captures = [c for c in captures if c.get("timestamp", "").isdigit()
                    and c.get("original", "").startswith(("http://", "https://"))]
        return captures, None
    except (ValueError, TypeError, KeyError) as e:
        return [], "CDX invalid JSON: " + str(e)[:100]


def fallback_snapshots(page):
    captures = []
    for year in ("2024", "2025", "2026"):
        args = {"url": page, "timestamp": year + "0701"}
        data, err = get(AVAILABLE + "?" + urlencode(args), limit=90_000)
        if err:
            print(f"  availability {year}: {err}")
            continue
        try:
            item = json.loads(data).get("archived_snapshots", {}).get("closest", {})
        except (ValueError, AttributeError):
            continue
        url = item.get("url", "")
        stamp = item.get("timestamp", "")
        if item.get("available") and url and stamp:
            captures.append({"timestamp": stamp, "original": page,
                             "archive_url": url})
    return captures


def choose(captures, max_per_page=6):
    selected = []
    seen = set()
    for item in sorted(captures, key=lambda x: x["timestamp"]):
        year = item["timestamp"][:4]
        if year not in ("2024", "2025", "2026"):
            continue
        month = item["timestamp"][:6]
        if month in seen:
            continue
        seen.add(month)
        selected.append(item)
    # Spread captures across years and don't let one year consume all slots.
    buckets = {}
    for x in selected:
        buckets.setdefault(x["timestamp"][:4], []).append(x)
    result = []
    for i in range(3):
        for year in ("2024", "2025", "2026"):
            group = buckets.get(year, [])
            if len(group) > i:
                result.append(group[i])
                if len(result) >= max_per_page:
                    return result
    return result


def inspect(page, capture):
    stamp = capture["timestamp"]
    original = capture.get("original") or page
    replay = capture.get("archive_url") or (
        "https://web.archive.org/web/" + stamp + "id_/" + original)
    print(f"\n  CAPTURE {stamp}: {replay}")
    body, err = get(replay)
    if err:
        print(f"  archived HTML unreadable: {err}")
        return

    parser = LinkReader()
    try:
        parser.feed(body)
    except Exception as e:
        print(f"  partial HTML parser failure: {e}")
    matches = {}
    for tag, href, label in parser.links:
        if any(k in (href + " " + label).lower() for k in KEYWORDS):
            matches[href] = label or tag

    for url in URLS.findall(body):
        if FILES.search(url) or any(k in url.lower() for k in
                                    ("zxd2267", "g3v2", "firmware")):
            matches.setdefault(url, "inline URL")
    for line in body.splitlines():
        if len(line) > 2500:
            continue
        if ("g3v2" in line.lower() or "zxd2267" in line.lower()
                or "jrh_g3v2" in line.lower()):
            fragment = re.sub(r"<[^>]*>", " ", line)
            print("  SOURCE TEXT:", fragment.strip()[:350])
            break

    if not matches:
        print("  No relevant links found in archived HTML (may use JS/API).")
        return
    shown = 0
    for link, label in matches.items():
        if shown >= 35:
            print(f"  ... {len(matches)-shown} additional link(s) not printed")
            break
        print(f"  LINK: {link[:350]}")
        if label:
            print(f"        label: {label[:140]}")
        shown += 1
    print("  Note: a listed URL is NOT a verified archived firmware binary.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pages-only", action="store_true",
                    help="report capture metadata, skip HTML requests")
    ap.add_argument("--max-per-page", type=int, default=6)
    args = ap.parse_args()
    if not 1 <= args.max_per_page <= 9:
        ap.error("--max-per-page must be 1..9")
    print("=" * 85)
    print("G3V2 / ZXD2267 — WAYBACK HISTORICAL DOWNLOAD URL AUDIT")
    print("=" * 85)
    print("GET only; public archive metadata + HTML; firmware download: DISABLED")
    print("USB/BLE/controller I/O: NONE; Bazzite base modifications: NONE")
    print("Period: 2023–2026, focus 2024–2025\n")

    found = 0
    for page in PAGES:
        print("\n" + "=" * 85)
        print("ORIGINAL:", page)
        captures, err = cdxcaptures(page)
        if err:
            print("  CDX:", err)
            print("  Trying public Wayback availability API (closest annual captures)")
            captures = fallback_snapshots(page)
        else:
            print("  CDX monthly captures:", len(captures))
        selected = choose(captures, args.max_per_page)
        print("  Selected:", len(selected))
        for c in selected:
            found += 1
            if args.pages_only:
                print(" ", c["timestamp"], c.get("archive_url", c.get("original")))
            else:
                inspect(page, c)
    print("\n" + "=" * 85)
    if not found:
        print("No accessible archive metadata. This does NOT prove the site was never archived.")
        print("Manual lookup: https://web.archive.org/web/*/https://machenike.com/shoubing/download")
    else:
        print("Review LINK lines for archived/download URLs. Do not run any EXE.")
        print("Any candidate firmware must be checked for exact G3V2 HWID before comparison.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
