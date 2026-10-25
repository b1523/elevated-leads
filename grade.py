#!/usr/bin/env python3
"""
Elevated Digital Co — Website Grader
Reads leads.csv, fetches each business's website, scores how dated/broken it
is, writes leads_graded.csv. Runs on GitHub Actions (full internet access).

Score is 0-100 where HIGHER = WORSE site = BETTER lead for us.
Rows with no website score 100.
"""

import csv
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
THIS_YEAR = datetime.now().year

# Third-party listing pages are not "their website".
NOT_A_REAL_SITE = (
    "facebook.com", "instagram.com", "linktr.ee", "yellowpages.com.au",
    "truelocal.com.au", "hipages.com.au", "oneflare.com.au", "gumtree.com",
    "airtasker.com", "serviceseeking.com.au", "localsearch.com.au",
)
# Free / template builders = low-effort, easy to beat.
BUILDERS = {
    "wix.com": "Wix", "wixsite.com": "Wix", "weebly.com": "Weebly",
    "godaddysites.com": "GoDaddy builder", "business.site": "Google Business site",
    "squarespace.com": "Squarespace", "site123.me": "Site123",
    "webnode": "Webnode", "yolasite.com": "Yola", "jimdo": "Jimdo",
}


HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

BLOCK_CODES = {401, 403, 406, 409, 429, 503, 520, 521, 522, 523, 524}


def clean(url):
    return url.split("?")[0]


def fetch(url, timeout=15):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=HEADERS)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        body = r.read(400_000).decode("utf-8", "replace")
        return r.status, r.geturl(), body, time.time() - t0


def fetch_curl(url, timeout=20):
    """Different TLS fingerprint to urllib; gets past some WAFs."""
    cmd = ["curl", "-sSL", "--compressed", "--max-time", str(timeout),
           "-o", "-", "-w", "\n__STATUS__%{http_code}__URL__%{url_effective}",
           "-A", UA, "-H", "Accept-Language: en-AU,en;q=0.9",
           "-H", "Accept: text/html,application/xhtml+xml,*/*;q=0.8", url]
    t0 = time.time()
    out = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
    text = out.stdout.decode("utf-8", "replace")
    m = re.search(r"\n__STATUS__(\d+)__URL__(.*)$", text, re.S)
    if not m:
        raise RuntimeError(out.stderr.decode("utf-8", "replace")[:100] or "curl failed")
    return int(m.group(1)), m.group(2), text[:m.start()][:400_000], time.time() - t0


def grade(row):
    url = (row.get("website") or "").strip()
    flags, score = [], 0

    if not url:
        row.update(grade_score=100, grade_status="GRADED", grade_flags="NO WEBSITE", final_url="")
        return row

    host = re.sub(r"^https?://", "", url).split("/")[0].lower()
    if any(d in host for d in NOT_A_REAL_SITE):
        row.update(grade_score=95, grade_status="GRADED",
                   grade_flags="Only a social/listing page", final_url=url)
        return row

    url = clean(url)
    body, status, final, secs = "", None, url, 0.0
    err = None
    for attempt in (fetch, fetch_curl):
        try:
            status, final, body, secs = attempt(url)
            err = None
            if status == 200:
                break
        except urllib.error.HTTPError as e:
            status, err = e.code, f"HTTP {e.code}"
        except ssl.SSLError:
            status, err = None, "SSL error"
        except Exception as e:
            status, err = None, f"{type(e).__name__}"

    # Still blocked after both methods -> we can't tell. Do NOT call it bad.
    if status in BLOCK_CODES or (status is None and err in (
            "RemoteDisconnected", "ConnectionResetError", "TimeoutError", "timeout")):
        row.update(grade_score=0, grade_status="UNVERIFIED",
                   grade_flags=f"Blocked bot check ({err or status}) - check by eye",
                   final_url=final)
        return row
    if status == 404 or (status and status >= 500):
        flags.append(f"HTTP {status}"); score += 60
    elif status is None and err == "SSL error":
        flags.append("SSL error"); score += 40
    elif status is None:
        flags.append(f"Unreachable ({err})"); score += 70

    if status == 200 and body:
        low = body.lower()

        if final.startswith("http://"):
            flags.append("No HTTPS"); score += 25
        if "viewport" not in low:
            flags.append("Not mobile-friendly"); score += 25
        if len(body) < 3000:
            flags.append("Very thin page"); score += 15
        if secs > 4:
            flags.append(f"Slow ({secs:.0f}s)"); score += 10

        years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)[^0-9]{0,30}((?:19|20)\d\d)", low)]
        if years:
            newest = max(years)
            age = THIS_YEAR - newest
            if age >= 4:
                flags.append(f"© {newest}"); score += 30
            elif age >= 2:
                flags.append(f"© {newest}"); score += 15

        gen = re.search(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)', low)
        if gen and "wordpress" in gen.group(1):
            flags.append("WordPress"); score += 5

        for key, label in BUILDERS.items():
            if key in host or key in low[:5000]:
                flags.append(f"{label} template"); score += 20
                break

        if any(t in low for t in ("<table", "<font ", "<marquee", "<center>")):
            flags.append("Table/old-HTML layout"); score += 15
        if "under construction" in low or "coming soon" in low:
            flags.append("Under construction"); score += 40
        if "flash" in low and "swf" in low:
            flags.append("Flash"); score += 30

    row.update(grade_score=min(score, 100), grade_status="GRADED",
               grade_flags="; ".join(flags) or "Looks modern",
               final_url=final)
    return row


def main():
    with open("leads.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"Grading {len(rows)} businesses...")

    with ThreadPoolExecutor(max_workers=12) as ex:
        graded = list(ex.map(grade, rows))

    graded.sort(key=lambda r: (-int(r["grade_score"]), -int(r.get("reviews") or 0)))

    fields = list(graded[0].keys()) if graded else []
    with open("leads_graded.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(graded)

    buckets = {"100 no site": 0, "60-99 poor": 0, "30-59 dated": 0,
               "0-29 fine": 0, "unverified": 0}
    for r in graded:
        s = int(r["grade_score"])
        if r["grade_status"] == "UNVERIFIED":
            buckets["unverified"] += 1
            continue
        k = ("100 no site" if s == 100 else "60-99 poor" if s >= 60
             else "30-59 dated" if s >= 30 else "0-29 fine")
        buckets[k] += 1
    print(buckets)


if __name__ == "__main__":
    main()
