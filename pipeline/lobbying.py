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
    by_ld, by_client, by_lobbyist = defaultdict(list), defaultdict(lambda: {"reports": 0, "compensation": 0.0, "bills": set()}), defaultdict(lambda: {"reports": 0, "compensation": 0.0, "clients": set()})
    for r in rows:
        for ld in r["bills"]:
            by_ld[ld].append({k: r.get(k) for k in ("report_id", "lobbyist", "firm", "client", "year", "month", "period", "compensation_month", "url")} | {"bills_in_report": len(r["bills"])})
        c = by_client[r["client"] or "—"]; c["reports"] += 1; c["compensation"] += r["compensation_month"] or 0; c["bills"].update(r["bills"])
        l = by_lobbyist[r["lobbyist"] or "—"]; l["reports"] += 1; l["compensation"] += r["compensation_month"] or 0; l["clients"].add(r["client"] or "—")
    for ld, items in by_ld.items():
        items.sort(key=lambda x: (x.get("period") or "", x["client"] or ""))
        (out / f"LD{ld}.json").write_text(json.dumps({
            "ld": ld, "reports": items,
            "clients": sorted({i["client"] for i in items if i["client"]}),
            "note": "Monthly lobbyist reports filed with the Maine Ethics Commission name the bills lobbied that month and the client's compensation for the month across all bills. They do not state a position on the bill.",
            "source": PORTAL}, ensure_ascii=False))
    idx = {
        "years": list(LOBBYING_YEARS),
        "most_lobbied": sorted(({"ld": ld, "reports": len(v), "clients": len({i['client'] for i in v})} for ld, v in by_ld.items()), key=lambda x: -x["reports"])[:100],
        "by_client": sorted(({"client": k, "reports": v["reports"], "compensation": round(v["compensation"], 2), "bills": len(v["bills"])} for k, v in by_client.items()), key=lambda x: -x["compensation"])[:300],
        "by_lobbyist": sorted(({"lobbyist": k, "reports": v["reports"], "compensation": round(v["compensation"], 2), "clients": len(v["clients"])} for k, v in by_lobbyist.items()), key=lambda x: -x["compensation"])[:300],
        "source": PORTAL,
        "lds": sorted(by_ld.keys()),   # which LD{n}.json files exist, so the site never requests a missing one
    }
    (out / "index.json").write_text(json.dumps(idx, ensure_ascii=False))
    print(f"lobbying: {len(by_ld)} bills with reports, {len(by_client)} clients -> {out}")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "discover"
    {"discover": discover, "index": lambda: index("--csv" in sys.argv), "build": build}[cmd]()
