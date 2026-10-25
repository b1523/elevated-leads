#!/usr/bin/env python3
"""
Screenshot candidate websites (desktop + mobile) with a real browser so they
can be reviewed by eye. Reads leads_graded.csv, writes screens/*.jpg and
screens/index.csv.
"""
import csv
import os
import re
import sys

from playwright.sync_api import sync_playwright

MAX_SITES = int(os.environ.get("MAX_SITES", "60"))


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def pick(rows):
    chosen = []
    for r in rows:
        if not r.get("website"):
            continue
        if "QLD" not in r["address"] and "Queensland" not in r["address"]:
            continue
        reviews = int(r.get("reviews") or 0)
        score = int(r["grade_score"])
        if r["grade_status"] == "UNVERIFIED" and reviews >= 15:
            chosen.append(r)
        elif r["grade_status"] == "GRADED" and 30 <= score < 95 and reviews >= 15:
            chosen.append(r)
    chosen.sort(key=lambda r: (r["grade_status"] != "UNVERIFIED",
                               -int(r["grade_score"]), -int(r["reviews"] or 0)))
    return chosen[:MAX_SITES]


def main():
    rows = list(csv.DictReader(open("leads_graded.csv", encoding="utf-8")))
    sites = pick(rows)
    os.makedirs("screens", exist_ok=True)
    print(f"Screenshotting {len(sites)} sites")
    out = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for i, r in enumerate(sites, 1):
            name, url = r["name"], r["website"].split("?")[0]
            s = slug(name)
            status = "ok"
            try:
                for tag, vp, mobile in (("d", {"width": 1280, "height": 800}, False),
                                        ("m", {"width": 390, "height": 844}, True)):
                    ctx = browser.new_context(
                        viewport=vp, is_mobile=mobile, device_scale_factor=1,
                        locale="en-AU",
                        user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                                    "AppleWebKit/605.1.15 Mobile/15E148") if mobile else None)
                    page = ctx.new_page()
                    page.goto(url, timeout=25000, wait_until="domcontentloaded")
                    page.wait_for_timeout(2500)
                    page.screenshot(path=f"screens/{s}-{tag}.jpg", type="jpeg", quality=55)
                    ctx.close()
            except Exception as e:
                status = f"fail: {type(e).__name__}"
            print(f"[{i}/{len(sites)}] {name[:34]:34} {status}")
            out.append({"slug": s, "name": name, "url": url, "phone": r["phone"],
                        "reviews": r["reviews"], "grade": r["grade_score"],
                        "grade_status": r["grade_status"], "flags": r["grade_flags"],
                        "shot": status})
        browser.close()
    with open("screens/index.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader(); w.writerows(out)


if __name__ == "__main__":
    main()
