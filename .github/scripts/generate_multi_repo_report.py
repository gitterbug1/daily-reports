import io
import json
import os
import re
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Dict, List
from zoneinfo import ZoneInfo

import requests


GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
EVENT_PREFIX = "UPLOAD_EVENT "
IST = ZoneInfo("Asia/Kolkata")
MQQ_POST_LOG = Path(__file__).parent / "mqq_post_log.json"

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


def get_workflow_display_name(repo: str) -> str:
    """Get display name for workflow based on repo"""
    return {
        "iwilllearnquran/learnqurandaily": "Quran Verse Generator (IG + YT)",
        "iwilllearnuduquran/learnurduqurandaily": "Urdu Quran Generator (IG + YT)",
        "iwilllearnenglishquran/learnenglishqurandaily": "English Quran Generator (IG + YT)",
    }.get(repo, "Quran Generator")


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
    return dt.astimezone(IST).strftime("%d/%m %I:%M %p")


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
    report += '| Run # | IG Ayah | IG Post | IG Date | FB Ayah | FB Post | FB Date | YT Ayah | YT Post | YT Date | Link |\n'
    report += "|------:|---------|---------|---------|---------|---------|---------|---------|---------|---------|------|\n"

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
            f"| #{run_number} | "
            f"{summary['platforms']['instagram']['ayah_key']} | "
            f"{ig_post_colored} | {ig_date} | "
            f"{summary['platforms']['facebook']['ayah_key']} | "
            f"{fb_post_colored} | {fb_date} | "
            f"{summary['platforms']['youtube']['ayah_key']} | "
            f"{yt_post_colored} | {yt_date} | "
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


def _mqq_status_cell(platform: dict) -> str:
    status = (platform.get("status") or "unknown").lower()
    label = {"success": "SUCCESS", "failed": "FAILED", "skipped": "SKIPPED"}.get(status, "UNKNOWN")
    return f'<span class="{status}">{label}</span>'


def _mqq_link_cell(platform: dict, label: str) -> str:
    url = platform.get("url")
    if url:
        return f"[{label}]({url})"
    return "-"


def _mqq_token_cell(platform: dict) -> str:
    status = platform.get("token_status", "unknown")
    expires = platform.get("token_expires_at")
    if expires:
        try:
            dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            exp_label = dt.astimezone(IST).strftime("%d/%m/%y %H:%M")
            now = datetime.now(UTC)
            expired = dt < now
            tag = "failed" if expired else "success"
            exp_str = f"exp: {exp_label}"
        except Exception:
            tag = "unknown"
            exp_str = f"exp: {expires[:10]}"
        return f'<span class="{tag}">{status} ({exp_str})</span>'
    tag = "success" if status == "valid" else ("failed" if status == "invalid" else "unknown")
    return f'<span class="{tag}">{status}</span>'


