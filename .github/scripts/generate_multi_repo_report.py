import os
import requests
import zipfile
import io
from datetime import datetime, timedelta, UTC
from typing import Dict, List

# ========================
# CONFIG
# ========================

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

REPOS = [
    "iwilllearnquran/learnqurandaily",
    "iwilllearnuduquran/learnurduqurandaily",
    "iwilllearnenglishquran/learnenglishqurandaily"
]

if not GITHUB_TOKEN:
    raise ValueError("❌ GITHUB_TOKEN is not set")

headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

# ========================
# HELPERS
# ========================

def now_utc():
    return datetime.now(UTC)

def get_repo_display_name(repo: str) -> str:
    return {
        "iwilllearnquran/learnqurandaily": "Learn Quran Daily (Arabic)",
        "iwilllearnuduquran/learnurduqurandaily": "Learn Urdu Quran Daily",
        "iwilllearnenglishquran/learnenglishqurandaily": "Learn English Quran Daily"
    }.get(repo, repo)

# ========================
# API CALLS
# ========================

def get_workflow_runs(repo: str, hours: int = 24) -> List[Dict]:
    url = f"https://api.github.com/repos/{repo}/actions/runs"

    since = (now_utc() - timedelta(hours=hours)).isoformat()

    params = {
        "created": f">={since}",
        "per_page": 50
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)

        if response.status_code == 401:
            raise Exception("401 Unauthorized → Check your GITHUB_TOKEN permissions")

        response.raise_for_status()
        return response.json().get("workflow_runs", [])

    except Exception as e:
        print(f"❌ Error fetching runs for {repo}: {e}")
        return []

def get_jobs_for_run(repo: str, run_id: int) -> List[Dict]:
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json().get("jobs", [])

    except Exception as e:
        print(f"❌ Error fetching jobs for run {run_id}: {e}")
        return []

import requests
import zipfile
import io

def get_job_logs(repo: str, job_id: int) -> str:
    url = f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs"

    try:
        response = requests.get(url, headers=headers, timeout=20, allow_redirects=True)

        if response.status_code != 200:
            print(f"⚠️ Failed to fetch logs: {response.status_code}")
            return ""

        content = response.content

        # -----------------------
        # CASE 1: ZIP file
        # -----------------------
        if content.startswith(b'PK'):
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                logs = ""
                for file in z.namelist():
                    logs += z.read(file).decode("utf-8", errors="ignore")
                return logs

        # -----------------------
        # CASE 2: Plain text logs (YOUR CASE)
        # -----------------------
        else:
            return content.decode("utf-8", errors="ignore")

    except Exception as e:
        print(f"❌ Error fetching logs for job {job_id}: {e}")
        return ""

# ========================
# LOG ANALYSIS
# ========================

def analyze_logs(logs: str):
    logs_lower = logs.lower()

    # Instagram
    if "instagram graph api validated" in logs_lower:
        ig = "✅ SUCCESS"
    else:
        ig = "⚠️ UNKNOWN"

    # Facebook
    if "facebook" in logs_lower and "error" in logs_lower:
        fb = "❌ FAILED"
    elif "facebook" in logs_lower:
        fb = "✅ SUCCESS"
    else:
        fb = "⚠️ UNKNOWN"

    # YouTube
    if "invalid_grant" in logs_lower:
        yt = "❌ FAILED"
    elif "youtube" in logs_lower and "error" in logs_lower:
        yt = "❌ FAILED"
    elif "youtube" in logs_lower:
        yt = "✅ SUCCESS"
    else:
        yt = "⚠️ UNKNOWN"

    return {
        "instagram": ig,
        "facebook": fb,
        "youtube": yt
    }
# ========================
# REPORT GENERATION
# ========================

def generate_repo_report(repo: str) -> str:
    display_name = get_repo_display_name(repo)
    runs = get_workflow_runs(repo)

    if not runs:
        return f"\n### {display_name}\n\nNo workflow runs in the last 24 hours.\n"

    report = f"\n### {display_name}\n\n"
    report += "| Run # | Status | Date | Instagram | Facebook | YouTube | Link |\n"
    report += "|-------|--------|------|-----------|----------|---------|------|\n"

    for run in runs[:10]:
        run_id = run["id"]
        run_number = run["run_number"]
        status = "✅ PASSED" if run["conclusion"] == "success" else "❌ FAILED"
        created_at = run["created_at"][:10]

        jobs = get_jobs_for_run(repo, run_id)

        ig_status = "⚠️ UNKNOWN"
        fb_status = "⚠️ UNKNOWN"
        yt_status = "⚠️ UNKNOWN"

        for job in jobs:
            logs = get_job_logs(repo, job["id"])
            analysis = analyze_logs(logs)

            if analysis["instagram"] == "❌ FAILED":
                ig_status = "❌ FAILED"
            elif ig_status != "❌ FAILED":
                ig_status = analysis["instagram"]

            if analysis["facebook"] == "❌ FAILED":
                fb_status = "❌ FAILED"
            elif fb_status != "❌ FAILED":
                fb_status = analysis["facebook"]

            if analysis["youtube"] == "❌ FAILED":
                yt_status = "❌ FAILED"
            elif yt_status != "❌ FAILED":
                yt_status = analysis["youtube"]

        link = f"https://github.com/{repo}/actions/runs/{run_id}"

        report += f"| #{run_number} | {status} | {created_at} | {ig_status} | {fb_status} | {yt_status} | [View]({link}) |\n"

    return report

def generate_full_report() -> str:
    report = "# 📊 Daily Upload Report\n\n"
    report += f"**Generated:** {now_utc().strftime('%Y-%m-%d %H:%M UTC')}\n"
    report += f"**Repositories:** {len(REPOS)}\n"

    for repo in REPOS:
        report += generate_repo_report(repo)

    report += "\n### Legend\n\n"
    report += "- ✅ SUCCESS: Upload completed\n"
    report += "- ❌ FAILED: Upload failed\n"
    report += "- ⚠️ UNKNOWN: Could not determine\n"

    return report

def save_report(report: str):
    os.makedirs("reports", exist_ok=True)

    date_str = now_utc().strftime("%Y-%m-%d")
    path = f"reports/report-{date_str}.md"

    with open(path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"✅ Report saved to {path}")

# ========================
# MAIN
# ========================

if __name__ == "__main__":
    report = generate_full_report()
    save_report(report)
    print(report)