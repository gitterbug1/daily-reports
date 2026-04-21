#!/usr/bin/env python3
"""
Rewrite the IG or YT schedule for one of the three daily-Quran repos so that
the NEXT scheduled run starts at the given (surah, ayah) and continues
sequentially (looping 1:1 -> 114:6 -> 1:1) until the existing row count is
filled.

Past rows (date/part already elapsed in IST) are left untouched; a timestamped
backup of the xlsx is written alongside.

Usage:
  py update_schedule.py --folder ar --platform ig --surah 2 --ayah 255
  py update_schedule.py --folder en --platform yt --surah 18 --ayah 1
  py update_schedule.py --folder all --platform ig --surah 2 --ayah 1
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent.parent.parent

FOLDERS = {
    "ar": ROOT / "learnqurandaily",
    "en": ROOT / "learnenglishqurandaily",
    "ur": ROOT / "learnurduqurandaily",
}

PLATFORM_FILE = {
    "ig": "quran_complete_schedule.xlsx",
    "yt": "yt_schedule.xlsx",
}


# Per-language part scheduled times (hour, minute) in IST
PART_SCHEDULES = {
    "ar": [(4, 0), (13, 0), (20, 30)],   # 4:00, 13:00, 20:30 IST
    "en": [(4, 0), (16, 0), (21, 0)],    # 4:00, 16:00, 21:00 IST
    "ur": [(4, 0), (16, 0), (21, 0)],    # 4:00, 16:00, 21:00 IST
}

# Ayahs per surah (1-indexed; total = 6236).
AYAH_COUNTS = [
    7, 286, 200, 176, 120, 165, 206, 75, 129, 109,
    123, 111, 43, 52, 99, 128, 111, 110, 98, 135,
    112, 78, 118, 64, 77, 227, 93, 88, 69, 60,
    34, 30, 73, 54, 45, 83, 182, 88, 75, 85,
    54, 53, 89, 59, 37, 35, 38, 29, 18, 45,
    60, 49, 62, 55, 78, 96, 29, 22, 24, 13,
    14, 11, 11, 18, 12, 12, 30, 52, 52, 44,
    28, 28, 20, 56, 40, 31, 50, 40, 46, 42,
    29, 19, 36, 25, 22, 17, 19, 26, 30, 20,
    15, 21, 11, 8, 8, 19, 5, 8, 8, 11,
    11, 8, 3, 9, 5, 4, 7, 3, 6, 3,
    5, 4, 5, 6,
]



def current_part_exact(now_ist: datetime, part_times: list[tuple[int, int]]) -> int:
    # part_times: [(h1, m1), (h2, m2), (h3, m3)]
    for i, (h, m) in enumerate(part_times):
        if (now_ist.hour, now_ist.minute) < (h, m):
            return i + 1
    return 3


def part_num(part_str: str) -> int:
    return int(str(part_str).strip().replace("Part", "").strip())


def next_ayah(surah: int, ayah: int) -> tuple[int, int]:
    if ayah < AYAH_COUNTS[surah - 1]:
        return surah, ayah + 1
    if surah < 114:
        return surah + 1, 1
    return 1, 1  # wrap after 114:6


def update_schedule(xlsx_path: Path, start_surah: int, start_ayah: int) -> None:
    if not xlsx_path.exists():
        raise FileNotFoundError(xlsx_path)

    df = pd.read_excel(xlsx_path)
    for col in ("Date", "Part", "Surah", "Start Ayah", "End Ayah"):
        if col not in df.columns:
            raise ValueError(f"{xlsx_path.name} missing column: {col}")

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["PartNum"] = df["Part"].map(part_num)

    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)
    today = pd.Timestamp(now.date())
    # Detect language from file path (ar/en/ur)
    lang = None
    for k, v in FOLDERS.items():
        if str(v) in str(xlsx_path):
            lang = k
            break
    if lang is None:
        lang = "ar"  # fallback default
    part_times = PART_SCHEDULES.get(lang, [(4,0), (10,0), (17,0)])
    cur_part = current_part_exact(now, part_times)

    # For today's rows, check if the current time is after the scheduled time for that part
    def is_future_row(row):
        # row["Date"] is pd.Timestamp, today is pd.Timestamp
        row_date = row["Date"]
        row_part = int(str(row["Part"]).replace("Part", "").strip())
        if row_date > today:
            return True
        if row_date < today:
            return False
        # row_date == today
        h, m = part_times[row_part - 1]
        return (now.hour, now.minute) < (h, m)

    mask_next = df.apply(is_future_row, axis=1)
    next_idx = df.index[mask_next]
    if len(next_idx) == 0:
        print(f"  [{xlsx_path.name}] no future rows to update — schedule exhausted.")
        return None

    start_idx = next_idx[0]

    s, a = start_surah, start_ayah
    n = 0
    for idx in df.index[start_idx:]:
        df.at[idx, "Surah"] = s
        df.at[idx, "Start Ayah"] = a
        df.at[idx, "End Ayah"] = a
        s, a = next_ayah(s, a)
        n += 1

    # Check if schedule reaches 114:6, if not extend it
    if s != 1 or a != 1:  # Not wrapped around to 1:1 yet, so incomplete
        last_date_val = df.iloc[-1]["Date"]
        last_part_str = df.iloc[-1]["Part"]
        last_part_num = part_num(last_part_str)
        
        # Generate new rows to reach 114:6
        new_rows = []
        next_date = last_date_val
        next_part = last_part_num
        
        while True:
            # Move to next part
            if next_part == 3:
                next_part = 1
                next_date = next_date + pd.Timedelta(days=1)
            else:
                next_part += 1
            
            new_rows.append({
                "Date": next_date,
                "Part": f"Part {next_part}",
                "Surah": s,
                "Start Ayah": a,
                "End Ayah": a,
            })
            
            if s == 114 and a == 6:
                break
            
            s, a = next_ayah(s, a)
        
        if new_rows:
            df_new = pd.DataFrame(new_rows)
            df = pd.concat([df, df_new], ignore_index=True)
            n += len(new_rows)
            print(f"  [{xlsx_path.name}] extended with {len(new_rows)} rows to reach 114:6")

    df = df.drop(columns=["PartNum"])
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    df.to_excel(xlsx_path, index=False)

    first_row = df.loc[start_idx]
    last_row = df.iloc[-1]
    print(f"  [{xlsx_path.name}] rewrote {n} rows from row {start_idx}")
    print(f"    first: {first_row['Date']} {first_row['Part']} -> Surah {first_row['Surah']}:{first_row['Start Ayah']}")
    print(f"    last : {last_row['Date']} {last_row['Part']} -> Surah {last_row['Surah']}:{last_row['Start Ayah']}")
    return start_idx

def verify_schedule_with_api(xlsx_path: Path, start_idx: int) -> None:
    """
    Verify entire updated schedule against AYAH_COUNTS:
    - Surah 1-114
    - Ayah valid for each surah
    - Ends at surah 114
    If schedule ends before surah 114, extend dates to continue.
    """
    df = pd.read_excel(xlsx_path)
    rows = list(df.index[start_idx:])
    
    if not rows:
        return
    
    errors = []
    last_surah = None
    last_ayah = None
    
    for idx in rows:
        surah = int(df.at[idx, "Surah"])
        ayah = int(df.at[idx, "Start Ayah"])
        last_surah = surah
        last_ayah = ayah
        
        if not (1 <= surah <= 114):
            errors.append(f"Row {idx}: Invalid surah {surah} (must be 1-114)")
            continue
        
        max_ayah = AYAH_COUNTS[surah - 1]
        if not (1 <= ayah <= max_ayah):
            errors.append(f"Row {idx}: Invalid ayah {ayah} for surah {surah} (max {max_ayah})")
    
    print(f"\n✓ Verified {len(rows)} rows against AYAH_COUNTS")
    
    if errors:
        print("❌ Validation errors found:")
        for e in errors[:10]:
            print("   ", e)
        if len(errors) > 10:
            print(f"   ...and {len(errors) - 10} more issues")
    else:
        print("✅ All rows valid")
    
    if last_surah == 114 and last_ayah == 6:
        print(f"   Schedule reaches complete Quran (114:6)")
    elif last_surah is not None:
        print(f"   ⚠ Schedule ends at {last_surah}:{last_ayah}, not 114:6")

def git_commit_changes(xlsx_path: Path, folder_name: str, platform: str, surah: int, ayah: int, git_user: str = None, git_pass: str = None) -> bool:
    """
    Commit the updated schedule to git with a descriptive message.
    Optionally use git credentials from arguments.
    Returns True if successful, False otherwise.
    """
    try:
        # Find git repo root using git command (most reliable)
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(xlsx_path.parent),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            repo_dir = Path(result.stdout.strip())
        except subprocess.CalledProcessError:
            # Fallback: search up directory tree for .git
            repo_dir = xlsx_path.parent
            while repo_dir.parent != repo_dir:
                if (repo_dir / ".git").exists():
                    break
                repo_dir = repo_dir.parent
            else:
                print(f"  [GIT] No git repo found for {xlsx_path}")
                return False
        
        # Setup environment for git credentials if provided
        env = os.environ.copy()
        if git_user and git_pass:
            # For HTTPS: use GIT_ASKPASS to provide credentials
            env["GIT_ASKPASS_OVERRIDE"] = "true"
            env["GIT_USERNAME"] = git_user
            env["GIT_PASSWORD"] = git_pass
        
        # Stage the file
        subprocess.run(
            ["git", "add", str(xlsx_path)],
            cwd=repo_dir,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        
        # Commit with descriptive message
        commit_msg = f"schedule: update {platform.upper()} schedule (surah {surah}:{ayah}) for {folder_name}"
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=repo_dir,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        
        print(f"  [GIT] ✓ Committed: {commit_msg}")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"  [GIT] Commit failed: {e.stderr.decode() if e.stderr else str(e)}")
        return False
    except Exception as e:
        print(f"  [GIT] Error: {e}")
        return False

def git_push_changes(xlsx_path: Path, git_user: str = None, git_pass: str = None) -> bool:
    """
    Push the committed changes to remote (origin/main or origin/master).
    Optionally use git credentials from arguments.
    Returns True if successful, False otherwise.
    """
    try:
        # Find git repo root
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(xlsx_path.parent),
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            repo_dir = Path(result.stdout.strip())
        except subprocess.CalledProcessError:
            print(f"  [GIT] Failed to find git repo for push")
            return False
        
        # Setup environment for git credentials if provided
        env = os.environ.copy()
        if git_user and git_pass:
            env["GIT_ASKPASS_OVERRIDE"] = "true"
            env["GIT_USERNAME"] = git_user
            env["GIT_PASSWORD"] = git_pass
        
        # Get current branch
        try:
            branch_result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=repo_dir,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            current_branch = branch_result.stdout.strip()
        except subprocess.CalledProcessError:
            current_branch = "main"
        
        # Push to origin
        subprocess.run(
            ["git", "push", "origin", current_branch],
            cwd=repo_dir,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        
        print(f"  [GIT] ✓ Pushed to origin/{current_branch}")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"  [GIT] Push failed: {e.stderr.decode() if e.stderr else str(e)}")
        return False
    except Exception as e:
        print(f"  [GIT] Error: {e}")
        return False

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--folder", choices=[*FOLDERS.keys(), "all"], required=True)
    p.add_argument("--platform", choices=list(PLATFORM_FILE.keys()), required=True)
    p.add_argument("--surah", type=int, required=True)
    p.add_argument("--ayah", type=int, required=True)
    p.add_argument("--no-commit", action="store_true", help="Skip git commit")
    p.add_argument("--no-push", action="store_true", help="Skip git push")
    p.add_argument("--git-name", type=str, help="Git user name for commits (optional)")
    p.add_argument("--git-email", type=str, help="Git user email for commits (optional)")
    p.add_argument("--git-user", type=str, help="GitHub username for authentication (optional)")
    p.add_argument("--git-pass", type=str, help="GitHub password/token for authentication (optional)")
    args = p.parse_args()

    if not (1 <= args.surah <= 114):
        sys.exit("--surah must be 1-114")
    if not (1 <= args.ayah <= AYAH_COUNTS[args.surah - 1]):
        sys.exit(f"--ayah out of range for surah {args.surah} (max {AYAH_COUNTS[args.surah - 1]})")

    targets = list(FOLDERS.values()) if args.folder == "all" else [FOLDERS[args.folder]]
    fname = PLATFORM_FILE[args.platform]

    for folder in targets:
        path = folder / fname
        folder_name = folder.name
        print(f"\n>>> {folder_name} / {args.platform}")
        try:
            start_idx = update_schedule(path, args.surah, args.ayah)
            if start_idx is not None:
                verify_schedule_with_api(path, start_idx)
                if not args.no_commit:
                    # Configure git user for this repo if provided
                    if args.git_name or args.git_email:
                        try:
                            repo_dir = path.parent
                            while repo_dir.parent != repo_dir and ".git" not in repo_dir.iterdir():
                                repo_dir = repo_dir.parent
                            
                            if args.git_name:
                                subprocess.run(
                                    ["git", "config", "user.name", args.git_name],
                                    cwd=repo_dir,
                                    check=True,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                )
                                print(f"  [GIT] Config user.name: {args.git_name}")
                            
                            if args.git_email:
                                subprocess.run(
                                    ["git", "config", "user.email", args.git_email],
                                    cwd=repo_dir,
                                    check=True,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                )
                                print(f"  [GIT] Config user.email: {args.git_email}")
                        except Exception as e:
                            print(f"  [GIT] Warning: Failed to configure git user: {e}")
                    
                    git_commit_changes(path, folder_name, args.platform, args.surah, args.ayah, args.git_user, args.git_pass)
                    
                    if not args.no_push:
                        git_push_changes(path, args.git_user, args.git_pass)
        except Exception as exc:
            print(f"  [FAIL] {exc}")


if __name__ == "__main__":
    main()