def generate_mqq_reel_report() -> str:
    if not MQQ_POST_LOG.exists():
        return "\n## My Quran Quest (Word Reels)\n\nNo post log found. Run autopost_sample_reels.py first.\n"

    try:
        entries: List[Dict] = json.loads(MQQ_POST_LOG.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"\n## My Quran Quest (Word Reels)\n\nFailed to read post log: {exc}\n"

    if not entries:
        return "\n## My Quran Quest (Word Reels)\n\nPost log is empty.\n"

    recent = list(reversed(entries[-20:]))

    report = "\n## My Quran Quest (Word Reels)\n\n"
    report += "| Date | Rank | Word | IG | FB | YT | IG Link | FB Link | YT Link | YT Token |\n"
    report += "|------|-----:|------|----|----|----|---------|---------|---------|-----------|\n"

    for entry in recent:
        date = entry.get("date", "?")
        rank = entry.get("rank", "?")
        arabic = entry.get("arabic", "")
        translit = entry.get("transliteration", "")
        meaning = entry.get("meaning", "")
        word_cell = f"{arabic} ({translit}) = {meaning}" if translit else f"{arabic} = {meaning}"

        ig = entry.get("instagram") or {}
        fb = entry.get("facebook") or {}
        yt = entry.get("youtube") or {}

        report += (
            f"| {date} | {rank} | {word_cell} | "
            f"{_mqq_status_cell(ig)} | {_mqq_status_cell(fb)} | {_mqq_status_cell(yt)} | "
            f"{_mqq_link_cell(ig, 'IG')} | {_mqq_link_cell(fb, 'FB')} | {_mqq_link_cell(yt, 'YT')} | "
            f"{_mqq_token_cell(yt)} |\n"
        )

    # Token status summary
    last = entries[-1]
    ig_last = last.get("instagram") or {}
    fb_last = last.get("facebook") or {}
    yt_last = last.get("youtube") or {}

    report += "\n### Token Status (last post)\n\n"
    report += f"**INSTAGRAM:** {_mqq_token_cell({'token_status': ig_last.get('token_status', 'unknown')})}\n\n"
    report += f"**FACEBOOK:** {_mqq_token_cell({'token_status': fb_last.get('token_status', 'unknown')})}\n\n"
    report += f"**YOUTUBE:** {_mqq_token_cell(yt_last)}\n"

    return report


def generate_full_report():
    report = f"Generated: {now_utc().strftime('%Y-%m-%d %H:%M UTC')}\n\n"

    for repo in REPOS:
        report += generate_repo_report(repo)

    report += generate_mqq_reel_report()

    return report


def markdown_to_html(md: str):
    import markdown

    body = markdown.markdown(md, extensions=["tables"])
    return """
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
            .table-wrapper {{
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
                border-radius: 8px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
                margin: 15px 0;
                width: 100%;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
                min-width: 100%;
                font-size: clamp(0.75em, 2vw, 0.9em);
                background: rgba(30, 41, 59, 0.9);
                border-radius: 8px;
                overflow: visible;
            }}
            th {{
                color: #ffffff;
                padding: clamp(8px, 2vw, 14px);
                text-align: left;
                font-weight: 700;
                border-bottom: 2px solid #3b82f6;
                white-space: nowrap;
                text-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
            }}
            /* Instagram header (cols 2-4) */
            th:nth-child(2), th:nth-child(3), th:nth-child(4) {{
                background: linear-gradient(135deg, #DA88B3 0%, #C8739E 100%);
                border-right: 1px solid rgba(218, 136, 179, 0.3);
            }}

            /* Facebook header (cols 5-7) */
            th:nth-child(5), th:nth-child(6), th:nth-child(7) {{
                background: linear-gradient(135deg, #6BA3E5 0%, #5A8FD1 100%);
                border-right: 1px solid rgba(107, 163, 229, 0.3);
            }}

            /* YouTube header (cols 8-10) */
            th:nth-child(8), th:nth-child(9), th:nth-child(10) {{
                background: linear-gradient(135deg, #FF7F7F 0%, #E85555 100%);
                border-right: 1px solid rgba(255, 127, 127, 0.3);
            }}

            /* Run # header (col 1 only) */
            th:nth-child(1) {{
                background: linear-gradient(135deg, #1e40af 0%, #1e3a8a 100%);
                border-right: 2px solid rgba(255,255,255,0.2);
            }}

            /* Link header (col 11) */
            th:nth-child(11) {{
                background: linear-gradient(135deg, #1e40af 0%, #1e3a8a 100%);
                border-left: 2px solid rgba(255,255,255,0.2);
            }}

            td {{
                padding: clamp(8px, 2vw, 12px);
                border-bottom: 1px solid #334155;
            }}

            /* Instagram cells (cols 2-4) */
            td:nth-child(2), td:nth-child(3), td:nth-child(4) {{
                background: rgba(218, 136, 179, 0.06);
                border-right: 1px solid rgba(218, 136, 179, 0.15);
            }}

            /* Facebook cells (cols 5-7) */
            td:nth-child(5), td:nth-child(6), td:nth-child(7) {{
                background: rgba(107, 163, 229, 0.06);
                border-right: 1px solid rgba(107, 163, 229, 0.15);
            }}

            /* YouTube cells (cols 8-10) */
            td:nth-child(8), td:nth-child(9), td:nth-child(10) {{
                background: rgba(255, 127, 127, 0.06);
                border-right: 1px solid rgba(255, 127, 127, 0.15);
            }}

            /* Hover effects */
            tr:hover td:nth-child(2),
            tr:hover td:nth-child(3),
            tr:hover td:nth-child(4) {{
                background: rgba(218, 136, 179, 0.12);
            }}

            tr:hover td:nth-child(5),
            tr:hover td:nth-child(6),
            tr:hover td:nth-child(7) {{
                background: rgba(107, 163, 229, 0.12);
            }}

            tr:hover td:nth-child(8),
            tr:hover td:nth-child(9),
            tr:hover td:nth-child(10) {{
                background: rgba(255, 127, 127, 0.12);
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
            @media (max-width: 1200px) {{
                .table-wrapper {{
                    overflow-x: auto;
                    -webkit-overflow-scrolling: touch;
                }}
            }}
            @media (max-width: 768px) {{
                body {{
                    padding: 12px;
                }}
                .container {{
                    padding: 0;
                    max-width: 100%;
                }}
                table {{
                    font-size: 0.7em;
                    min-width: 100%;
                }}
                th, td {{
                    padding: 6px 4px;
                }}
                th {{
                    white-space: nowrap;
                    font-size: 0.85em;
                }}
                h2 {{
                    margin: 15px 0 10px 0;
                    padding: 10px;
                    font-size: 1.1em;
                }}
                h3 {{
                    font-size: 0.95em;
                    margin: 10px 0 8px 0;
                }}
                .api-section {{
                    padding: 10px;
                    margin: 10px 0;
                    font-size: 0.8em;
                }}
                .header h1 {{
                    font-size: 1.6em;
                }}
                .header p {{
                    font-size: 0.9em;
                }}
            }}
            @media (max-width: 480px) {{
                body {{
                    padding: 8px;
                }}
                .container {{
                    max-width: 100%;
                }}
                table {{
                    font-size: 0.6em;
                }}
                th, td {{
                    padding: 4px 3px;
                }}
                .header h1 {{
                    font-size: 1.3em;
                    margin-bottom: 5px;
                }}
                .header p {{
                    font-size: 0.75em;
                }}
                .success, .failed, .unknown, .skipped {{
                    padding: 2px 3px;
                    font-size: 0.65em;
                }}
                h2 {{
                    font-size: 1em;
                    padding: 8px;
                    margin: 10px 0 8px 0;
                }}
                h3 {{
                    font-size: 0.85em;
                    margin: 8px 0 5px 0;
                }}
                a {{
                    padding: 2px 4px;
                    font-size: 0.85em;
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
    """.format(body=body)


def save_report(report: str):
    os.makedirs("site", exist_ok=True)
    html = markdown_to_html(report)
    # Wrap ALL tables in scrollable container
    html = html.replace('<table>', '<div class="table-wrapper"><table>')
    html = html.replace('</table>', '</table></div>')
    with open("site/index.html", "w", encoding="utf-8") as file_obj:
        file_obj.write(html)
    print("HTML report generated")


if __name__ == "__main__":
    report = generate_full_report()
    save_report(report)
    print(report)
