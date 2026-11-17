"""Upload scheduled YouTube Shorts (Harivadan Explains) via the YouTube Data API.

Env: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN (GitHub secrets)
     ONLY=<n> upload that one now | CHECK=1 test login only | DRY_RUN=1
Exit codes: 0 ok/nothing due, 1 error, 3 auth problem (stop), 4 quota hit (wait).
"""
import datetime as dt, json, os, re, sys, urllib.parse, urllib.request

RAW = "https://raw.githubusercontent.com/alphazetservices/hx-reels/main"
SCHED, LOG = "yt_schedule.json", "yt_posted.json"


def req(url, data=None, headers=None, method=None):
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=300) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def token():
    body = urllib.parse.urlencode({
        "client_id": os.environ["YT_CLIENT_ID"].strip(),
        "client_secret": os.environ["YT_CLIENT_SECRET"].strip(),
        "refresh_token": os.environ["YT_REFRESH_TOKEN"].strip(),
        "grant_type": "refresh_token"}).encode()
    st, _, b = req("https://oauth2.googleapis.com/token", body)
    if st != 200:
        print("Login failed:", st, b[:300].decode(errors="replace"), flush=True)
        sys.exit(3)
    return json.loads(b)["access_token"]


def clean(s):
    return s.replace("<", "").replace(">", "")


def meta(item):
    cap = clean(item["caption"]).replace("@harivadanexplains", "Harivadan Explains")
    first = cap.strip().split("\n")[0].strip()
    first = re.sub(r"#\w+", "", first).strip() or "Harivadan Explains"
    title = (first[:90].rstrip() + " #Shorts")[:100]
    desc = cap.replace("Follow Harivadan Explains", "Subscribe to Harivadan Explains")
    tags = re.findall(r"#(\w+)", cap)[:10] + ["shorts"]
    return title, desc[:4900], tags


def upload(tok, item):
    title, desc, tags = meta(item)
    st, _, video = req(f"{RAW}/{item['file']}")
    if st != 200:
        print("Could not fetch video", item["file"], st, flush=True); sys.exit(1)
    body = json.dumps({
        "snippet": {"title": title, "description": desc, "tags": tags,
                    "categoryId": "27", "defaultLanguage": "en"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": True, "embeddable": True}}).encode()
    h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8",
         "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(len(video))}
    st, hd, b = req("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable"
                    "&part=snippet,status", body, h, "POST")
    if st != 200:
        txt = b[:500].decode(errors="replace"); print("Start upload failed:", st, txt, flush=True)
        if "quota" in txt.lower() or "uploadLimitExceeded" in txt: sys.exit(4)
        if st in (401, 403): sys.exit(3)
        sys.exit(1)
    loc = hd.get("Location") or hd.get("location")
    st, _, b = req(loc, video, {"Content-Type": "video/mp4"}, "PUT")
    if st not in (200, 201):
        txt = b[:500].decode(errors="replace"); print("Upload failed:", st, txt, flush=True)
        if "quota" in txt.lower() or "uploadLimitExceeded" in txt: sys.exit(4)
        sys.exit(1)
    v = json.loads(b)
    print(f"Uploaded {item['n']}: https://youtube.com/shorts/{v['id']} status={v.get('status')}", flush=True)
    return v


def main():
    sched = json.load(open(SCHED, encoding="utf-8"))
    posted = json.load(open(LOG, encoding="utf-8")) if os.path.exists(LOG) else {}
    if os.environ.get("CHECK") == "1":
        tok = token()
        st, _, b = req("https://www.googleapis.com/youtube/v3/channels?part=snippet,status&mine=true",
                       headers={"Authorization": f"Bearer {tok}"})
        print("Channel:", st, b[:600].decode(errors="replace")); print("Login OK")
        ids = ",".join(v["id"] for v in list(posted.values())[-5:])
        if ids:
            st, _, b = req(f"https://www.googleapis.com/youtube/v3/videos?part=status,processingDetails&id={ids}",
                           headers={"Authorization": f"Bearer {tok}"})
            print("Recent videos:", b[:1500].decode(errors="replace"))
        return
    only = os.environ.get("ONLY", "").strip()
    now = dt.datetime.now(dt.timezone.utc)
    if only:
        due = [r for r in sched if r["n"] == only]
    else:
        due = sorted((r for r in sched if r["n"] not in posted
                      and dt.datetime.fromisoformat(r["post_at"]) <= now), key=lambda r: r["post_at"])
    if not due:
        print("Nothing due."); return
    item = due[0]
    if item["n"] in posted and not only:
        return
    if os.environ.get("DRY_RUN") == "1":
        print("Would upload", item["n"], meta(item)[0]); return
    v = upload(token(), item)
    posted[item["n"]] = {"id": v["id"], "at": now.isoformat(),
                         "privacy": v.get("status", {}).get("privacyStatus")}
    json.dump(posted, open(LOG, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
