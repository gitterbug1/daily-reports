import os
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

# Three repositories to monitor
REPOS = [
    "ilearnarabicquran/learnqurandaily",
    "ilearncuranurdu/learnurduqurandaily", 
    "iwilllearnenglishquran/learnenglishqurandaily"
]

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}

def get_repo_display_name(repo: str) -> str:
    """Convert repo name to display name"""
    display_names = {
        "ilearnarabicquran/learnqurandaily": "Learn Quran Daily (Arabic)",
        "ilearncuranurdu/learnurduqurandaily": "Learn Urdu Quran Daily",
        "iwilllearnenglishquran/learnenglishqurandaily": "Learn English Quran Daily"
    }
    return display_names.get(repo, repo)

def get_workflow_runs(repo: str, hours: int = 24) -> List[Dict]:
    """Fetch workflow runs from the last N hours"""
    url = f"https://api.github.com/repos/{repo}/actions/runs"
    
    yesterday = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    
    params = {
        "created": f">={yesterday}",
        "per_page": 100
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        return response.json().get("workflow_runs", [])
    except Exception as e:
        print(f"Error fetching runs for {repo}: {str(e)}")
        return []

def get_job_logs(repo: str, job_id: int) -> str:
    """Fetch logs for a specific job"""
    url = f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.text
        return ""
    except Exception as e:
        print(f"Error fetching logs for job {job_id}: {str(e)}")
        return ""

def analyze_logs(logs: str) -> Dict[str, str]:
    """Analyze logs to determine upload success"""
    if not logs:
        return {
            "instagram": "⚠️ UNKNOWN",
            "facebook": "⚠️ UNKNOWN",
            "youtube": "⚠️ UNKNOWN"
        }
    
    logs_lower = logs.lower()
    
    status = {
        "instagram": "❌ FAILED" if ("instagram" in logs_lower or "ig" in logs_lower) and "error" in logs_lower else "✅ SUCCESS",
        "facebook": "❌ FAILED" if "facebook" in logs_lower and "error" in logs_lower else "✅ SUCCESS",
        "youtube": "❌ FAILED" if "refresherror" in logs_lower or ("youtube" in logs_lower and "error" in logs_lower) else "✅ SUCCESS"
    }
    
    return status

def get_jobs_for_run(repo: str, run_id: int) -> List[Dict]:
    """Fetch jobs for a specific run"""
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get("jobs", [])
    except Exception as e:
        print(f"Error fetching jobs for run {run_id}: {str(e)}")
        return []

def generate_repo_report(repo: str) -> str:
    """Generate report for a single repository"""
    display_name = get_repo_display_name(repo)
    runs = get_workflow_runs(repo)
    
    if not runs:
        return f"\n### {display_name}\n\nNo workflow runs in the last 24 hours.\n"
    
    report = f"\n### {display_name}\n\n"
    report += "| Run # | Status | Date | Instagram | Facebook | YouTube | Link |\n"
    report += "|-------|--------|------|-----------|----------|---------|------|\n"
    
    for run in runs[:10]:  # Last 10 runs
        run_id = run["id"]
        run_number = run["run_number"]
        status = "✅ PASSED" if run["conclusion"] == "success" else "❌ FAILED"
        created_at = run["created_at"][:10]
        
        jobs = get_jobs_for_run(repo, run_id)
        
        ig_status = "⚠️"
        fb_status = "⚠️"
        yt_status = "⚠️"
        
        for job in jobs:
            logs = get_job_logs(repo, job["id"])
            analysis = analyze_logs(logs)
            ig_status = analysis["instagram"]
            fb_status = analysis["facebook"]
            yt_status = analysis["youtube"]
        
        link = f"https://github.com/{repo}/actions/runs/{run_id}"
        report += f"| #{run_number} | {status} | {created_at} | {ig_status} | {fb_status} | {yt_status} | [View]({link}) |\n"
    
    return report

def generate_full_report() -> str:
    """Generate consolidated report for all repositories"""
    report = f"# 📊 Daily Upload Report\n\n"
    report += f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
    report += f"**Repositories:** {len(REPOS)}\n"
    
    for repo in REPOS:
        report += generate_repo_report(repo)
    
    report += "\n### Legend\n\n"
    report += "- ✅ SUCCESS: Upload completed without errors\n"
    report += "- ❌ FAILED: Upload encountered an error\n"
    report += "- ⚠️ UNKNOWN: Could not determine status from logs\n"
    
    return report

def save_report(report: str) -> None:
    """Save report to file"""
    os.makedirs("reports", exist_ok=True)
    
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    report_path = f"reports/report-{date_str}.md"
    
    with open(report_path, "w") as f:
        f.write(report)
    
    print(f"✅ Report saved to {report_path}")
    print(report)

if __name__ == "__main__":
    report = generate_full_report()
    save_report(report)