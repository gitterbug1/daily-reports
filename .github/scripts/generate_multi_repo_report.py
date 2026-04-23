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
REPORT_REPO = "gitterbug1/daily-reports"
REPORT_WORKFLOW_FILE = "multi-repo-daily-report.yml"
REPORT_WORKFLOW_URL = f"https://github.com/{REPORT_REPO}/actions/workflows/{REPORT_WORKFLOW_FILE}"

REPOS = [
    "iwilllearnquran/learnqurandaily",
    "iwilllearnuduquran/learnurduqurandaily",
    "iwilllearnenglishquran/learnenglishqurandaily",
]

MQQ_REPO = "myquranquest-gh/MQQ-Autopost"

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


def get_latest_workflow_run(repo: str, workflow_file: str) -> Dict:
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/runs"
    params = {"per_page": 1}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        runs = response.json().get("workflow_runs", [])
        return runs[0] if runs else {}
    except Exception as exc:
        print(f"Error fetching latest workflow run for {repo}/{workflow_file}: {exc}")
        return {}


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


def _status_class(status: str | None) -> str:
    return (status or "unknown").lower()


def _workflow_status_meta(run: Dict) -> Dict[str, str]:
    if not run:
        return {
            "status_class": "unknown",
            "label": "UNKNOWN",
            "meta": "No workflow runs found yet",
            "url": REPORT_WORKFLOW_URL,
        }

    status = (run.get("status") or "unknown").lower()
    conclusion = (run.get("conclusion") or "").lower()

    if status in {"queued", "in_progress", "waiting", "requested", "pending"}:
        status_class = "running"
        label = status.replace("_", " ").upper()
    elif conclusion:
        status_class = {
            "success": "success",
            "failure": "failed",
            "cancelled": "failed",
            "timed_out": "failed",
            "skipped": "skipped",
        }.get(conclusion, "unknown")
        label = {
            "failure": "FAILED",
            "cancelled": "CANCELLED",
            "timed_out": "TIMED OUT",
            "skipped": "SKIPPED",
            "success": "SUCCESS",
        }.get(conclusion, conclusion.upper())
    else:
        status_class = "unknown"
        label = status.upper()

    updated_at = run.get("updated_at") or run.get("created_at")
    when = to_ist_label(updated_at)
    run_number = run.get("run_number")
    meta = f"Run #{run_number} · {when}" if run_number else when

    return {
        "status_class": status_class,
        "label": label,
        "meta": meta,
        "url": run.get("html_url") or REPORT_WORKFLOW_URL,
    }


def _ayah_cell(ayah_key: str, url: str | None) -> str:
    if url and ayah_key != "?":
        return f'<a href="{url}" target="_blank" rel="noopener" class="ayah-link">{ayah_key}</a>'
    return ayah_key


