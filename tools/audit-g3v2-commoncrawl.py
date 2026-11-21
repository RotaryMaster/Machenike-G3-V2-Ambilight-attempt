#!/usr/bin/env python3
"""Look for old G3V2 download links in Common Crawl (GET-only, no device I/O).

This searches a bounded set of historical crawl indices and reads a few
archived HTML WARC records by HTTP Range. It NEVER downloads firmware binaries
or executes them. No privileged commands, USB, BLE, or base-OS modifications.
"""
from __future__ import annotations

import argparse
import gzip
from html.parser import HTMLParser
import io
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

INDEX = "https://index.commoncrawl.org/"
DATA = "https://data.commoncrawl.org/"
# Representative archived months: not an exhaustive scan.
CRAWLS = (
    "CC-MAIN-2024-51",
    "CC-MAIN-2025-08",
    "CC-MAIN-2025-18",
    "CC-MAIN-2025-33",
    "CC-MAIN-2025-43",
    "CC-MAIN-2026-04",
)
URL_PATTERNS = (
    "machenike.com/shoubing/*",
    "www.machenike.com/shoubing/*",
    "global.machenike.com/shoubing/*",
)
NEEDLES = ("g3v2", "g3-v2", "g3_v2", "zxd2267", "keylinker", "mixdriver",
           "firmware", "download", "固件", "手柄", "下载")
EXT = re.compile(r"(?i)\.(?:exe|fot|zip|7z|rar|bin)(?=$|[?&#])")
MAX_INDEX = 550_000
MAX_WARC = 1_000_000


def request(url, *, headers=None, maxbytes=MAX_INDEX):
    hdr = {
        "User-Agent": "Mozilla/5.0 (G3V2-Firmware-Archive-ReadOnly/1.0)",
        "Accept": "application/json,text/plain,text/html,*/*",
    }
    if headers:
        hdr.update(headers)
    try:
        with urlopen(Request(url, headers=hdr, method="GET"), timeout=12) as res:
            if res.status not in (200, 206):
                return None, "HTTP " + str(res.status)
            data = res.read(maxbytes + 1)
            if len(data) > maxbytes:
                return None, f"response larger than {maxbytes} bytes"
            if headers and "Range" in headers and res.status != 206:
                return None, "server did not honor Range (no full WARC download)"
            return data, None
    except HTTPError as e:
        if e.code == 404:
            return None, "404 (no records)"
        return None, f"HTTP {e.code}"
    except (OSError, URLError, ValueError) as e:
        return None, f"{type(e).__name__}: {str(e)[:130]}"


def get_index(crawl, url_pattern):
    qs = urlencode({
        "url": url_pattern, "output": "json", "filter": "status:200",
        "limit": "35",
    })
    data, error = request(INDEX + crawl + "-index?" + qs)
    if error:
        return [], error
    records = []
    for line in data.decode("utf-8", "replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("status") != "200":
            continue
        if (not isinstance(record.get("url"), str) or
                not isinstance(record.get("filename"), str)):
            continue
        try:
            n = int(record.get("length", "-1"))
            offset = int(record.get("offset", "-1"))
            if n < 1 or n > MAX_WARC or offset < 0:
                continue
        except (ValueError, TypeError):
            continue
        records.append(record)
    return records, None


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for key in ("href", "src", "data-url", "data-href"):
            if a.get(key):
                self.refs.append(a[key])


def inspect_warc(rec):
    start = int(rec["offset"])
    size = int(rec["length"])
    url = DATA + rec["filename"].lstrip("/")
    comp, error = request(url, headers={
        "Range": f"bytes={start}-{start+size-1}"
    }, maxbytes=MAX_WARC)
    if error:
        return [], error
    try:
        payload = gzip.decompress(comp)
    except (EOFError, OSError) as e:
        return [], "cannot decompress record: " + str(e)
    if len(payload) > 4_000_000:
        return [], "decompressed record too large"
    # Remove the WARC and embedded HTTP headers when present.
    sections = re.split(rb"\r?\n\r?\n", payload, maxsplit=2)
    html = sections[2] if len(sections) > 2 else payload
    text = html.decode("utf-8", "replace")
    reader = Links()
    try:
        reader.feed(text)
    except Exception:
        pass
    candidates = set(reader.refs)
    # Capture literal download URLs from inline scripts and JSON, if present.
    for match in re.findall(r"""(?:https?:)?//[^\s"'<>\\]+""", text, re.I):
        candidates.add(match)
    found = []
    for value in candidates:
        clean = value.replace("\\/", "/").strip()
        norm = clean.lower()
        if any(word in norm for word in NEEDLES) or EXT.search(norm):
            found.append(urljoin(rec["url"], clean))
    return sorted(set(found))[:30], None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-pages", type=int, default=6,
                    help="maximum distinct archived HTML pages to read (default 6)")
    args = ap.parse_args()
    if not 0 <= args.max_pages <= 12:
        ap.error("--max-pages must be 0..12")
    print("=" * 90)
    print("G3V2 HISTORICAL DOWNLOADS — COMMON CRAWL INDEPENDENT ARCHIVE")
    print("=" * 90)
    print("Scope: bounded 2024-2026 indices, GET-only, HTML only, never download EXE/FOT")
    print("No G3V2 device contact, no OS base modification\n")
    hits, failures = {}, 0
    for crawl in CRAWLS:
        print(f"\n=== {crawl} ===", flush=True)
        for pattern in URL_PATTERNS:
            rows, error = get_index(crawl, pattern)
            if error:
                failures += 1 if error != "404 (no records)" else 0
                print(f"  {pattern}: {error}", flush=True)
                continue
            print(f"  {pattern}: {len(rows)} records", flush=True)
            for rec in rows:
                if "html" in rec.get("mime", "text/html").lower():
                    key = (rec["url"], rec.get("digest", ""))
                    hits[key] = rec

    print("\n=== INDEX SUMMARY ===")
    print("Unique HTML records:", len(hits))
    print("Non-404 failed index requests:", failures)
    if not hits:
        print("No usable captures. This does NOT prove that none exist.")
        return 0

    scored = sorted(hits.values(), key=lambda r: (
        not any(word in r["url"].lower() for word in ("g3v2","firmware","download")),
        r.get("timestamp", ""),
        r["url"],
    ))
    for rec in scored[:30]:
        print(f"CAPTURE {rec.get('timestamp','?')} {rec['url']}")
    print(f"\n=== ARCHIVED PAGE LINKS (up to {args.max_pages}) ===")
    for rec in scored[:args.max_pages]:
        print(f"\nPAGE: {rec['url']} ({rec.get('timestamp','?')})", flush=True)
        matches, error = inspect_warc(rec)
        if error:
            print(f"  WARC: {error}")
            continue
        if not matches:
            print("  No firmware-like links in this archived HTML")
        for link in matches:
            print("  CANDIDATE:", link)
    print("\nCANDIDATE means an archived link reference, not a verified firmware binary.")
    print("Before analysis: verify downloaded file SHA256, FOT magic and G3V2 HWID.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
