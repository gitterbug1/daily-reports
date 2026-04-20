#!/usr/bin/env python3
"""
Meta Token Generator (Advanced)

Features:
- Takes APP_ID + APP_SECRET as input
- Converts short-lived → long-lived token
- Fetches Page + Instagram details
- Saves separate token files:
    facebook_<page_name>_token.json
    instagram_<username>_token.json
"""

import requests
import json
import re
from pathlib import Path

GRAPH_VERSION = "v22.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"


def clean_filename(name: str) -> str:
    """Make safe filename"""
    name = name.lower().strip()
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^a-z0-9_]", "", name)
    return name


def exchange_for_long_lived_token(app_id, app_secret, short_token):
    print("\n🔄 Exchanging for long-lived token...")

    url = f"{BASE_URL}/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_token,
    }

    res = requests.get(url, params=params)
    data = res.json()

    if "access_token" not in data:
        raise Exception(f"❌ Token exchange failed:\n{data}")

    print("✅ Long-lived token generated")
    return data["access_token"]


def get_pages(long_token):
    print("\n📄 Fetching Facebook pages...")

    res = requests.get(
        f"{BASE_URL}/me/accounts",
        params={"access_token": long_token},
    )
    data = res.json()

    if "data" not in data or not data["data"]:
        raise Exception(f"❌ No pages found:\n{data}")

    return data["data"]


def get_instagram_details(page_id, page_token):
    print("\n📸 Fetching Instagram details...")

    res = requests.get(
        f"{BASE_URL}/{page_id}",
        params={
            "fields": "instagram_business_account",
            "access_token": page_token,
        },
    )
    data = res.json()

    ig_id = data.get("instagram_business_account", {}).get("id")

    if not ig_id:
        print("⚠️ No Instagram linked.")
        return None, None

    # Get username
    res2 = requests.get(
        f"{BASE_URL}/{ig_id}",
        params={
            "fields": "username",
            "access_token": page_token,
        },
    )
    ig_data = res2.json()

    username = ig_data.get("username")

    return ig_id, username


def save_json(filename, data):
    Path(filename).write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"💾 Saved → {filename}")


def main():
    print("===================================")
    print(" Meta Token Generator (Advanced)")
    print("===================================")

    app_id = input("\n🔧 Enter APP_ID:\n> ").strip()
    app_secret = input("\n🔐 Enter APP_SECRET:\n> ").strip()
    short_token = input("\n🔑 Paste SHORT-LIVED USER TOKEN:\n> ").strip()

    # Step 1
    long_token = exchange_for_long_lived_token(app_id, app_secret, short_token)

    # Step 2
    pages = get_pages(long_token)

    print("\n📋 Pages:")
    for i, p in enumerate(pages):
        print(f"[{i}] {p['name']} (ID: {p['id']})")

    choice = int(input("\n👉 Select page index: "))
    page = pages[choice]

    page_id = page["id"]
    page_name = page["name"]
    page_token = page["access_token"]

    # Step 3
    ig_id, ig_username = get_instagram_details(page_id, page_token)

    # Clean names
    safe_page = clean_filename(page_name)
    safe_ig = clean_filename(ig_username) if ig_username else "no_instagram"

    # Save Facebook token
    fb_file = f"facebook_{safe_page}_token.json"
    save_json(fb_file, {
        "page_name": page_name,
        "FB_PAGE_ID": page_id,
        "FB_PAGE_TOKEN": page_token,
        "LONG_LIVED_USER_TOKEN": long_token
    })

    # Save Instagram token
    ig_file = f"instagram_{safe_ig}_token.json"
    save_json(ig_file, {
        "instagram_username": ig_username,
        "IG_USER_ID": ig_id,
        "FB_PAGE_TOKEN": page_token,
        "LONG_LIVED_USER_TOKEN": long_token
    })

    print("\n====================================")
    print("✅ DONE")
    print("====================================")

    print(f"\nFacebook file: {fb_file}")
    print(f"Instagram file: {ig_file}")

    print("\n⚠️ Token valid ~60 days")


if __name__ == "__main__":
    main()