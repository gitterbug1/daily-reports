"""
===========================================
YouTube OAuth Token Generator (One-Time Use)
===========================================

WHAT THIS SCRIPT DOES:
- Opens a browser and asks you to log into your Google account
- Requests permission to access your YouTube account
- Generates OAuth credentials (access + refresh token)
- Saves them into a JSON file for later use (CI / automation)

WHY YOU NEED THIS:
- Your main script (GitHub Actions) cannot log in via browser
- So you generate the token once locally
- Then reuse it forever in automation

-------------------------------------------
STEPS TO USE THIS SCRIPT:
-------------------------------------------

1. Make sure Python is installed (you already have `py` working)

2. Install required libraries:
   Run in terminal:
   py -m pip install google-auth google-auth-oauthlib google-api-python-client

3. Ensure this file exists:
   - CLIENT_SECRET_FILE (downloaded from Google Cloud Console)

4. Run this script:
   py generate_token.py

5. Browser will open:
   - Select your Google account
   - Click "Allow"

6. After success:
   - A file will be created: youtube_api_oauth_credentials.json

7. Open that file and confirm:
   It contains:
   "refresh_token": "..."

8. Copy entire file content and paste into GitHub Secret:
   YT_TOKEN_JSON

-------------------------------------------
IMPORTANT NOTES:
-------------------------------------------
- Only run this script when your token breaks
- Running it repeatedly creates new tokens (can invalidate old ones)
- The refresh_token is the most important part
"""

from google_auth_oauthlib.flow import InstalledAppFlow
from pathlib import Path

CLIENT_SECRET_FILE = "C:\\Users\\fff\\Downloads\\daily_english_quran_yt_client_id_and_client_secret.json"
TOKEN_FILE = "english_youtube_api_oauth_credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload"
]

flow = InstalledAppFlow.from_client_secrets_file(
    CLIENT_SECRET_FILE,
    SCOPES
)

creds = flow.run_local_server(
    port=0,
    access_type='offline',   # ensures refresh token
    prompt='consent'         # forces refresh token
)

Path(TOKEN_FILE).write_text(creds.to_json(), encoding="utf-8")

print("✅ Token generated and saved to yt_token.json")
print("Refresh token present:", bool(creds.refresh_token))