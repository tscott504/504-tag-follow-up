"""
Tag Follow-Up refresher for REsimpli.

Pulls every lead's comments through the REsimpli Open API, finds @mentions,
checks whether the tagged person left a note on that lead afterward, and
rebuilds tag-follow-up-504.html next to this script.

Run:  python refresh_tags.py            (or double-click refresh.bat on Windows)
Key:  RESIMPLI_API_KEY environment variable, or api_key.txt next to this script.

The first run reads every lead, so it takes a while at the API's documented
100 requests/minute. Later runs only re-read leads that changed since the
last run (cached in tag_cache.json), so they are much faster.
Only the Python standard library is used (Python 3.9+).
"""
import json, os, re, sys, time, html, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

BASE = "https://api.resimpli.com/api/v6/openapi/"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get("TAG_CACHE") or os.path.join(HERE, "tag_cache.json")
TEMPLATE = os.path.join(HERE, "template.html")
OUT = os.environ.get("BOARD_OUT") or os.path.join(HERE, "tag-follow-up-504.html")
RATE_PER_MIN = 100        # documented Open API limit per key
WINDOW_DAYS = 120         # how far back the dashboard shows tags
WORKERS = 4
EXCLUDE_PEOPLE = {"Jamie Hawthorne", "Liz Olson"}   # tags to or from these people are left out
EXCLUDE_CAMPAIGNS = {"land marketing"}             # leads in these campaigns are left out (lowercase)
EXPECTED_EMAIL_DOMAIN = "504homebuyers.com"   # refuse to run if the key belongs to any other account
AUTO = re.compile(r"Review this lead|New lead to review|Lead Moved to Follow-Up")
MENTION_ID = re.compile(r'data-id="([0-9a-f]{24})"')
MENTION_SPAN = re.compile(r'<span class="mention".*?</span>\s*﻿?</span>', re.S)
PLAIN_AT = re.compile(r"@([A-Za-z]+)")
TAG_RE = re.compile(r"<[^>]+>")


def get_key():
    k = os.environ.get("RESIMPLI_API_KEY", "").strip()
    p = os.path.join(HERE, "api_key.txt")
    if not k and os.path.exists(p):
        k = open(p, encoding="utf-8").read().strip()
    if not k:
        if not sys.stdin.isatty():
            sys.exit("Set RESIMPLI_API_KEY (in GitHub: Settings > Secrets and variables > Actions).")
        k = input("Paste your REsimpli Open API key: ").strip()
        if input("Save it to api_key.txt for next time? [y/N] ").lower().startswith("y"):
            open(p, "w", encoding="utf-8").write(k)
    return k


class Limiter:
    def __init__(self, per_min):
        self.gap = 60.0 / per_min
        self.lock = threading.Lock()
        self.next = time.monotonic()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.gap
        d = t - time.monotonic()
        if d > 0:
            time.sleep(d)


class Api:
    def __init__(self, key, base=BASE):
        self.key, self.base, self.lim, self.calls = key, base, Limiter(RATE_PER_MIN), 0

    def post(self, path, body):
        data = json.dumps(body).encode()
        for attempt in range(6):
            self.lim.wait()
            req = urllib.request.Request(self.base + path, data=data, method="POST",
                                         headers={"Content-Type": "application/json", "Authorization": self.key})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    self.calls += 1
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    sys.exit("The API key was rejected (401). Check api_key.txt or RESIMPLI_API_KEY.")
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(min(60, 2 ** attempt * 3))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError):
                time.sleep(min(60, 2 ** attempt * 3))
        raise RuntimeError(f"Gave up on {path} after retries")


def items_of(resp):
    d = (resp or {}).get("data") or {}
    return d.get("items", d) if isinstance(d, dict) else d


def strip(h):
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", h or "")).replace("﻿", "")).strip()


def progress(msg):
    sys.stdout.write("\r" + msg.ljust(70))
    sys.stdout.flush()


