import io
import json
import os
import re
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Dict, List
from zoneinfo import ZoneInfo

import requests


GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
EVENT_PREFIX = "UPLOAD_EVENT "
IST = ZoneInfo("Asia/Kolkata")

REPOS = [
    "iwilllearnquran/learnqurandaily",
    "iwilllearnuduquran/learnurduqurandaily",
    "iwilllearnenglishquran/learnenglishqurandaily",
]

if not GITHUB_TOKEN:
    raise ValueError("GITHUB_TOKEN is not set")

headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}


def now_utc() -> datetime:
    return datetime.now(UTC)


def get_repo_display_name(repo: str) -> str:
    return {
        "iwilllearnquran/learnqurandaily": "Learn Quran Daily (Arabic)",
        "iwilllearnuduquran/learnurduqurandaily": "Learn Urdu Quran Daily",
        "iwilllearnenglishquran/learnenglishqurandaily": "Learn English Quran Daily",
    }.get(repo, repo)


def get_workflow_runs(repo: str, hours: int = 48) -> List[Dict]:
    url = f"https://api.github.com/repos/{repo}/actions/runs"
    since = (now_utc() - timedelta(hours=hours)).isoformat()
    params = {"created": f">={since}", "per_page": 50}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        return response.json().get("workflow_runs", [])
    except Exception as exc:
        print(f"Error fetching runs for {repo}: {exc}")
        return []


def get_jobs_for_run(repo: str, run_id: int) -> List[Dict]:
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json().get("jobs", [])
    except Exception as exc:
        print(f"Error fetching jobs for {repo} run {run_id}: {exc}")
        return []


def get_job_logs(repo: str, job_id: int) -> str:
    url = f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs"
    try:
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code != 200:
            return ""

        content = response.content
        if content.startswith(b"PK"):
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                return "".join(
                    archive.read(file_name).decode("utf-8", errors="ignore")
                    for file_name in archive.namelist()
                )
        return content.decode("utf-8", errors="ignore")
    except Exception as exc:
        print(f"Error fetching logs for {repo} job {job_id}: {exc}")
        return ""


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        return None


def to_ist_label(value: str | None) -> str:
    dt = parse_iso_datetime(value)
    if not dt:
        return "?"
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")


def parse_upload_events(logs: str) -> List[Dict]:
    def parse_event_payload(payload: str) -> Dict | None:
        normalized = payload.strip()
        if not normalized:
            return None

        candidate_payloads = [normalized]

        trimmed_stars = normalized.strip("*").strip()
        if trimmed_stars and trimmed_stars != normalized:
            candidate_payloads.append(trimmed_stars)

        for candidate in list(candidate_payloads):
            if candidate and not candidate.startswith("{") and '"' in candidate:
                candidate_payloads.append("{" + candidate.lstrip("* ").rstrip("* ") + "}")

        for candidate in candidate_payloads:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

        recovered: Dict[str, object] = {}
        for key, value in re.findall(r'"([^"]+)":\s*"([^"]*)"', normalized):
            recovered[key] = value
        for key, value in re.findall(r'"([^"]+)":\s*(null|true|false|-?\d+(?:\.\d+)?)', normalized, flags=re.IGNORECASE):
            lowered = value.lower()
            if lowered == "null":
                recovered[key] = None
            elif lowered == "true":
                recovered[key] = True
            elif lowered == "false":
                recovered[key] = False
            elif "." in value:
                try:
                    recovered[key] = float(value)
                except ValueError:
                    recovered[key] = value
            else:
                try:
                    recovered[key] = int(value)
                except ValueError:
                    recovered[key] = value

        return recovered or None

    events: List[Dict] = []
    for line in logs.splitlines():
        prefix_index = line.find(EVENT_PREFIX)
        if prefix_index == -1:
            continue
        payload = line[prefix_index + len(EVENT_PREFIX):].strip()
        event = parse_event_payload(payload)
        if not event:
            continue
        events.append(event)
    return events