def generate_repo_html(repo: str, section_id: str) -> str:
    display_name = get_repo_display_name(repo)
    runs = get_workflow_runs(repo)

    if not runs:
        return f'<div class="repo-section"><h2>{display_name}</h2><p class="no-data">No workflow runs found.</p></div>'

    rows_html = ""
    for idx, run in enumerate(runs[:10]):
        run_id = run["id"]
        run_number = run["run_number"]

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

        ig = summary["platforms"]["instagram"]
        fb = summary["platforms"]["facebook"]
        yt = summary["platforms"]["youtube"]

        ig_event = ig["post_result"]
        fb_event = fb["post_result"]
        yt_event = yt["post_result"]

        ig_status = _status_class(ig_event.get("status")) if ig_event else "unknown"
        fb_status = _status_class(fb_event.get("status")) if fb_event else "unknown"
        yt_status = _status_class(yt_event.get("status")) if yt_event else "unknown"

        ig_date = to_ist_label(ig_event.get("posted_at") or ig_event.get("logged_at")) if ig_event else "?"
        fb_date = to_ist_label(fb_event.get("posted_at") or fb_event.get("logged_at")) if fb_event else "?"
        yt_date = to_ist_label(yt_event.get("posted_at") or yt_event.get("logged_at")) if yt_event else "?"

        ig_url = ig_event.get("url") if ig_event else None
        fb_url = fb_event.get("url") if fb_event else None
        yt_url = yt_event.get("url") if yt_event else None

        # Determine overall row status for filtering
        statuses = [ig_status, fb_status, yt_status]
        if "failed" in statuses:
            row_filter = "failed"
        elif all(s == "success" for s in statuses):
            row_filter = "success"
        else:
            row_filter = "other"

        hidden = ' style="display:none"' if idx >= 3 else ""
        rows_html += f'''<tr class="data-row" data-filter="{row_filter}" data-idx="{idx}"{hidden}>
<td class="run-num">#{run_number}</td>
<td class="ayah">{_ayah_cell(ig["ayah_key"], ig_url)}</td>
<td><span class="badge {ig_status}">{ig_status.upper()}</span></td>
<td class="date">{ig_date}</td>
<td class="ayah">{_ayah_cell(fb["ayah_key"], fb_url)}</td>
<td><span class="badge {fb_status}">{fb_status.upper()}</span></td>
<td class="date">{fb_date}</td>
<td class="ayah">{_ayah_cell(yt["ayah_key"], yt_url)}</td>
<td><span class="badge {yt_status}">{yt_status.upper()}</span></td>
<td class="date">{yt_date}</td>
<td class="link-cell"><a href="{link}" target="_blank" rel="noopener" class="run-link" title="View workflow run">View</a></td>
</tr>
'''

    total_runs = min(len(runs), 10)
    show_more_btn = ""
    if total_runs > 3:
        show_more_btn = f'''<div class="show-more-wrap">
<button class="show-more-btn" onclick="toggleRows('{section_id}', this)" data-expanded="false">
Show more ({total_runs - 3} more runs)
</button>
</div>'''

    return f'''<div class="repo-section" id="{section_id}">
<h2>
    <span class="repo-title">{display_name}</span>
</h2>
<div class="filter-bar">
    <button class="filter-btn active" onclick="filterRows('{section_id}', 'all', this)">All</button>
    <button class="filter-btn filter-success" onclick="filterRows('{section_id}', 'success', this)">Success</button>
    <button class="filter-btn filter-failed" onclick="filterRows('{section_id}', 'failed', this)">Failed</button>
    <button class="filter-btn filter-other" onclick="filterRows('{section_id}', 'other', this)">Other</button>
</div>
<div class="table-wrapper">
<table>
<thead>
<tr>
    <th class="col-run" rowspan="2">Run</th>
    <th class="col-ig" colspan="3"><svg class="icon" viewBox="0 0 24 24" width="16" height="16"><defs><radialGradient id="ig{section_id}" cx="30%" cy="107%" r="150%"><stop offset="0%" stop-color="#fdf497"/><stop offset="5%" stop-color="#fdf497"/><stop offset="45%" stop-color="#fd5949"/><stop offset="60%" stop-color="#d6249f"/><stop offset="90%" stop-color="#285AEB"/></radialGradient></defs><rect x="2" y="2" width="20" height="20" rx="5" fill="none" stroke="url(#ig{section_id})" stroke-width="2"/><circle cx="12" cy="12" r="5" fill="none" stroke="url(#ig{section_id})" stroke-width="2"/><circle cx="17.5" cy="6.5" r="1.5" fill="url(#ig{section_id})"/></svg><span class="platform-name"> Instagram</span></th>
    <th class="col-fb" colspan="3"><svg class="icon" viewBox="0 0 24 24" width="16" height="16"><path d="M24 12.073c0-6.627-5.373-12-12-12S0 5.446 0 12.073c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.668 4.533-4.668 1.312 0 2.686.235 2.686.235v2.953h-1.513c-1.491 0-1.956.925-1.956 1.875v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z" fill="#1877F2"/></svg><span class="platform-name"> Facebook</span></th>
    <th class="col-yt" colspan="3"><svg class="icon" viewBox="0 0 24 24" width="16" height="16"><path d="M23.498 6.186a3.016 3.016 0 00-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 00.502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 002.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 002.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814z" fill="#FF0000"/><path d="M9.545 15.568V8.432L15.818 12l-6.273 3.568z" fill="#fff"/></svg><span class="platform-name"> YouTube</span></th>
    <th class="col-actions" rowspan="2">Run</th>
</tr>
<tr>
    <th class="col-ig sub">Ayah</th><th class="col-ig sub">Status</th><th class="col-ig sub">Date</th>
    <th class="col-fb sub">Ayah</th><th class="col-fb sub">Status</th><th class="col-fb sub">Date</th>
    <th class="col-yt sub">Ayah</th><th class="col-yt sub">Status</th><th class="col-yt sub">Date</th>
</tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
</div>
{show_more_btn}
</div>
'''


