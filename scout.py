#!/usr/bin/env python3
"""
Elevated Digital Co — Lead Scout
Runs on GitHub Actions. Reads searches.json, queries the Google Places API,
writes leads.csv.

The API key comes from the PLACES_API_KEY repository secret.
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

ENDPOINT = "https://places.googleapis.com/v1/places:searchText"

FIELDS = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.nationalPhoneNumber,"
    "places.websiteUri,"
    "places.rating,"
    "places.userRatingCount,"
    "places.primaryTypeDisplayName,"
    "places.businessStatus,"
    "nextPageToken"
)

API_KEY = os.environ.get("PLACES_API_KEY", "").strip()


def load_config():
    with open("searches.json", encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("searches"):
        sys.exit("searches.json has no 'searches' list.")
    return cfg


def call_places(query, page_token=None):
    payload = {"textQuery": query, "regionCode": "AU"}
    if page_token:
        payload["pageToken"] = page_token

    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode("utf-8"), method="POST"
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Goog-Api-Key", API_KEY)
    req.add_header("X-Goog-FieldMask", FIELDS)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        print(f"    HTTP {e.code}: {detail}")
        return {}
    except Exception as e:
        print(f"    ERROR: {type(e).__name__}: {e}")
        return {}


def suburb_from_address(addr):
    """Pull the suburb out of an Australian formatted address."""
    if not addr:
        return ""
    parts = [p.strip() for p in addr.split(",")]
    for part in reversed(parts):
        tokens = part.split()
        # e.g. "Ashgrove QLD 4060" -> Ashgrove
        if len(tokens) >= 3 and tokens[-1].isdigit() and len(tokens[-1]) == 4:
            return " ".join(tokens[:-2])
    return parts[-2] if len(parts) >= 2 else ""


def text(node):
    return node.get("text", "") if isinstance(node, dict) else ""


def main():
    if not API_KEY:
        sys.exit(
            "PLACES_API_KEY is empty. Add it under "
            "Settings > Secrets and variables > Actions."
        )

    cfg = load_config()
    max_pages = int(cfg.get("max_pages_per_search", 3))
    only_no_website = bool(cfg.get("only_no_website", True))
    min_reviews = int(cfg.get("min_reviews", 0))

    found = {}

    for query in cfg["searches"]:
        print(f"Searching: {query}")
        token = None
        for page in range(max_pages):
            data = call_places(query, token)
            places = data.get("places", [])
            if not places:
                break

            new = 0
            for p in places:
                pid = p.get("id")
                if not pid or pid in found:
                    continue
                new += 1
                addr = p.get("formattedAddress", "")
                found[pid] = {
                    "name": text(p.get("displayName")),
                    "suburb": suburb_from_address(addr),
                    "category": text(p.get("primaryTypeDisplayName")),
                    "phone": p.get("nationalPhoneNumber", ""),
                    "website": p.get("websiteUri", ""),
                    "rating": p.get("rating", ""),
                    "reviews": p.get("userRatingCount", 0) or 0,
                    "address": addr,
                    "status": p.get("businessStatus", ""),
                    "found_via": query,
                }
            print(f"  page {page + 1}: {len(places)} results, {new} new")

            token = data.get("nextPageToken")
            if not token:
                break
            time.sleep(2)  # next-page tokens need a moment to become valid

    rows = list(found.values())
    total = len(rows)

    rows = [r for r in rows if r["status"] != "CLOSED_PERMANENTLY"]
    rows = [r for r in rows if r["phone"]]
    if min_reviews:
        rows = [r for r in rows if r["reviews"] >= min_reviews]
    if only_no_website:
        rows = [r for r in rows if not r["website"]]

    rows.sort(key=lambda r: -r["reviews"])

    with open("leads.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "name", "suburb", "category", "phone", "website",
                "rating", "reviews", "address", "found_via",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n{total} unique businesses found, {len(rows)} kept after filters.")
    print("Top 15:")
    for r in rows[:15]:
        print(f"  {r['name']} | {r['suburb']} | {r['phone']} "
              f"| {r['rating']}* ({r['reviews']})")


if __name__ == "__main__":
    main()