ACCOUNT_ID = None


def check_account(api):
    """Confirm the key belongs to the 504 Home Buyers account and remember its id."""
    global ACCOUNT_ID
    me = api.post("getUserInfo", {}).get("data") or {}
    email = (me.get("email") or "").lower()
    if not email.endswith("@" + EXPECTED_EMAIL_DOMAIN):
        sys.exit(f"This key belongs to {email or 'an unknown account'}, not {EXPECTED_EMAIL_DOMAIN}. Stopping.")
    ACCOUNT_ID = me.get("mainUserId") or me.get("_id")
    print(f"Account: {me.get('firstName','')} {me.get('lastName','')} ({email})")


def fetch_comments(api, lead_id):
    out, page = [], 1
    while page <= 30:
        it = items_of(api.post("activityList", {"moduleId": 1, "subModuleId": lead_id, "type": 1, "page": page, "limit": 100})) or []
        for x in it:
            if x.get("activityType") == 8 and x.get("mainUserId") == ACCOUNT_ID:
                r = x.get("reply") or {}
                out.append({"by": x.get("createdBy"), "byName": x.get("createdByName") or "", "at": x.get("createdAt") or 0,
                            "html": x.get("comment") or "",
                            "replyUsers": [u.get("userId") for u in (r.get("userData") or [])],
                            "replyAt": r.get("createdAt") or 0})
        if len(it) < 100:
            break
        page += 1
    return out


def fetch_contact(api, lead_id):
    try:
        d = (api.post("lead/details", {"leadId": lead_id}).get("data") or {}).get("leadData") or {}
        c = (d.get("contactData") or [{}])[0] or {}
        name = (c.get("fullName") or " ".join(x for x in [c.get("firstName"), c.get("lastName")] if x) or "").strip()
        ph = next((p.get("phoneNumber") for p in (c.get("phoneNumbers") or []) if p.get("phoneNumber")), "")
        return {"contact": name, "phone": ph or ""}
    except Exception:
        return {"contact": "", "phone": ""}