def _mqq_status_cell(platform: dict) -> str:
    status = (platform.get("status") or "unknown").lower()
    label = {"success": "SUCCESS", "failed": "FAILED", "skipped": "SKIPPED"}.get(status, "UNKNOWN")
    return f'<span class="badge {status}">{label}</span>'


def _mqq_link_cell(platform: dict) -> str:
    url = platform.get("url")
    if url:
        return f'<a href="{url}" target="_blank" rel="noopener" class="post-link">&#x1F517;</a>'
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
        return f'<span class="badge {tag}">{status} ({exp_str})</span>'
    tag = "success" if status == "valid" else ("failed" if status == "invalid" else "unknown")
    return f'<span class="badge {tag}">{status}</span>'


def fetch_mqq_entries(hours: int = 168) -> List[Dict]:
    """Pull MQQ UPLOAD_EVENT entries from the MQQ-Autopost repo's recent workflow logs."""
    runs = get_workflow_runs(MQQ_REPO, hours=hours)
    entries: List[Dict] = []
    seen = set()
    for run in runs:
        run_id = run.get("id")
        if not run_id:
            continue
        jobs = get_jobs_for_run(MQQ_REPO, run_id)
        for job in jobs:
            job_id = job.get("id")
            if not job_id:
                continue
            logs = get_job_logs(MQQ_REPO, job_id)
            for event in parse_upload_events(logs):
                key = (event.get("date"), event.get("rank"), event.get("arabic"))
                if key in seen:
                    continue
                seen.add(key)
                entries.append(event)
    entries.sort(key=lambda e: str(e.get("posted_at_utc") or ""))
    return entries


def generate_mqq_reel_html() -> str:
    entries: List[Dict] = fetch_mqq_entries()

    if not entries and MQQ_POST_LOG.exists():
        try:
            entries = json.loads(MQQ_POST_LOG.read_text(encoding="utf-8"))
        except Exception as exc:
            return f'<div class="repo-section"><h2>My Quran Quest (Word Reels)</h2><p class="no-data">Failed to read post log: {exc}</p></div>'

    if not entries:
        return '<div class="repo-section"><h2>My Quran Quest (Word Reels)</h2><p class="no-data">No recent posts.</p></div>'

    recent = list(reversed(entries[-20:]))

    rows = ""
    for idx, entry in enumerate(recent):
        date = entry.get("date", "?")
        rank = entry.get("rank", "?")
        arabic = entry.get("arabic", "")
        translit = entry.get("transliteration", "")
        meaning = entry.get("meaning", "")
        word_cell = f"{arabic} ({translit}) = {meaning}" if translit else f"{arabic} = {meaning}"

        ig = entry.get("instagram") or {}
        fb = entry.get("facebook") or {}
        yt = entry.get("youtube") or {}

        hidden = ' style="display:none"' if idx >= 3 else ""
        rows += f'''<tr class="data-row" data-idx="{idx}"{hidden}>
<td>{date}</td><td class="run-num">{rank}</td><td class="ayah">{word_cell}</td>
<td>{_mqq_status_cell(ig)}</td><td>{_mqq_status_cell(fb)}</td><td>{_mqq_status_cell(yt)}</td>
<td class="link-cell">{_mqq_link_cell(ig)}</td><td class="link-cell">{_mqq_link_cell(fb)}</td><td class="link-cell">{_mqq_link_cell(yt)}</td>
<td>{_mqq_token_cell(yt)}</td>
</tr>
'''

    total = len(recent)
    show_more = ""
    if total > 3:
        show_more = f'''<div class="show-more-wrap">
<button class="show-more-btn" onclick="toggleRows('mqq-section', this)" data-expanded="false">
Show more ({total - 3} more entries)
</button>
</div>'''

    return f'''<div class="repo-section" id="mqq-section">
<h2>My Quran Quest (Word Reels)</h2>
<div class="table-wrapper">
<table>
<thead>
<tr>
    <th>Date</th><th>Rank</th><th>Word</th>
    <th><svg class="icon" viewBox="0 0 24 24" width="14" height="14"><defs><radialGradient id="igmqq" cx="30%" cy="107%" r="150%"><stop offset="0%" stop-color="#fdf497"/><stop offset="5%" stop-color="#fdf497"/><stop offset="45%" stop-color="#fd5949"/><stop offset="60%" stop-color="#d6249f"/><stop offset="90%" stop-color="#285AEB"/></radialGradient></defs><rect x="2" y="2" width="20" height="20" rx="5" fill="none" stroke="url(#igmqq)" stroke-width="2"/><circle cx="12" cy="12" r="5" fill="none" stroke="url(#igmqq)" stroke-width="2"/><circle cx="17.5" cy="6.5" r="1.5" fill="url(#igmqq)"/></svg> IG</th>
    <th><svg class="icon" viewBox="0 0 24 24" width="14" height="14"><path d="M24 12.073c0-6.627-5.373-12-12-12S0 5.446 0 12.073c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.668 4.533-4.668 1.312 0 2.686.235 2.686.235v2.953h-1.513c-1.491 0-1.956.925-1.956 1.875v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z" fill="#1877F2"/></svg> FB</th>
    <th><svg class="icon" viewBox="0 0 24 24" width="14" height="14"><path d="M23.498 6.186a3.016 3.016 0 00-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 00.502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 002.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 002.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814z" fill="#FF0000"/><path d="M9.545 15.568V8.432L15.818 12l-6.273 3.568z" fill="#fff"/></svg> YT</th>
    <th>IG Link</th><th>FB Link</th><th>YT Link</th>
    <th>YT Token</th>
</tr>
</thead>
<tbody>
{rows}
</tbody>
</table>
</div>
{show_more}
</div>
'''


