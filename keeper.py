"""Keeps posting on time without relying on GitHub's cron.

Loop: pull latest log -> post any due Reel -> push log -> sleep until the next
Reel's time (if it falls inside this job's time budget). When the budget is
used up, the workflow re-launches itself (see post.yml), so the chain never stops.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time

BUDGET = dt.timedelta(hours=5, minutes=30)  # GitHub jobs may run up to 6h
start = dt.datetime.now(dt.timezone.utc)


def sh(*cmd):
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def next_due():
    sched = json.load(open("schedule.json", encoding="utf-8"))
    posted = json.load(open("posted.json", encoding="utf-8"))
    pending = [dt.datetime.fromisoformat(r["post_at"]) for r in sched if r["n"] not in posted]
    return min(pending) if pending else None


while True:
    sh("git", "pull", "-q", "--rebase")
    r = subprocess.run([sys.executable, "post_reel.py"])
    sh("git", "add", "posted.json")
    if sh("git", "diff", "--cached", "--quiet").returncode != 0:
        sh("git", "commit", "-m", "Log posted Reel")
        sh("git", "push")
    nxt = next_due()
    now = dt.datetime.now(dt.timezone.utc)
    if nxt is None:
        print("All Reels posted.")
        open(".done", "w").close()
        break
    end = start + BUDGET
    if now >= end:
        print("Time budget used; handing over to a fresh run.")
        break
    if nxt <= now:
        time.sleep(60)  # still due (e.g. a failed attempt); retry shortly
        continue
    if nxt > end:
        print(f"Next Reel at {nxt}; waiting until {end} then handing over.")
        time.sleep((end - now).total_seconds())
        break
    wait = (nxt - now).total_seconds() + 20
    print(f"Sleeping {int(wait)}s until {nxt}")
    time.sleep(wait)