def extract_post_details_fallback(logs: str):
    logs_lower = logs.lower()
    match = re.search(r"surah (\d+), ayah (\d+)", logs_lower)
    ayah_key = f"{match.group(1)}:{match.group(2)}" if match else "?"

    time_match = re.search(r"\d{4}-\d{2}-\d{2}t\d{2}:\d{2}:\d{2}", logs_lower)
    if not time_match:
        return ayah_key, "?"

    dt = parse_iso_datetime(time_match.group(0))
    return ayah_key, dt.astimezone(IST).strftime("%Y-%m-%d %H:%M IST") if dt else "?"


def analyze_logs_fallback(logs: str, repo: str) -> Dict[str, Dict[str, str]]:
    logs_lower = logs.lower()
    instagram_api_status = (
        "success" if "instagram graph api validated" in logs_lower
        else "failed" if "instagram graph api validation failed" in logs_lower or "missing ig_graph_access_token" in logs_lower or "missing ig_user_id" in logs_lower
        else "unknown"
    )
    instagram_post_status = (
        "success" if "successfully uploaded video to instagram" in logs_lower
        else "failed" if "failed to upload video" in logs_lower
        else "unknown"
    )

    fb_debug_valid = re.search(r"\[fb debug\] token valid:\s*(true|false).*?expires:\s*([^\r\n]+)", logs, re.IGNORECASE)
    facebook_api_status = (
        "success" if fb_debug_valid and fb_debug_valid.group(1).lower() == "true"
        else "failed" if fb_debug_valid and fb_debug_valid.group(1).lower() == "false"
        else "skipped" if "facebook page upload skipped" in logs_lower
        else "success" if "facebook page upload successful" in logs_lower
        else "failed" if "facebook page upload failed" in logs_lower or "facebook page upload step failed" in logs_lower
        else "unknown"
    )
    facebook_post_status = (
        "success" if "facebook page upload successful" in logs_lower
        else "failed" if "facebook page upload failed" in logs_lower or "facebook page upload step failed" in logs_lower
        else "skipped" if "facebook page upload skipped" in logs_lower
        else "unknown"
    )

    youtube_api_status = (
        "failed" if "invalid_grant" in logs_lower or "client secret not found" in logs_lower or "no yt_client_secret_json" in logs_lower
        else "success" if "uploading" in logs_lower and "to youtube" in logs_lower
        else "unknown"
    )
    youtube_post_status = (
        "success" if "upload complete! video id" in logs_lower
        else "failed" if "invalid_grant" in logs_lower
        else "unknown"
    )

    facebook_api_event = {"status": facebook_api_status}
    if fb_debug_valid:
        expires_raw = fb_debug_valid.group(2).strip()
        if expires_raw and expires_raw.upper() != "NEVER":
            facebook_api_event["token_expires_at"] = expires_raw

    return {
        "instagram": {"api_check": {"status": instagram_api_status}, "post_result": {"status": instagram_post_status}},
        "facebook": {"api_check": facebook_api_event, "post_result": {"status": facebook_post_status}},
        "youtube": {"api_check": {"status": youtube_api_status}, "post_result": {"status": youtube_post_status}},
    }


def latest_event(events: List[Dict], event_name: str, platform: str) -> Dict:
    matching = [
        event for event in events
        if event.get("event") == event_name and event.get("platform") == platform
    ]
    if not matching:
        return {}
    matching.sort(key=lambda event: event.get("logged_at") or "")
    return matching[-1]


