"""Post scheduled Reels to Instagram via the official Instagram API (Instagram Login).

Runs from GitHub Actions every 15 minutes. Posts at most one Reel per run:
the earliest scheduled Reel whose time has passed and that is not yet posted.

Env:
  IG_TOKEN    Instagram user access token (GitHub secret)
  PAGES_BASE  Public base URL of the videos, e.g. https://alphazetservices.github.io/hx-reels
  ONLY        Optional: post this Reel number now (e.g. "001"), ignoring its time
  DRY_RUN     Optional: "1" to only print what would happen
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

TOKEN = os.environ.get("IG_TOKEN", "").strip()
# Instagram-login tokens start with "IG"; Facebook-login (Unified login) tokens start with "EA".
API = ("https://graph.facebook.com/v25.0" if TOKEN.startswith("EA")
       else "https://graph.instagram.com/v25.0")
RAW_BASE = "https://raw.githubusercontent.com/alphazetservices/hx-reels/main"
MAX_LATE = dt.timedelta(hours=3)  # skip a Reel if the runner was down for longer than this


def call(method, path, _soft=False, **params):
    params["access_token"] = TOKEN
    data = urllib.parse.urlencode(params).encode()
    url = f"{API}/{path}"
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode())
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        if _soft:
            return {"error": body}
        print(f"API error on {path}: {e.code} {body}", flush=True)
        if any(k in body for k in ("API access blocked", "request limit", "rate limit", "Error validating access token", "has expired", "session has been invalidated")):
            sys.exit(3)  # account-level problem: stop, don't hammer Meta
        sys.exit(1)


def ig_user():
    """Return (instagram user id, username) for the token."""
    if os.environ.get("IG_USER_ID"):
        return os.environ["IG_USER_ID"], "(from IG_USER_ID)"
    if not TOKEN.startswith("EA"):
        me = call("GET", "me", fields="user_id,username")
        return me["user_id"], me.get("username")
    # Facebook-login token: find the Instagram business account it can manage.
    pages = call("GET", "me/accounts", fields="instagram_business_account{id,username}")
    for p in pages.get("data", []):
        iba = p.get("instagram_business_account")
        if iba:
            return iba["id"], iba.get("username")
    me = call("GET", "me", fields="instagram_business_account{id,username}")
    if me.get("instagram_business_account"):
        return me["instagram_business_account"]["id"], me["instagram_business_account"].get("username")
    sys.exit("Token works but no Instagram account found. Set IG_USER_ID.")


def main():
    if os.environ.get("CHECK") == "1":
        print(f"Token received: starts with '{TOKEN[:4]}', length {len(TOKEN)}")
        for host in ("https://graph.instagram.com/v25.0", "https://graph.facebook.com/v25.0"):
            url = f"{host}/me?fields=id,username&access_token={urllib.parse.quote(TOKEN)}"
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    print(f"{host}: OK {r.read().decode()[:200]}")
            except urllib.error.HTTPError as e:
                print(f"{host}: {e.code} {e.read().decode()[:200]}")
        uid, name = ig_user()
        name = call("GET", uid, fields="username").get("username", name)
        print(f"Token OK. Instagram account: {name} (id {uid}). API: {API}")
        return
    schedule = json.load(open("schedule.json", encoding="utf-8"))
    posted = json.load(open("posted.json", encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc)
    only = os.environ.get("ONLY", "").strip()

    if only:
        due = [r for r in schedule if r["n"] in (only, only.zfill(3))]
    else:
        due = [r for r in schedule
               if r["n"] not in posted
               and dt.datetime.fromisoformat(r["post_at"]) <= now]
    if not due:
        print("Nothing due.")
        return
    reel = due[0]
    if reel["n"] in posted and posted[reel["n"]].get("status") == "posted" and os.environ.get("PREP_ONLY") != "1":
        print(f"Reel {reel['n']} was already posted; not posting twice.")
        return
    late = now - dt.datetime.fromisoformat(reel["post_at"])
    if not only and late > MAX_LATE:
        print(f"Reel {reel['n']} is {late} late; marking skipped.")
        posted[reel["n"]] = {"status": "skipped_late", "at": now.isoformat()}
        json.dump(posted, open("posted.json", "w", encoding="utf-8"), indent=1)
        return

    if not reel.get("caption"):
        print(f"Reel {reel['n']} has no approved caption yet; holding it.")
        return
    raw_url = f"{RAW_BASE}/{reel['file']}"
    print(f"Posting Reel {reel['n']} ({reel['source']})", flush=True)
    if os.environ.get("DRY_RUN") == "1":
        print(reel["caption"])
        return

    uid, name = ig_user()
    print("Account:", name, flush=True)
    cid = make_container(uid, reel, raw_url)
    for _ in range(90):  # wait up to ~15 minutes for Instagram to process the video
        st = call("GET", cid, fields="status_code,status")
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            sys.exit(f"Processing failed: {st}")
        time.sleep(10)
    else:
        sys.exit("Timed out waiting for video processing.")
    if os.environ.get("PREP_ONLY") == "1":
        print(f"PREP OK: container {cid} processed; not published (test mode).")
        return

    pub = call("POST", f"{uid}/media_publish", creation_id=cid)
    posted[reel["n"]] = {"status": "posted", "media_id": pub.get("id"), "at": now.isoformat()}
    json.dump(posted, open("posted.json", "w", encoding="utf-8"), indent=1)
    print("Published media id", pub.get("id"), flush=True)


def make_container(uid, reel, raw_url):
    """Upload the video bytes straight to Instagram (resumable upload).
    Falls back to letting Instagram fetch the public URL if that fails."""
    data = urllib.request.urlopen(raw_url, timeout=120).read()
    print(f"Downloaded {len(data)/1e6:.1f} MB", flush=True)
    try:
        c = call("POST", f"{uid}/media", media_type="REELS", upload_type="resumable",
                 caption=reel["caption"], share_to_feed="true", _soft=True)
        if c and "API access blocked" in str(c.get("error", "")):
            print("Meta says API access blocked; stopping.", flush=True)
            sys.exit(3)
        if c and c.get("id"):
            req = urllib.request.Request(
                f"https://rupload.facebook.com/ig-api-upload/v25.0/{c['id']}", data=data, method="POST",
                headers={"Authorization": f"OAuth {TOKEN}", "offset": "0", "file_size": str(len(data))})
            with urllib.request.urlopen(req, timeout=300) as r:
                print("Upload:", r.read().decode()[:200], flush=True)
            return c["id"]
        print("Resumable container failed:", c, flush=True)
    except Exception as e:
        print("Resumable upload failed, falling back to URL:", str(e)[:300], flush=True)
    c = call("POST", f"{uid}/media", media_type="REELS", video_url=raw_url,
             caption=reel["caption"], share_to_feed="true")
    return c["id"]


if __name__ == "__main__":
    main()