def generate_html_report():
    generated_at = now_utc().strftime("%Y-%m-%d %H:%M UTC")
    latest_report_run = _workflow_status_meta(get_latest_workflow_run(REPORT_REPO, REPORT_WORKFLOW_FILE))

    sections = ""
    for i, repo in enumerate(REPOS):
        sections += generate_repo_html(repo, f"repo-{i}")

    sections += generate_mqq_reel_html()

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Daily Upload Report</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            padding: 20px;
            min-height: 100vh;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; }}

        /* Header */
        .header {{
            text-align: center;
            padding: 30px 20px 20px;
            margin-bottom: 24px;
            background: linear-gradient(135deg, rgba(59,130,246,0.15), rgba(139,92,246,0.1));
            border-radius: 16px;
            border: 1px solid rgba(59,130,246,0.2);
        }}
        .header h1 {{
            color: #f1f5f9;
            font-size: clamp(1.6em, 5vw, 2.2em);
            font-weight: 700;
            letter-spacing: -0.02em;
        }}
        .header .subtitle {{
            color: #94a3b8;
            font-size: clamp(0.85em, 2.5vw, 1em);
            margin-top: 6px;
        }}
        .header .generated {{
            color: #64748b;
            font-size: 0.8em;
            margin-top: 8px;
        }}
        .header-actions {{
            display: flex;
            justify-content: center;
            gap: 10px;
            flex-wrap: wrap;
            margin-top: 16px;
        }}
        .action-btn {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 8px 14px;
            border-radius: 999px;
            border: 1px solid rgba(96,165,250,0.35);
            background: rgba(30,41,59,0.7);
            color: #e2e8f0;
            font-size: 0.85em;
            font-weight: 600;
            transition: transform 0.15s ease, background 0.15s ease, border-color 0.15s ease;
        }}
        .action-btn:hover {{
            background: rgba(59,130,246,0.18);
            border-color: rgba(96,165,250,0.65);
            color: #f8fafc;
            transform: translateY(-1px);
        }}
        .action-btn.primary {{
            background: linear-gradient(135deg, rgba(59,130,246,0.24), rgba(139,92,246,0.18));
        }}
        .workflow-status {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            flex-wrap: wrap;
            margin-top: 12px;
            color: #94a3b8;
            font-size: 0.82em;
        }}
        .workflow-status .meta {{
            color: #64748b;
        }}

        /* Repo sections */
        .repo-section {{
            margin-bottom: 32px;
            background: rgba(30,41,59,0.6);
            border-radius: 12px;
            padding: 0;
            overflow: hidden;
            border: 1px solid rgba(51,65,85,0.5);
        }}
        .repo-section h2 {{
            padding: 16px 20px;
            font-size: clamp(1.1em, 3vw, 1.4em);
            font-weight: 600;
            color: #f1f5f9;
            background: linear-gradient(135deg, rgba(30,41,59,0.9), rgba(30,41,59,0.7));
            border-bottom: 1px solid rgba(51,65,85,0.6);
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .no-data {{
            padding: 24px;
            color: #64748b;
            text-align: center;
        }}

        /* Filter bar */
        .filter-bar {{
            display: flex;
            gap: 6px;
            padding: 12px 20px;
            background: rgba(15,23,42,0.5);
            border-bottom: 1px solid rgba(51,65,85,0.4);
            flex-wrap: wrap;
        }}
        .filter-btn {{
            padding: 5px 14px;
            border: 1px solid #334155;
            border-radius: 6px;
            background: transparent;
            color: #94a3b8;
            font-size: 0.8em;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .filter-btn:hover {{ background: rgba(51,65,85,0.5); color: #e2e8f0; }}
        .filter-btn.active {{ background: #334155; color: #f1f5f9; border-color: #475569; }}
        .filter-success.active {{ background: rgba(34,197,94,0.2); color: #86efac; border-color: rgba(34,197,94,0.4); }}
        .filter-failed.active {{ background: rgba(239,68,68,0.2); color: #fca5a5; border-color: rgba(239,68,68,0.4); }}
        .filter-other.active {{ background: rgba(234,179,8,0.2); color: #fde047; border-color: rgba(234,179,8,0.4); }}

        /* Table */
        .table-wrapper {{
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: clamp(0.72em, 1.8vw, 0.85em);
        }}
        thead tr:first-child th {{
            padding: 10px 8px;
            font-weight: 700;
            text-transform: uppercase;
            font-size: 0.75em;
            letter-spacing: 0.05em;
            border-bottom: 2px solid #334155;
            white-space: nowrap;
        }}
        thead tr:nth-child(2) th {{
            padding: 6px 8px;
            font-weight: 600;
            font-size: 0.7em;
            color: #94a3b8;
            border-bottom: 1px solid #334155;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }}
        th.sub {{ font-weight: 500; }}

        /* Platform column colors */
        .col-run {{ background: rgba(15,23,42,0.5); color: #94a3b8; }}
        .col-actions {{ background: rgba(15,23,42,0.5); color: #94a3b8; }}

        .col-ig {{ background: linear-gradient(135deg, rgba(214,36,159,0.18), rgba(253,89,73,0.12)); color: #f9a8d4; }}
        .col-fb {{ background: linear-gradient(135deg, rgba(24,119,242,0.18), rgba(24,119,242,0.1)); color: #93c5fd; }}
        .col-yt {{ background: linear-gradient(135deg, rgba(255,0,0,0.15), rgba(255,0,0,0.08)); color: #fca5a5; }}

        td {{ padding: 10px 8px; border-bottom: 1px solid rgba(51,65,85,0.4); }}
        tr:last-child td {{ border-bottom: none; }}

        /* Subtle platform row tints - 3 cols per platform: Ayah, Status, Date */
        td:nth-child(2), td:nth-child(3), td:nth-child(4) {{
            background: rgba(214,36,159,0.04);
        }}
        td:nth-child(5), td:nth-child(6), td:nth-child(7) {{
            background: rgba(24,119,242,0.04);
        }}
        td:nth-child(8), td:nth-child(9), td:nth-child(10) {{
            background: rgba(255,0,0,0.04);
        }}
        tr:hover td {{ background: rgba(51,65,85,0.25) !important; }}

        .run-num {{ font-weight: 600; color: #94a3b8; white-space: nowrap; }}
        .ayah {{ font-weight: 600; color: #e2e8f0; font-family: 'Segoe UI', sans-serif; }}
        .date {{ color: #94a3b8; white-space: nowrap; font-size: 0.9em; }}
        .link-cell {{ text-align: center; }}

        /* Status badges */
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-weight: 700;
            font-size: 0.78em;
            letter-spacing: 0.02em;
            white-space: nowrap;
        }}
        .badge.success {{ color: #86efac; background: rgba(34,197,94,0.15); }}
        .badge.running {{ color: #fde68a; background: rgba(245,158,11,0.16); }}
        .badge.failed {{ color: #fca5a5; background: rgba(239,68,68,0.15); }}
        .badge.unknown {{ color: #fde047; background: rgba(234,179,8,0.12); }}
        .badge.skipped {{ color: #94a3b8; background: rgba(148,163,184,0.12); }}

        /* Links */
        a {{ color: #60a5fa; text-decoration: none; transition: color 0.15s; }}
        a:hover {{ color: #93c5fd; }}
        .ayah-link {{
            color: #93c5fd;
            font-weight: 700;
            border-bottom: 1px dashed rgba(147,197,253,0.4);
            padding-bottom: 1px;
        }}
        .ayah-link:hover {{ color: #bfdbfe; border-bottom-color: rgba(191,219,254,0.6); }}
        .run-link {{
            padding: 3px 10px;
            border-radius: 4px;
            background: rgba(59,130,246,0.12);
            font-size: 0.85em;
            font-weight: 500;
        }}
        .run-link:hover {{ background: rgba(59,130,246,0.25); }}
        .platform-name {{ }}

        /* Show more */
        .show-more-wrap {{ padding: 12px 20px; text-align: center; border-top: 1px solid rgba(51,65,85,0.3); }}
        .show-more-btn {{
            padding: 7px 20px;
            border: 1px solid #334155;
            border-radius: 6px;
            background: rgba(51,65,85,0.3);
            color: #94a3b8;
            font-size: 0.82em;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .show-more-btn:hover {{ background: rgba(51,65,85,0.5); color: #e2e8f0; }}

        /* Icon alignment */
        .icon {{ vertical-align: middle; margin-right: 4px; }}

        /* Mobile */
        @media (max-width: 768px) {{
            body {{ padding: 10px; }}
            .header {{ padding: 20px 12px 14px; margin-bottom: 16px; }}
            .filter-bar {{ padding: 8px 12px; }}
            .filter-btn {{ padding: 4px 10px; font-size: 0.75em; }}
            .repo-section h2 {{ padding: 12px 14px; font-size: 1em; }}
            table {{ font-size: 0.68em; }}
            td, th {{ padding: 6px 4px; }}
            .badge {{ padding: 2px 5px; font-size: 0.7em; }}
            .show-more-wrap {{ padding: 8px 12px; }}
            .platform-name {{ display: none; }}
        }}
        @media (max-width: 480px) {{
            body {{ padding: 6px; }}
            table {{ font-size: 0.6em; }}
            td, th {{ padding: 4px 3px; }}
            .badge {{ padding: 1px 3px; font-size: 0.65em; }}
            .platform-name {{ display: none; }}
        }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>Daily Upload Report</h1>
        <div class="subtitle">Quran Upload Automation Status</div>
        <div class="generated">Generated: {generated_at}</div>
        <div class="header-actions">
            <a class="action-btn primary" href="{REPORT_WORKFLOW_URL}" target="_blank" rel="noopener">Run workflow</a>
            <a class="action-btn" href="{latest_report_run['url']}" target="_blank" rel="noopener">Latest run</a>
        </div>
        <div class="workflow-status">
            <span>Report workflow</span>
            <span class="badge {latest_report_run['status_class']}">{latest_report_run['label']}</span>
            <span class="meta">{latest_report_run['meta']}</span>
        </div>
    </div>
    {sections}
</div>
<script>
function toggleRows(sectionId, btn) {{
    const section = document.getElementById(sectionId);
    const rows = section.querySelectorAll('.data-row');
    const expanded = btn.dataset.expanded === 'true';

    rows.forEach(row => {{
        const idx = parseInt(row.dataset.idx);
        if (idx >= 3) {{
            row.style.display = expanded ? 'none' : '';
        }}
    }});

    btn.dataset.expanded = expanded ? 'false' : 'true';
    if (expanded) {{
        const total = rows.length;
        btn.textContent = 'Show more (' + (total - 3) + ' more runs)';
    }} else {{
        btn.textContent = 'Show less';
    }}
}}

function filterRows(sectionId, filter, btn) {{
    const section = document.getElementById(sectionId);
    const rows = section.querySelectorAll('.data-row');
    const showMoreBtn = section.querySelector('.show-more-btn');

    // Update active button
    section.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    // Reset show-more state
    if (showMoreBtn) {{
        showMoreBtn.dataset.expanded = 'true';
        showMoreBtn.textContent = 'Show less';
    }}

    rows.forEach(row => {{
        if (filter === 'all' || row.dataset.filter === filter) {{
            row.style.display = '';
        }} else {{
            row.style.display = 'none';
        }}
    }});
}}
</script>
</body>
</html>'''


def save_report():
    os.makedirs("site", exist_ok=True)
    html = generate_html_report()
    with open("site/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("HTML report generated")


if __name__ == "__main__":
    save_report()