def build_run_summary(events: List[Dict], logs_list: List[str], repo: str) -> Dict:
    platforms = ["instagram", "facebook", "youtube"]
    fallback = analyze_logs_fallback("\n".join(logs_list), repo) if logs_list else {}
    summary = {
        "platforms": {
            platform: {
                "ayah_key": "?",
                "api_check": latest_event(events, "api_check", platform),
                "post_result": latest_event(events, "post_result", platform),
            }
            for platform in platforms
        },
    }

    if fallback:
        for platform in platforms:
            if not summary["platforms"][platform]["api_check"]:
                summary["platforms"][platform]["api_check"] = fallback[platform]["api_check"]
            if not summary["platforms"][platform]["post_result"]:
                summary["platforms"][platform]["post_result"] = fallback[platform]["post_result"]

    for platform in platforms:
        for event_name in ("post_result", "api_check"):
            event = summary["platforms"][platform][event_name]
            ayah_key = event.get("ayah_key") if event else None
            if ayah_key:
                summary["platforms"][platform]["ayah_key"] = ayah_key
                break

    fallback_ayah = "?"
    for logs in logs_list:
        ayah_key, _ = extract_post_details_fallback(logs)
        if ayah_key != "?":
            fallback_ayah = ayah_key
            break

    if fallback_ayah != "?":
        for platform in platforms:
            if summary["platforms"][platform]["ayah_key"] == "?":
                summary["platforms"][platform]["ayah_key"] = fallback_ayah

    return summary


def format_status(status: str | None) -> str:
    return {
        "success": "SUCCESS",
        "failed": "FAILED",
        "skipped": "SKIPPED",
        "unknown": "UNKNOWN",
    }.get((status or "unknown").lower(), (status or "unknown").upper())


def format_api_cell(event: Dict) -> str:
    if not event:
        return "UNKNOWN"

    status = format_status(event.get("status"))
    token_status = event.get("token_status")
    token_expires_at = event.get("token_expires_at")
    data_access_expires_at = event.get("data_access_expires_at")
    token_expired = event.get("token_expired")

    details = []
    if token_status:
        details.append(f"token={token_status}")
    if token_expired is not None:
        details.append(f"expired={'yes' if token_expired else 'no'}")
    if token_expires_at:
        expiry = to_ist_label(token_expires_at)
        details.append(f"exp={expiry if expiry != '?' else token_expires_at}")
    elif token_status:
        details.append("exp=unknown")
    if data_access_expires_at:
        data_expiry = to_ist_label(data_access_expires_at)
        details.append(f"data_exp={data_expiry if data_expiry != '?' else data_access_expires_at}")

    if details:
        return f"{status} ({', '.join(details)})"
    return status


def format_post_cell(event: Dict) -> str:
    if not event:
        return "UNKNOWN"

    status = format_status(event.get("status"))
    posted_at = event.get("posted_at") or event.get("logged_at")
    posted_label = to_ist_label(posted_at)

    if posted_label != "?" and status == "SUCCESS":
        return f"{status} ({posted_label})"
    if event.get("reason"):
        return f"{status} ({event['reason']})"
    if event.get("error") and status == "FAILED":
        return f"{status}"
    return status


