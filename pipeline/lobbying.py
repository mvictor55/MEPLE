"""
Lobbying disclosures from the Maine Ethics Commission (mainelobbying.com).

What the law gives us (3 M.R.S. §317): every registered lobbyist files a
monthly report during session, jointly with the client, listing the specific
bills (LDs) lobbied that month and the compensation paid by that client. The
reports do NOT say whether the lobbying was for or against a bill, and the
compensation is per client per month, not per bill. The site must say both.

The public portal (https://mainelobbying.com/public/dashboard) is a JavaScript
app. Its data API (found 2026-11-07 with `discover` and the portal's JS bundle):
  api.mainelobbying.com/api/advanced-search/monthly-report/bills-and-issues?pageSize=100&pageNumber=N
      one row per (monthly report, LD or issue): billOrIssue, ldNumber, isIssue, year, month,
      primaryLobbyistName, clientRegistrationName, filingId, filingVersion
  .../monthly-report/compensation-for-lobbying?pageSize=100&pageNumber=N
      one row per monthly report: compensationForLobbying, year, month, filingId, filingVersion
  Pages are capped at 100 rows. `year` is the LOBBYING year, which runs December-November:
  lobbying year 2026, month 12 is December 2025. Years 2025 and 2026 cover the 132nd.
  This is the same data as the advanced search's "Download CSV".

  STEP 0  python -m pipeline.lobbying discover
          Opens the portal in headless Chromium, clicks into the search/report
          views, and logs every XHR/fetch URL to pipeline/raw/lobbying/discovered.json.
          Fill LOBBYING_ENDPOINTS below (and the field map in normalize_record).

  STEP 1  python -m pipeline.lobbying index
          Pulls every monthly report for the session into raw/lobbying/reports.json.

  STEP 2  python -m pipeline.lobbying build
          Writes site/data/lobbying/LD{ld}.json (reports naming that bill) and
          site/data/lobbying/index.json (totals by client and by lobbyist, most-
          lobbied bills) for the site to load lazily.

If the portal offers a CSV/Excel export of monthly reports, prefer that: put the
file at pipeline/raw/lobbying/export.csv and run `index --csv`.
"""
import csv, json, re, sys
from collections import defaultdict
from pathlib import Path
from . import config, fetch

RAW_L = config.RAW / "lobbying"
PORTAL = "https://mainelobbying.com/public/dashboard"
API = "https://api.mainelobbying.com/api/advanced-search/monthly-report"
LOBBYING_ENDPOINTS = {"bills": API + "/bills-and-issues", "compensation": API + "/compensation-for-lobbying"}
SEARCH_URL = "https://mainelobbying.com/advanced-search"
LOBBYING_YEARS = ("2025", "2026")    # lobbying years overlapping the 132nd

# Lobbying client -> testimony profile, when the names differ (reviewed 2026-11-08). These only link the client
# page and the profile; counts are never merged. Deliberately NOT linked: Maine Tourism Association (separate from
# HospitalityMaine; the joint profile is their joint filings), United States of Care Action (vs United States of
# Care) and Elevance Health (vs Anthem Blue Cross and Blue Shield), which are separate legal entities.
CLIENT_PROFILE_ALIASES = {
    "Oxford Casino - Churchill Downs Inc.": "Oxford Casino and Hotel",
    "Pharmaceutical Research and Manufacturers of America (PhRMA)": "PhRMA",
    "Charter Communications Operating, LLC": "Charter Communications",
    "Cannabis Association of Maine, Inc., d/b/a CannabisME": "Cannabis Association of Maine",
    "Maine Equal Justice Partners, Inc.": "Maine Equal Justice",
    "Maine Vapors Association": "Maine Vapers Association",
    "Alliance for Addiction and Mental Health Services, Maine": "Alliance for Addiction and Mental Health Services",
    "Legal Services for the Elderly": "Legal Services for Maine Elders",   # renamed; its testimony says "formerly known as"
    "Comcast Cable Communications Management, LLC": "Comcast",
    "Bioscience Association of Maine (BioME)": "Bioscience Association of Maine",
    "Maine Alliance of Boys & Girls Club": "Maine Alliance of Boys & Girls Clubs",
    "CTIA - The Wireless Association": "CTIA",
}

