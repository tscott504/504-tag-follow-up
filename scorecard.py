"""
504 Weekly Scorecard.

Builds scorecard.html from the cache refresh_tags.py keeps (leads, comments,
teammate actions, status changes) plus REsimpli's appointment list, and the
targets and closed deals in scorecard_config.json.

Run after refresh_tags.py:  python scorecard.py
"""
import html, json, os, re, sys, time
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import refresh_tags as rt

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "scorecard_config.json"), encoding="utf-8"))
OUT = os.environ.get("SCORECARD_OUT") or os.path.join(os.path.dirname(os.path.abspath(rt.OUT)), "scorecard.html")
TZ = ZoneInfo("America/Chicago")
WEEKS = 6
H, D = 3600_000, 86_400_000


def ms(dt):
    return int(dt.timestamp() * 1000)


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", (s or "").lower())).strip()


def week_starts(now):
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return [monday - timedelta(weeks=i) for i in range(WEEKS - 1, -1, -1)]


def fetch_appointments(api, start, end):
    out, page = [], 1
    while page <= 50:
        d = (api.post("appointmentList", {"appointmentType": 9, "startDateTime": start.isoformat(),
                                          "endDateTime": end.isoformat(), "page": page, "limit": 100}).get("data") or {})
        it = d.get("items") or []
        out += it
        if len(it) < 100:
            break
        page += 1
    return [a for a in out if a.get("appointmentType") == 0]   # seller appointments only


def to_status(text):
    m = re.search(r"\bto\b(?!.*\bto\b)(.*)$", text or "", re.I)
    return norm(m.group(1)) if m else ""