def generate_repo_report(repo: str) -> str:
    display_name = get_repo_display_name(repo)
    runs = get_workflow_runs(repo)

    if not runs:
        return f"\n### {display_name}\n\nNo workflow runs.\n"

    report = f"\n## {display_name}\n\n"
    report += '| Run # | Workflow | <span style="color:#C13584">**IG Ayah**</span> | <span style="color:#C13584">**IG Post**</span> | <span style="color:#C13584">**IG Date**</span> | <span style="color:#FF0000">**YT Ayah**</span> | <span style="color:#FF0000">**YT Post**</span> | <span style="color:#FF0000">**YT Date**</span> | <span style="color:#1877F2">**FB Ayah**</span> | <span style="color:#1877F2">**FB Post**</span> | <span style="color:#1877F2">**FB Date**</span> | Link |\n'
    report += "|------:|----------|---------|---------|---------|---------|---------|---------|---------|---------|---------|------|\n"

    api_summary = {"instagram": {}, "facebook": {}, "youtube": {}}

    for run in runs[:10]:
        run_id = run["id"]
        run_number = run["run_number"]
        workflow_name = run.get("name", "Workflow")

        jobs = get_jobs_for_run(repo, run_id)
        logs_list: List[str] = []
        events: List[Dict] = []
        for job in jobs:
            logs = get_job_logs(repo, job["id"])
            if not logs:
                continue
            logs_list.append(logs)
            events.extend(parse_upload_events(logs))

        summary = build_run_summary(events, logs_list, repo)
        link = f"https://github.com/{repo}/actions/runs/{run_id}"

        # Extract post status and date for each platform
        ig_post_event = summary['platforms']['instagram']['post_result']
        yt_post_event = summary['platforms']['youtube']['post_result']
        fb_post_event = summary['platforms']['facebook']['post_result']

        ig_post_status = format_status(ig_post_event.get("status")) if ig_post_event else "UNKNOWN"
        yt_post_status = format_status(yt_post_event.get("status")) if yt_post_event else "UNKNOWN"
        fb_post_status = format_status(fb_post_event.get("status")) if fb_post_event else "UNKNOWN"

        # Color code the status
        ig_post_colored = f'<span class="{ig_post_status.lower()}">{ig_post_status}</span>'
        yt_post_colored = f'<span class="{yt_post_status.lower()}">{yt_post_status}</span>'
        fb_post_colored = f'<span class="{fb_post_status.lower()}">{fb_post_status}</span>'

        ig_date = to_ist_label(ig_post_event.get("posted_at") or ig_post_event.get("logged_at")) if ig_post_event else "?"
        yt_date = to_ist_label(yt_post_event.get("posted_at") or yt_post_event.get("logged_at")) if yt_post_event else "?"
        fb_date = to_ist_label(fb_post_event.get("posted_at") or fb_post_event.get("logged_at")) if fb_post_event else "?"

        # Store API info for summary
        for platform in ["instagram", "facebook", "youtube"]:
            api_event = summary['platforms'][platform]['api_check']
            if api_event:
                api_summary[platform][run_number] = api_event

        report += (
            f"| #{run_number} | {workflow_name} | "
            f"{summary['platforms']['instagram']['ayah_key']} | "
            f"{ig_post_colored} | {ig_date} | "
            f"{summary['platforms']['youtube']['ayah_key']} | "
            f"{yt_post_colored} | {yt_date} | "
            f"{summary['platforms']['facebook']['ayah_key']} | "
            f"{fb_post_colored} | {fb_date} | "
            f"[View]({link}) |\n"
        )

    # Add API status section
    report += "\n### API Status\n\n"
    
    for platform in ["instagram", "facebook", "youtube"]:
        report += f"**{platform.upper()}:**\n"
        if api_summary[platform]:
            latest_api = list(api_summary[platform].values())[-1]
            status = format_status(latest_api.get("status"))
            token_status = latest_api.get("token_status", "unknown")
            token_expires = latest_api.get("token_expires_at", "")
            
            if token_expires:
                token_exp_label = to_ist_label(token_expires)
                report += f"- Status: **{status}** | Token: **{token_status}** | Expires: **{token_exp_label}**\n"
            else:
                report += f"- Status: **{status}** | Token: **{token_status}**\n"
        else:
            report += f"- Status: **NO DATA**\n"
    
    return report


def generate_full_report():
    report = f"Generated: {now_utc().strftime('%Y-%m-%d %H:%M UTC')}\n\n"

    for repo in REPOS:
        report += generate_repo_report(repo)

    return report