def main():
    t0 = time.time()
    api = Api(get_key())
    check_account(api)
    cache = {"leads": {}}
    if os.path.exists(CACHE):
        try:
            cache = json.load(open(CACHE, encoding="utf-8"))
        except Exception:
            pass

    users = {}
    page = 1
    while True:
        it = items_of(api.post("userList", {"page": page, "limit": 100})) or []
        for u in it:
            users[u["_id"]] = (f'{u.get("firstName") or ""} {u.get("lastName") or ""}').strip()
        if len(it) < 100:
            break
        page += 1
    first = {n.split()[0].lower(): i for i, n in users.items() if n}

    leads, page = [], 1
    while True:
        resp = api.post("lead/list", {"page": page, "limit": 100})
        it = items_of(resp) or []
        leads += it
        total = (resp.get("data") or {}).get("count") or len(leads)
        progress(f"Reading lead list: {len(leads)}/{total}")
        if len(it) < 100:
            break
        page += 1
    print()

    seen = set()
    todo = []
    for l in leads:
        lid = l["_id"]
        seen.add(lid)
        c = cache["leads"].get(lid)
        meta = {"address": l.get("address") or "", "status": l.get("mainStatusTitle") or "",
                "campaign": l.get("marketingTitle") or "", "updatedAt": l.get("updatedAt") or 0}
        if c and c.get("updatedAt") == meta["updatedAt"] and "comments" in c:
            c.update(meta)
        else:
            cache["leads"][lid] = {**(c or {}), **meta}
            todo.append(lid)
    for lid in list(cache["leads"]):
        if lid not in seen:
            del cache["leads"][lid]

    mins = len(todo) / RATE_PER_MIN
    print(f"{len(todo)} of {len(leads)} leads changed since last run (about {mins:.0f} min at {RATE_PER_MIN}/min).")
    done = [0]
    lock = threading.Lock()

    def work(lid):
        cs = fetch_comments(api, lid)
        with lock:
            cache["leads"][lid]["comments"] = cs
            done[0] += 1
            if done[0] % 200 == 0:
                save(cache)
        if done[0] % 10 == 0 or done[0] == len(todo):
            progress(f"Reading comments: {done[0]}/{len(todo)}")

    with ThreadPoolExecutor(WORKERS) as ex:
        list(ex.map(work, todo))
    print()

    now = int(time.time() * 1000)
    cut = now - WINDOW_DAYS * 864e5
    tags = []
    for lid, L in cache["leads"].items():
        cs = L.get("comments") or []
        for c in cs:
            if c["at"] < cut:
                continue
            ids = set(MENTION_ID.findall(c["html"]))
            for m in PLAIN_AT.findall(strip(MENTION_SPAN.sub("", c["html"]))):
                if m.lower() in first:
                    ids.add(first[m.lower()])
            for uid in ids:
                if uid == c["by"] or uid not in users:
                    continue
                later = sorted((x for x in cs if x["by"] == uid and x["at"] > c["at"]), key=lambda x: x["at"])
                reply_at, reply_text = 0, ""
                if later:
                    reply_at, reply_text = later[0]["at"], strip(later[0]["html"])[:120]
                elif uid in c["replyUsers"] and c["replyAt"]:
                    reply_at = c["replyAt"]
                text = strip(c["html"])[:260]
                tags.append({"lid": lid, "from": c["byName"] or users.get(c["by"], "Former user"), "to": users[uid],
                             "at": c["at"], "text": text, "auto": 1 if AUTO.search(text) else 0,
                             "replyAt": reply_at, "reply": reply_text})

    tags = [t for t in tags if t["from"] not in EXCLUDE_PEOPLE and t["to"] not in EXCLUDE_PEOPLE
            and (cache["leads"][t["lid"]].get("campaign") or "").lower() not in EXCLUDE_CAMPAIGNS]

    need = [lid for lid in {t["lid"] for t in tags} if "contact" not in cache["leads"][lid]]
    if need:
        print(f"Looking up seller name and phone for {len(need)} tagged leads...")
        with ThreadPoolExecutor(WORKERS) as ex:
            for lid, info in zip(need, ex.map(lambda i: fetch_contact(api, i), need)):
                cache["leads"][lid].update(info)
    save(cache)

    tags.sort(key=lambda t: -t["at"])
    people, pidx, lrows, lidx = [], {}, [], {}

    def P(name):
        if name not in pidx:
            pidx[name] = len(people)
            people.append(name)
        return pidx[name]

    rows = []
    for t in tags:
        if t["lid"] not in lidx:
            L = cache["leads"][t["lid"]]
            lidx[t["lid"]] = len(lrows)
            lrows.append([L.get("address", ""), L.get("status", ""), L.get("contact", ""), L.get("campaign", ""), L.get("phone", ""), t["lid"]])
        rows.append([lidx[t["lid"]], P(t["from"]), P(t["to"]), t["at"], t["text"], t["auto"], t["replyAt"], t["reply"]])

    data = {"generatedAt": now, "sample": False, "people": people, "leads": lrows, "tags": rows}
    page_html = open(TEMPLATE, encoding="utf-8").read()
    page_html = page_html.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    page_html = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
                 '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
                 + page_html.replace('<div class="wrap">', '</head><body>\n<div class="wrap">', 1) + "\n</body></html>")
    open(OUT, "w", encoding="utf-8").write(page_html)
    open_count = sum(1 for t in tags if not t["replyAt"])
    print(f"Done in {(time.time()-t0)/60:.1f} min, {api.calls} API calls. {len(tags)} tags, {open_count} waiting on a note.")
    print(f"Open {OUT}")


def save(cache):
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, separators=(",", ":"))
    os.replace(tmp, CACHE)


if __name__ == "__main__":
    main()