def main():
    api = rt.Api(rt.get_key())
    rt.check_account(api)
    cache = json.load(open(rt.CACHE, encoding="utf-8"))
    users = cache.get("users") or {}
    uid = {v: k for k, v in users.items()}
    team = CFG["team"]
    abbey, ron, waleed = uid.get(team["lead_manager"]), uid.get(team["acquisitions"]), uid.get(team["marketing_va"])
    land = [w.lower() for w in CFG.get("exclude_campaigns_containing", [])]
    T = CFG["weekly_targets"]

    now = datetime.now(TZ)
    nowms = ms(now)
    weeks = week_starts(now)
    wb = [(ms(w), ms(w + timedelta(weeks=1))) for w in weeks]

    def wk(t):
        for i, (a, b) in enumerate(wb):
            if a <= t < b:
                return i
        return None

    zero = lambda: [0] * WEEKS
    m = {k: zero() for k in ["leads", "leads_sms", "land", "appts_set", "appts_set_abbey", "appts_set_ai", "qualified",
                              "kept", "appts_due", "offers", "contracts", "abbey_calls", "abbey_texts", "ron_calls"]}

    leads = cache["leads"]
    for lid, L in leads.items():
        i = wk(L.get("created") or 0)
        camp = (L.get("campaign") or "").lower()
        if i is not None:
            if any(w in camp for w in land):
                m["land"][i] += 1
            else:
                m["leads"][i] += 1
                if re.search(r"sms|text", camp):
                    m["leads_sms"][i] += 1
        for at, txt in L.get("events") or []:
            j = wk(at)
            if j is None:
                continue
            st = to_status(txt)
            if re.search(r"make offer|offers made|offer made", st):
                m["offers"][j] += 1
            if "under contract" in st:
                m["contracts"][j] += 1
        for by, at, label in L.get("actions") or []:
            j = wk(at)
            if j is None:
                continue
            if by == abbey and label == "Called the seller":
                m["abbey_calls"][j] += 1
            if by == abbey and label == "Texted the seller":
                m["abbey_texts"][j] += 1
            if by == ron and label == "Called the seller":
                m["ron_calls"][j] += 1

    appts = fetch_appointments(api, weeks[0] - timedelta(days=1), now + timedelta(days=120))
    not_updated = []
    for a in appts:
        setter = a.get("setter") or {}
        sname = f'{setter.get("firstName") or ""} {setter.get("lastName") or ""}'.strip() if isinstance(setter, dict) else ""
        i = wk(a.get("createdAt") or 0)
        if i is not None:
            m["appts_set"][i] += 1
            if sname == team["lead_manager"]:
                m["appts_set_abbey"][i] += 1
            if a.get("isSetByAiAgent"):
                m["appts_set_ai"][i] += 1
        st = a.get("startDateTimeInTimeStamp") or 0
        j = wk(st)
        if j is not None and st < nowms:
            m["appts_due"][j] += 1
            if a.get("qualification") == 1:
                m["qualified"][j] += 1
            if a.get("appointmentStatus") == 1:
                m["kept"][j] += 1
        if st and nowms - 14 * D < st < nowms - 12 * H and (a.get("qualification", -1) == -1 or a.get("appointmentStatus", -1) == -1):
            not_updated.append(a)

    # Leaks right now
    untouched = []
    for lid, L in leads.items():
        if norm(L.get("status")) not in ("new leads", "new lead"):
            continue
        c = L.get("created") or 0
        if not c or nowms - c < D or any(w in (L.get("campaign") or "").lower() for w in land):
            continue
        if not any(a[1] > c for a in L.get("actions") or []):
            untouched.append((lid, L, nowms - c))
    untouched.sort(key=lambda x: -x[2])

    stale_tags = []
    cut = nowms - 30 * D
    for lid, L in leads.items():
        if any(w in (L.get("campaign") or "").lower() for w in rt.EXCLUDE_CAMPAIGNS):
            continue
        cs = L.get("comments") or []
        for c in cs:
            if c["at"] < cut or nowms - c["at"] < D:
                continue
            ids = set(rt.MENTION_ID.findall(c["html"]))
            first = {n.split()[0].lower(): k for k, n in users.items() if n}
            for mm in rt.PLAIN_AT.findall(rt.strip(rt.MENTION_SPAN.sub("", c["html"]))):
                if mm.lower() in first:
                    ids.add(first[mm.lower()])
            for u in ids:
                if u == c["by"] or u not in users or users[u] in rt.EXCLUDE_PEOPLE:
                    continue
                if not any(a[0] == u and a[1] > c["at"] for a in L.get("actions") or []):
                    stale_tags.append((users[u], lid, L, c["at"]))
    stale_by = {}
    for name, *_ in stale_tags:
        stale_by[name] = stale_by.get(name, 0) + 1

    # Q4
    q = CFG["q4"]
    deals = [d for d in CFG.get("closed_deals", []) if not d.get("example") and q["start"] <= d["date"] <= q["end"]]
    revenue = sum(float(d.get("revenue_to_504") or 0) for d in deals)
    q_end = datetime.fromisoformat(q["end"]).replace(tzinfo=TZ)
    q_start = datetime.fromisoformat(q["start"]).replace(tzinfo=TZ)
    pct_time = max(0, min(1, (now - q_start) / (q_end + timedelta(days=1) - q_start)))
    contracts_q = sum(1 for L in leads.values() for at, t in L.get("events") or []
                      if ms(q_start) <= at <= nowms and "under contract" in to_status(t))
    offers_4 = sum(m["offers"][-5:-1])
    kept_4 = sum(m["kept"][-5:-1])

    write_html(now, weeks, m, T, untouched, not_updated, stale_by, stale_tags, deals, revenue, q, pct_time,
               contracts_q, offers_4, kept_4, users)
    print(f"Scorecard written: {OUT}")


def fmt_money(v):
    return f"${v:,.0f}"


def status(actual, target, lower_is_better=False):
    if target is None:
        return ""
    if lower_is_better:
        return "good" if actual <= target else "bad"
    if actual >= target:
        return "good"
    if actual >= 0.75 * target:
        return "warn"
    return "bad"


LABEL = {"good": "On track", "warn": "Close", "bad": "Behind", "": ""}