def org_key(s) -> str:
    """The name rule for client <-> profile links (same as the site used before links were built here)."""
    s = str(s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(the|inc|llc|corp|corporation)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()

def client_profiles(clients) -> dict:
    """client name -> profile id, by the name rule or CLIENT_PROFILE_ALIASES (organizations and agencies only)."""
    p = config.OUT / "profiles" / "index.json"
    if not p.exists(): return {}
    by_key = {}
    for x in json.loads(p.read_text(encoding="utf-8")):
        if x["type"] != "official": by_key.setdefault(org_key(x["name"]), x["id"])
    alias = {k.strip(): v for k, v in CLIENT_PROFILE_ALIASES.items()}
    out = {}
    for c in clients:
        pid = by_key.get(org_key(alias.get(c.strip(), c)))
        if pid: out[c] = pid
    return out

def discover():
    from playwright.sync_api import sync_playwright
    seen = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.on("request", lambda r: seen.append({"type": r.resource_type, "method": r.method, "url": r.url, "post": (r.post_data or "")[:500]}) if r.resource_type in ("xhr", "fetch") else None)
        page.goto(PORTAL, wait_until="networkidle"); page.wait_for_timeout(2500)
        # Click anything that looks like a search or reports view, then try a bill search.
        for label in ("Search", "Lobbyist", "Client", "Reports", "Monthly", "Bill", "Legislation", "Export", "Download"):
            try:
                el = page.get_by_text(label, exact=False).first
                if el and el.is_visible():
                    el.click(); page.wait_for_timeout(2000)
            except Exception:
                pass
        for sel in page.query_selector_all("input[type=text], input[type=search]"):
            try:
                sel.fill("LD 1822"); sel.press("Enter"); page.wait_for_timeout(2500); break
            except Exception:
                pass
        b.close()
    RAW_L.mkdir(parents=True, exist_ok=True)
    (RAW_L / "discovered.json").write_text(json.dumps(seen, indent=1))
    for s in seen:
        print(s["method"], s["url"], s["post"][:120])
    print(f"\n{len(seen)} data requests logged -> {RAW_L/'discovered.json'}")

LD_RE = re.compile(r"\bLD\s*(\d{1,4})\b", re.I)

def normalize_record(r: dict) -> dict:
    """Map one portal record (shape UNVERIFIED) to MEPLE's lobbying row."""
    def pick(*names):
        for n in names:
            for k in r:
                if k.lower().replace("_", "").replace(" ", "") == n.lower().replace("_", "").replace(" ", ""):
                    return r[k]
        return None
    bills_raw = pick("bills", "legislation", "ldnumbers", "billsLobbied") or ""
    if isinstance(bills_raw, list):
        bills = [int(x) for b in bills_raw for x in LD_RE.findall(str(b))]
    else:
        bills = [int(x) for x in LD_RE.findall(str(bills_raw))]
    comp = pick("compensation", "compensationAmount", "amount", "totalCompensation")
    try:
        comp = float(str(comp).replace("$", "").replace(",", "")) if comp not in (None, "") else None
    except ValueError:
        comp = None
    return {
        "report_id": str(pick("id", "reportId", "filingId") or ""),
        "lobbyist": pick("lobbyist", "lobbyistName", "lobbyist_name"),
        "firm": pick("firm", "lobbyistFirm", "organization"),
        "client": pick("client", "clientName", "employer", "employerName"),
        "year": str(pick("lobbyingYear", "year") or ""),
        "month": pick("month", "reportMonth", "period"),
        "compensation_month": comp,
        "bills": sorted(set(bills)),
        "url": pick("url", "reportUrl", "link"),
        "raw_keys": sorted(r.keys())[:40],
    }

def _pages(url: str) -> list[dict]:
    out, n = [], 1
    while True:
        d = fetch.get_json(f"{url}?pageSize=100&pageNumber={n}", refresh=True)
        items = d.get("items") or []
        out.extend(items)
        if not items or len(out) >= (d.get("total") or 0):
            return out
        n += 1
        if n % 50 == 0:
            print(f"  ...{len(out)}/{d.get('total')} rows from {url.rsplit('/', 1)[-1]}", file=sys.stderr, flush=True)

def _calendar(year: int, month: int) -> tuple[int, int]:
    """Lobbying year runs December-November: (lobbying year 2026, month 12) -> December 2025."""
    return (year - 1, 12) if month == 12 else (year, month)

def index(from_csv: bool = False):
    RAW_L.mkdir(parents=True, exist_ok=True)
    if from_csv:
        rows = []
        with open(RAW_L / "export.csv", newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                rows.append(normalize_record(r))
    else:
        years = {int(y) for y in LOBBYING_YEARS}
        bills = [r for r in _pages(LOBBYING_ENDPOINTS["bills"]) if r.get("year") in years]
        comp = [r for r in _pages(LOBBYING_ENDPOINTS["compensation"]) if r.get("year") in years]
        (RAW_L / "bills_and_issues.json").write_text(json.dumps(bills), encoding="utf-8")
        (RAW_L / "compensation.json").write_text(json.dumps(comp), encoding="utf-8")
        # one report per filingId, latest version (amended reports keep the id)
        latest = {}
        for r in comp + bills:
            latest[r["filingId"]] = max(latest.get(r["filingId"], 0), r.get("filingVersion") or 0)
        reports = {}
        for r in comp + bills:
            if (r.get("filingVersion") or 0) != latest[r["filingId"]]:
                continue
            cy, cm = _calendar(r["year"], r["month"])
            rep_ = reports.setdefault(r["filingId"], {
                "report_id": r["filingId"], "lobbyist": r.get("primaryLobbyistName"), "firm": None,
                "client": r.get("clientRegistrationName"), "year": str(r["year"]), "month": r["month"],
                "period": f"{cy}-{cm:02d}", "compensation_month": None, "bills": set(), "issues": set(), "url": SEARCH_URL})
            if "compensationForLobbying" in r:
                rep_["compensation_month"] = round(float(r["compensationForLobbying"] or 0), 2)
            elif r.get("isIssue") or not r.get("ldNumber"):
                if r.get("issueAndInterest"): rep_["issues"].add(r["issueAndInterest"])
            else:
                rep_["bills"].add(int(r["ldNumber"]))
        rows = [{**v, "bills": sorted(v["bills"]), "issues": sorted(v["issues"])} for v in reports.values()]
        print(f"{len(bills)} report-bill rows and {len(comp)} compensation rows for lobbying years {sorted(years)}")
    rows = [r for r in rows if r["bills"] or r["client"]]
    (RAW_L / "reports.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"{len(rows)} monthly reports -> {RAW_L/'reports.json'}; {sum(1 for r in rows if r['bills'])} name at least one LD")

def build():
    if not (RAW_L / "reports.json").exists():
        print("lobbying: no pipeline/raw/lobbying/reports.json yet; skipping (run `python -m pipeline.lobbying index` first)"); return
    rows = json.loads((RAW_L / "reports.json").read_text(encoding="utf-8"))
    out = config.OUT / "lobbying"; out.mkdir(parents=True, exist_ok=True)
    # Compensation is reported per client per month across every bill in the report, so nothing here is
    # summed, sorted or ranked by pay, and per-bill files carry no dollar figures.
    by_ld = defaultdict(list)
    by_client = defaultdict(lambda: {"reports": 0, "bills": set(), "months": defaultdict(set), "lobbyists": set()})
    by_lobbyist = defaultdict(lambda: {"reports": 0, "clients": set(), "bills": set(), "firms": defaultdict(int)})
    for r in rows:
        for ld in r["bills"]:
            by_ld[ld].append({k: r.get(k) for k in ("report_id", "lobbyist", "firm", "client", "year", "month", "period", "url")} | {"bills_in_report": len(r["bills"])})
        c = by_client[r["client"] or "—"]; c["reports"] += 1; c["bills"].update(r["bills"])
        c["lobbyists"].add((r["lobbyist"] or "", r["firm"] or ""))
        for ld in r["bills"]:
            if r.get("period"): c["months"][ld].add(r["period"])
        l = by_lobbyist[r["lobbyist"] or "—"]; l["reports"] += 1; l["clients"].add(r["client"] or "—"); l["bills"].update(r["bills"])
        if r["firm"]: l["firms"][r["firm"]] += 1
    # Distinct clients per bill: the client name exactly as filed, the same grouping the bill page's lobbying panel uses.
    clients_of = {ld: len({i["client"] for i in v if i["client"]}) for ld, v in by_ld.items()}
    # One file per client (lobbyists, bills and the calendar months each bill was reported), kept out of the main index.
    slugs, used = {}, set()
    for k in sorted(by_client):
        base = re.sub(r"[^a-z0-9]+", "-", k.lower()).strip("-")[:80] or "client"
        sl, i = base, 2
        while sl in used: sl, i = f"{base}-{i}", i + 1
        used.add(sl); slugs[k] = sl
    prof_of = client_profiles(by_client)
    print(f"lobbying: {len(prof_of)} of {len(by_client)} clients linked to a testimony profile")
    # Each client's written testimony on the same LD: a letter filed under this LD (the bill's own testimony list, so a
    # jointly heard bill's letters count only where they were filed) whose organization is the client's testimony profile,
    # or whose organization name matches the client by the same name rule and aliases. Lobbying reports state no position;
    # positions come from the letters.
    alias = {k.strip(): v for k, v in CLIENT_PROFILE_ALIASES.items()}
    n_matched = 0
    for ld, items in by_ld.items():
        items.sort(key=lambda x: (x.get("period") or "", x["client"] or ""))
        clients = sorted({i["client"] for i in items if i["client"]})
        bp = config.OUT / "bills" / f"LD{ld}.json"
        letters = json.loads(bp.read_text(encoding="utf-8")).get("testimony", []) if bp.exists() else []
        testimony = {}
        for c in clients:
            pid, ck = prof_of.get(c), org_key(alias.get(c.strip(), c))
            hit = [t for t in letters if t.get("organization") and ((pid and t.get("org_profile_id") == pid) or org_key(t["organization"]) == ck)]
            if hit:
                testimony[c] = [{"id": t["id"], "position": t.get("position"), "position_source": t.get("position_source"),
                                 "name": t.get("name"), "organization": t.get("organization")} for t in hit]
        n_matched += len(testimony)
        (out / f"LD{ld}.json").write_text(json.dumps({
            "ld": ld, "reports": items,
            "clients": clients,
            "testimony": testimony,
            "note": "Monthly lobbyist reports filed with the Maine Ethics Commission name the bills lobbied that month. Compensation is reported per client per month across all bills in a report, so there is no per-bill dollar figure. The reports do not state a position on the bill.",
            "source": PORTAL}, ensure_ascii=False), encoding="utf-8")
    print(f"lobbying: {n_matched} bill-client pairs also have written testimony on that bill")
    cdir = out / "clients"
    if cdir.exists():
        for p in cdir.glob("*.json"): p.unlink()
    cdir.mkdir(parents=True, exist_ok=True)
    for k, v in by_client.items():
        (cdir / f"{slugs[k]}.json").write_text(json.dumps({
            "client": k, "slug": slugs[k], "reports": v["reports"], "profile_id": prof_of.get(k),
            "lobbyists": [{"lobbyist": a, "firm": f or None} for a, f in sorted(v["lobbyists"])],
            "bills": sorted(({"ld": ld, "months": sorted(v["months"].get(ld, ()))} for ld in v["bills"]), key=lambda x: (-len(x["months"]), x["ld"])),
            "source": PORTAL}, ensure_ascii=False), encoding="utf-8")
    (cdir / "index.json").write_text(json.dumps({k: slugs[k] for k in sorted(by_client)}, ensure_ascii=False), encoding="utf-8")
    idx = {
        "years": list(LOBBYING_YEARS),
        "most_lobbied": sorted(({"ld": ld, "reports": len(v), "clients": clients_of[ld]} for ld, v in by_ld.items()), key=lambda x: (-x["clients"], -x["reports"], x["ld"]))[:100],
        "by_client": sorted(({"client": k, "slug": slugs[k], "reports": v["reports"], "bills": len(v["bills"]), **({"profile_id": prof_of[k]} if k in prof_of else {})} for k, v in by_client.items()), key=lambda x: (-x["reports"], x["client"])),
        "by_lobbyist": sorted(({"lobbyist": k, "firm": max(v["firms"], key=v["firms"].get) if v["firms"] else None, "reports": v["reports"],
                                "clients": len(v["clients"]), "bills": len(v["bills"])} for k, v in by_lobbyist.items()), key=lambda x: (-x["reports"], x["lobbyist"])),
        "source": PORTAL,
        "first_period": min((r["period"] for r in rows if r.get("period")), default=None),
        "last_period": max((r["period"] for r in rows if r.get("period")), default=None),
        "lds": sorted(by_ld.keys()),   # which LD{n}.json files exist, so the site never requests a missing one
    }
    (out / "index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    # Add the counts to the bill index so the Bills list can sort without fetching per-bill files (0 = no lobbying reported).
    bi = config.OUT / "bills" / "index.json"
    if bi.exists():
        bills = json.loads(bi.read_text(encoding="utf-8"))
        for b in bills:
            b["lobbying_clients"] = clients_of.get(b["ld"], 0)
            b["lobbying_reports"] = len(by_ld.get(b["ld"], []))
        bi.write_text(json.dumps(bills, ensure_ascii=False), encoding="utf-8")
    print(f"lobbying: {len(by_ld)} bills with reports, {len(by_client)} clients -> {out}; counts added to bills/index.json")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "discover"
    {"discover": discover, "index": lambda: index("--csv" in sys.argv), "build": build}[cmd]()