def markdown_to_html(md: str):
    import markdown

    body = markdown.markdown(md, extensions=["tables"])
    return f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
                color: #e2e8f0;
                padding: 20px;
                min-height: 100vh;
            }}
            .container {{
                max-width: 1400px;
                margin: 0 auto;
            }}
            h2 {{
                color: #60a5fa;
                margin: 30px 0 15px 0;
                padding: 15px;
                background: rgba(30, 41, 59, 0.8);
                border-left: 4px solid #3b82f6;
                border-radius: 4px;
                font-size: clamp(1.3em, 5vw, 1.8em);
            }}
            h3 {{
                color: #93c5fd;
                margin: 20px 0 10px 0;
                font-size: clamp(1em, 4vw, 1.2em);
                padding: 10px 15px;
                background: rgba(30, 41, 59, 0.6);
                border-radius: 4px;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
                margin: 15px 0;
                font-size: clamp(0.75em, 2vw, 0.9em);
                background: rgba(30, 41, 59, 0.9);
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
            }}
            th {{
                background: linear-gradient(135deg, #1e40af 0%, #1e3a8a 100%);
                color: #e0e7ff;
                padding: clamp(8px, 2vw, 14px);
                text-align: left;
                font-weight: 600;
                border-bottom: 2px solid #3b82f6;
                white-space: nowrap;
            }}
            td {{
                padding: clamp(8px, 2vw, 12px);
                border-bottom: 1px solid #334155;
            }}
            tr:hover {{
                background: rgba(59, 130, 246, 0.1);
            }}
            tr:last-child td {{
                border-bottom: none;
            }}
            a {{
                color: #60a5fa;
                text-decoration: none;
                font-weight: 500;
                padding: 4px 8px;
                border-radius: 3px;
                background: rgba(96, 165, 250, 0.1);
                transition: all 0.2s ease;
            }}
            a:hover {{
                color: #93c5fd;
                background: rgba(96, 165, 250, 0.2);
            }}
            .success {{
                color: #86efac;
                font-weight: 700;
                background: rgba(134, 239, 172, 0.15);
                padding: 4px 8px;
                border-radius: 3px;
                display: inline-block;
            }}
            .failed {{
                color: #f87171;
                font-weight: 700;
                background: rgba(248, 113, 113, 0.15);
                padding: 4px 8px;
                border-radius: 3px;
                display: inline-block;
            }}
            .unknown {{
                color: #fbbf24;
                font-weight: 700;
                background: rgba(251, 191, 36, 0.15);
                padding: 4px 8px;
                border-radius: 3px;
                display: inline-block;
            }}
            .skipped {{
                color: #94a3b8;
                font-weight: 700;
                background: rgba(148, 163, 184, 0.15);
                padding: 4px 8px;
                border-radius: 3px;
                display: inline-block;
            }}
            .api-section {{
                background: rgba(30, 41, 59, 0.8);
                padding: 15px;
                margin: 15px 0;
                border-radius: 8px;
                border-left: 4px solid #8b5cf6;
            }}
            .api-section strong {{
                color: #a78bfa;
            }}
            .header {{
                text-align: center;
                margin-bottom: 30px;
            }}
            .header h1 {{
                color: #60a5fa;
                margin-bottom: 10px;
                font-size: clamp(1.8em, 8vw, 2.5em);
            }}
            .header p {{
                color: #94a3b8;
                font-size: clamp(0.9em, 3vw, 1.1em);
            }}
            /* Mobile responsive */
            @media (max-width: 768px) {{
                body {{
                    padding: 10px;
                }}
                .container {{
                    padding: 0;
                }}
                table {{
                    font-size: 0.8em;
                }}
                th, td {{
                    padding: 6px 8px;
                }}
                th {{
                    white-space: normal;
                }}
                h2 {{
                    margin: 20px 0 10px 0;
                    padding: 10px;
                }}
                .api-section {{
                    padding: 10px;
                    margin: 10px 0;
                }}
            }}
            @media (max-width: 480px) {{
                table {{
                    font-size: 0.7em;
                }}
                th, td {{
                    padding: 4px 6px;
                }}
                .header h1 {{
                    font-size: 1.5em;
                }}
                .header p {{
                    font-size: 0.85em;
                }}
                .success, .failed, .unknown, .skipped {{
                    padding: 2px 4px;
                    font-size: 0.9em;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Daily Upload Report</h1>
                <p>Quran Upload Automation Status</p>
            </div>
            {body}
        </div>
    </body>
    </html>
    """


def save_report(report: str):
    os.makedirs("site", exist_ok=True)
    html = markdown_to_html(report)
    with open("site/index.html", "w", encoding="utf-8") as file_obj:
        file_obj.write(html)
    print("HTML report generated")


if __name__ == "__main__":
    report = generate_full_report()
    save_report(report)
    print(report)