def write_html(now, weeks, m, T, untouched, not_updated, stale_by, stale_tags, deals, revenue, q, pct_time,
               contracts_q, offers_4, kept_4, users):
    e = html.escape
    last, cur = WEEKS - 2, WEEKS - 1
    wlabel = lambda w: f"{w:%b} {w.day}"
    lw = weeks[last]

    rows = [
        ("Leads (excl. land)", "Walleed", "leads", T["leads"], f'stretch {T["leads_stretch"]}'),
        ("Leads from SMS campaigns", "Walleed", "leads_sms", None, "share of leads"),
        ("Appointments set", "Abbey", "appts_set", T["appointments_set"], ""),
        ("Qualified appointments", "Abbey", "qualified", T["qualified_appointments"], "by appointment date"),
        ("Appointments kept", "Ron", "kept", T["appointments_kept"], "by appointment date"),
        ("Offers made", "Ron", "offers", T["offers"], "moved to Make Offer"),
        ("Contracts", "Ron", "contracts", T["contracts"], "moved to Under Contract"),
        ("Abbey calls to sellers", "Abbey", "abbey_calls", T["abbey_calls"], "outbound, logged in REsimpli"),
    ]

    def bars(vals, target):
        mx = max([1] + vals + ([target] if target else []))
        out = []
        for i, v in enumerate(vals):
            h = max(2, round(36 * v / mx))
            cls = "cur" if i == cur else ("last" if i == last else "")
            out.append(f'<span class="b {cls}" style="height:{h}px" title="Week of {wlabel(weeks[i])}: {v}"></span>')
        tl = f'<span class="tl" style="bottom:{round(36 * target / mx)}px"></span>' if target else ""
        return f'<div class="spark">{tl}{"".join(out)}</div>'

    trs = []
    for name, owner, key, tgt, note in rows:
        v_last, v_cur = m[key][last], m[key][cur]
        s = status(v_last, tgt)
        tgt_s = (f"{tgt:g}" if tgt is not None else "–")
        trs.append(f"""<tr><td><div class="mname">{e(name)}</div><div class="mnote">{e(note)}</div></td><td class="own">{e(owner)}</td>
<td class="num big">{v_last}</td><td class="num">{tgt_s}</td><td>{f'<span class="pill {s}">{LABEL[s]}</span>' if s else ''}</td>
<td class="num muted">{v_cur}</td><td>{bars(m[key], tgt)}</td></tr>""")

    offer_rate = f"{round(100 * offers_4 / kept_4)}%" if kept_4 else "–"
    deals_n = len(deals)
    # pace check starts once a full month of the quarter has passed
    deal_s = status(deals_n, q["deals_goal"] * pct_time) if pct_time >= 1 / 3 else ""

    unt_rows = "".join(
        f'<li><a href="https://dashboard.resimpli.com/leads/details?leadsId={e(lid)}" target="_blank" rel="noopener">{e(L.get("address") or L.get("contact") or "No address")}</a>'
        f'<span>{e(L.get("campaign") or "")}</span><span class="age">{round(age / D)}d</span></li>'
        for lid, L, age in untouched[:12])
    nu_rows = "".join(
        f'<li>{e(a.get("address") or a.get("title") or "Appointment")}<span>{(lambda d: f"{d:%a} {d:%b} {d.day}")(datetime.fromtimestamp((a.get("startDateTimeInTimeStamp") or 0) / 1000, TZ))}</span>'
        f'<span class="age">{"no qualified mark" if a.get("qualification", -1) == -1 else "no kept mark"}</span></li>'
        for a in sorted(not_updated, key=lambda a: a.get("startDateTimeInTimeStamp") or 0)[:12])
    stale_s = ", ".join(f"{e(k)} {v}" for k, v in sorted(stale_by.items(), key=lambda x: -x[1])) or "none"
    deal_rows = "".join(f'<li>{e(d["date"])} · {e(d.get("address", ""))}<span>{e(d.get("type", ""))}</span><span class="age">{fmt_money(float(d.get("revenue_to_504") or 0))}</span></li>' for d in deals) or '<li class="muted">No closed deals logged for Q4 yet. Add them in scorecard_config.json.</li>'

    page = f"""<title>504 Weekly Scorecard</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: meeting sheet. Q4 strip, last week's numbers vs target, then the leaks to fix. */
:root{{--bg:#f3f4f7;--surface:#fff;--surface-2:#eceef3;--line:#d9dce5;--fg:#181b24;--fg-2:#4a5063;--fg-3:#7a8094;--accent:#3346d3;
--good:#0a8a0a;--good-soft:#e2f3e2;--warn:#b97800;--warn-soft:#fbf0d6;--crit:#c43434;--crit-soft:#f9e1e1;
--f-display:"Bricolage Grotesque",ui-sans-serif,system-ui,sans-serif;--f-body:"IBM Plex Sans",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;--f-mono:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace}}
@media (prefers-color-scheme:dark){{:root{{--bg:#12141a;--surface:#1b1e26;--surface-2:#232733;--line:#2f3442;--fg:#eceef4;--fg-2:#b2b7c6;--fg-3:#828899;--accent:#8e9bff;
--good:#5fd35f;--good-soft:#1c3320;--warn:#f4bc4a;--warn-soft:#3a2f17;--crit:#ff7f7f;--crit-soft:#3d1f22;color-scheme:dark}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 var(--f-body)}}
.wrap{{max-width:1100px;margin:0 auto;padding-inline:20px;padding-block:28px 56px;display:flex;flex-direction:column;gap:18px}}
h1,h2{{font-family:var(--f-display);margin:0;text-wrap:balance}}h1{{font-size:28px}}h2{{font-size:17px}}
.head{{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap}}.sub{{color:var(--fg-2);margin:4px 0 0}}
.stamp{{font:12px var(--f-mono);color:var(--fg-3)}}a{{color:var(--accent)}}
.nav{{display:flex;gap:14px;font-size:13px}}
.q4{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}
.card{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;min-width:0}}
.l{{font:500 11px var(--f-mono);letter-spacing:.08em;text-transform:uppercase;color:var(--fg-3)}}
.v{{font:700 28px/1.2 var(--f-display);font-variant-numeric:tabular-nums}}.d{{color:var(--fg-2);font-size:12.5px}}
.meter{{height:6px;background:var(--surface-2);border-radius:3px;margin-top:8px;position:relative;overflow:hidden}}.meter i{{position:absolute;left:0;top:0;bottom:0;background:var(--accent);border-radius:3px}}
.meter b{{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--fg-3)}}
.panel{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px;min-width:0}}
.scroll{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}}
th{{font:500 11px var(--f-mono);letter-spacing:.06em;text-transform:uppercase;color:var(--fg-3);text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}}
td{{padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:middle}}tr:last-child td{{border-bottom:0}}
.num{{text-align:right;font-family:var(--f-mono)}}.big{{font-size:16px;font-weight:600}}.muted{{color:var(--fg-3)}}
.mname{{font-weight:600}}.mnote{{font-size:12px;color:var(--fg-3)}}.own{{color:var(--fg-2)}}
.pill{{font:600 11.5px var(--f-body);border-radius:999px;padding:2px 9px;white-space:nowrap}}
.pill.good{{background:var(--good-soft);color:var(--good)}}.pill.warn{{background:var(--warn-soft);color:var(--warn)}}.pill.bad{{background:var(--crit-soft);color:var(--crit)}}
.spark{{position:relative;display:flex;align-items:flex-end;gap:3px;height:38px;min-width:96px}}
.spark .b{{width:12px;background:var(--surface-2);border-radius:3px 3px 1px 1px}}.spark .b.last{{background:var(--accent)}}.spark .b.cur{{background:var(--fg-3);opacity:.5}}
.spark .tl{{position:absolute;left:0;right:0;border-top:1px dashed var(--fg-3)}}
.grid3{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}
ul{{list-style:none;margin:8px 0 0;padding:0;display:flex;flex-direction:column}}ul li{{display:flex;gap:10px;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line);font-size:13px;min-width:0}}
ul li:last-child{{border-bottom:0}}ul li>*:first-child{{flex:1;min-width:0;overflow-wrap:anywhere}}ul li span{{color:var(--fg-3);white-space:nowrap}}ul li .age{{font-family:var(--f-mono);color:var(--crit)}}
.agenda{{margin:0;padding-left:20px;display:flex;flex-direction:column;gap:4px;color:var(--fg-2)}}
.foot{{color:var(--fg-3);font-size:12.5px;max-width:85ch}}
@media (max-width:860px){{.q4{{grid-template-columns:repeat(2,minmax(0,1fr))}}.grid3{{grid-template-columns:minmax(0,1fr)}}}}
@media (max-width:480px){{.wrap{{padding-inline:16px}}}}
</style>
<div class="wrap">
<header class="head"><div><h1>504 Weekly Scorecard</h1><p class="sub">Last week, {wlabel(lw)} to {wlabel(lw + timedelta(days=6))}, against the 1 deal a month plan.</p></div>
<div><div class="nav"><a href="./">Tag Follow-Up board</a></div><div class="stamp">Updated {wlabel(now)}, {now.year} {now.hour % 12 or 12}:{now:%M} {'AM' if now.hour < 12 else 'PM'} CT</div></div></header>

<section class="q4" aria-label="Q4 progress">
 <div class="card"><div class="l">Q4 deals closed</div><div class="v">{deals_n} <span class="d">of {q['deals_goal']}</span></div>{f'<span class="pill {deal_s}">{LABEL[deal_s]}</span>' if deal_s else ''}<div class="meter"><i style="width:{min(100, round(100 * deals_n / q['deals_goal']))}%"></i><b style="left:{round(100 * pct_time)}%"></b></div></div>
 <div class="card"><div class="l">Q4 revenue to 504</div><div class="v">{fmt_money(revenue)}</div><div class="d">goal {fmt_money(q['revenue_goal'])}</div><div class="meter"><i style="width:{min(100, round(100 * revenue / q['revenue_goal']))}%"></i><b style="left:{round(100 * pct_time)}%"></b></div></div>
 <div class="card"><div class="l">Contracts since Oct 1</div><div class="v">{contracts_q}</div><div class="d">plan is about 9 for the quarter</div></div>
 <div class="card"><div class="l">Offer rate, last 4 weeks</div><div class="v">{offer_rate}</div><div class="d">offers ÷ kept appointments ({kept_4} kept)</div></div>
</section>

<section class="panel"><h2>Last week vs target</h2>
<div class="scroll"><table><thead><tr><th>Metric</th><th>Owner</th><th class="num">Last week</th><th class="num">Target</th><th>Status</th><th class="num">This week</th><th>6 weeks</th></tr></thead>
<tbody>{''.join(trs)}</tbody></table></div></section>

<section class="grid3">
 <div class="panel"><h2>New leads untouched over 24h</h2><div class="v">{len(untouched)}</div><ul>{unt_rows or '<li class="muted">None. Every new lead has been worked.</li>'}</ul></div>
 <div class="panel"><h2>Appointments not updated</h2><div class="v">{len(not_updated)}</div><div class="d">Past appointments in the last 2 weeks missing a qualified or kept mark.</div><ul>{nu_rows or '<li class="muted">All appointments are updated.</li>'}</ul></div>
 <div class="panel"><h2>Tags with no action over 24h</h2><div class="v">{len(stale_tags)}</div><div class="d">Last 30 days. {stale_s}.</div><ul><li><a href="./">Open the Tag Follow-Up board</a></li></ul></div>
</section>

<section class="grid3">
 <div class="panel"><h2>Q4 closed deals</h2><ul>{deal_rows}</ul></div>
 <div class="panel" style="grid-column:span 2"><h2>Meeting agenda (15 min)</h2><ol class="agenda">
 <li>Leads and SMS volume. Walleed brings texts sent from his sheet.</li>
 <li>Appointments set, qualified and kept. Abbey.</li>
 <li>Offers and contracts, and the offer rate on kept appointments. Ron.</li>
 <li>Open contracts and expected close dates.</li>
 <li>Clear the three leak lists above before next week.</li></ol></div>
</section>

<p class="foot">Weeks run Monday to Sunday, Central time. Leads exclude land campaigns. Appointments count seller appointments only: "set" is by the day it was booked, qualified and kept are by the appointment date. Offers and contracts count leads moved into Make Offer or Under Contract that week. Abbey's calls are outbound calls logged in REsimpli. Closed deals and revenue come from scorecard_config.json, with listings counted at 504's 20% share.</p>
</div>"""
    doc = ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n'
           + page.replace('<div class="wrap">', '</head><body>\n<div class="wrap">', 1) + "\n</body></html>")
    open(OUT, "w", encoding="utf-8").write(doc)


if __name__ == "__main__":
    main()
