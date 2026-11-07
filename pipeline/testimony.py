"""
Testimony: index every document the Legislature has posted for the session,
download the PDFs, extract text, and (optionally) infer positions.

Endpoints (found 2026-11-06 in the source of TestimonyByCmte-stub.asp, which
loads them by XHR; all return JSON):
  getTestyCmte.asp?snum=132
      -> [{committeeName, committeeCode, committeeId, dateCount}]
  getTestyCmteDates.asp?snum=132&comCode=EUT
      -> [{tzo, presentedDate: "Thu Feb 6 00:00:00 EST 2025"}]
  getTestyForCmteHearingDate.asp?hearingDate=2025-02-06&comCode=EUT
      -> [{id, firstName, lastName, organization, ld, paperNumber, title,
           sessionNumber, policyArea, topic}]
  getTestimonyDoc.asp?id=NNN  -> the PDF
The Legislature does not record a position. The feed has no town field: its
single "organization" field holds whatever the witness typed, which for
individuals is usually their town ("Portland", "Cape Elizabeth, ME",
"Washington, DC"). split_org_town() moves place names to "town" using the
Legislature's own list of Maine municipalities (rosters.json "towns", from the
House district-towns page) plus a "City, ST" pattern for out-of-state places. A per-bill view also exists
(getTestyRecords.asp?recid=..&date=.., keyed from getPHWS.asp) but the
committee/date walk covers every bill in fewer requests.

    python -m pipeline.testimony discover   # re-log the XHR calls (diagnostic)
    python -m pipeline.testimony index      # -> pipeline/raw/testimony/index.json
    python -m pipeline.testimony extract [N]  # PDFs -> text/*.txt, then positions
    python -m pipeline.testimony positions    # shared documents + bill-specific inferred positions
"""
import hashlib, json, re, sys
from datetime import datetime
from pathlib import Path
from . import config, fetch
from .normalize import infer_position_for_ld
from .pdftext import pdf_to_text

RAW_T = config.RAW / "testimony"
TB = config.BASE + "/legis/bills/"
TESTIMONY_ENDPOINTS = {
    "committees": TB + "getTestyCmte.asp?snum={snum}",
    "dates": TB + "getTestyCmteDates.asp?snum={snum}&comCode={code}",
    "records": TB + "getTestyForCmteHearingDate.asp?hearingDate={date}&comCode={code}",
}

def discover():
    """Diagnostic: load the testimony-by-committee page, expand one committee and
    one date, and log every XHR/fetch request it makes."""
    from playwright.sync_api import sync_playwright
    seen = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.on("request", lambda r: seen.append((r.resource_type, r.method, r.url)) if r.resource_type in ("xhr", "fetch", "document") else None)
        # The *-stub.asp pages are fragments (no jQuery); the landing page embeds them.
        page.goto(config.TESTIMONY_LANDING.format(snum=config.SESSION), wait_until="networkidle")
        for sel in (".committee-link", ".date-item"):
            try:
                page.wait_for_selector(sel, timeout=15000)
                page.click(sel); page.wait_for_load_state("networkidle"); page.wait_for_timeout(1500)
            except Exception as e:
                print(f"could not click {sel}: {str(e)[:100]}", file=sys.stderr)
        b.close()
    RAW_T.mkdir(parents=True, exist_ok=True)
    (RAW_T / "discovered_requests.json").write_text(json.dumps(seen, indent=1), encoding="utf-8")
    for s in seen:
        if s[0] in ("xhr", "fetch"):
            print(*s)
    print(f"\n{len(seen)} requests logged -> {RAW_T/'discovered_requests.json'}")

def _clean(s) -> str | None:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s or None

def _name(r) -> str:
    first, last = _clean(r.get("firstName")), _clean(r.get("lastName"))
    return " ".join(x for x in (first, last) if x) or "Unknown"

def _date(s: str) -> str | None:
    m = re.match(r"\w{3} (\w{3}) +(\d{1,2}) [\d:]+ \w+ (\d{4})", s or "")
    return datetime.strptime(" ".join(m.groups()), "%b %d %Y").date().isoformat() if m else None

_US_STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())
_STATE_NAMES = {"maine", "massachusetts", "new hampshire", "vermont", "new york", "connecticut", "rhode island", "california",
                "florida", "virginia", "maryland", "pennsylvania", "new jersey", "texas", "colorado", "oregon", "washington", "district of columbia"}

def _town_set() -> set[str]:
    p = config.RAW / "rosters" / "rosters.json"
    towns = json.loads(p.read_text(encoding="utf-8")).get("towns", []) if p.exists() else []
    if not towns:
        print("warning: no town list in rosters.json; run `python run.py rosters` first", file=sys.stderr)
    return {t["town"].lower() for t in towns}

# Villages and islands that aren't municipalities but are typed as a home town (seen in the 132nd's feed).
_VILLAGES = {"spruce head", "orr's island", "orrs island", "bailey island", "peaks island", "chebeague island", "new harbor",
             "stratton", "ocean park", "hulls cove", "northeast harbor", "cumberland foreside", "belgrade lakes", "kittery point",
             "south paris", "lisbon falls", "north waterboro", "east waterboro", "west rockport", "south china", "pemaquid",
             "round pond", "west bath", "north yarmouth", "sebago lake", "south freeport", "west kennebunk", "cape porpoise",
             "biddeford pool", "prouts neck", "bustins island", "little deer isle", "tenants harbor", "port clyde", "sunset",
             "east boothbay", "trevett", "ocean point", "south bristol", "walpole", "damariscotta mills", "dover"}
_JUNK = {"n/a", "na", "null", "none", "n.a.", "-", ".", "self", "myself"}
_COMPASS = r"(?:north|south|east|west|northeast|northwest|southeast|southwest|lower|upper)"
_VILLAGE_SUFFIX = r"(?:falls|point|harbor|harbour|lakes?|foreside|center|centre|mills?|village|corner|beach|island|neck|station)"

def _is_place(base: str, towns: set[str]) -> bool:
    b = base.lower().replace("twp.", "township").replace(" twp", " township").strip()
    if b in towns or b in _VILLAGES:
        return True
    m = re.match(rf"^{_COMPASS}\s+(.+)$", b) or re.match(rf"^(.+?)\s+{_VILLAGE_SUFFIX}$", b)
    return bool(m and m.group(1) in towns)

def split_org_town(value: str | None, towns: set[str]) -> tuple[str | None, str | None]:
    """(organization, town) from the feed's single free-text field."""
    v = _clean(value)
    if not v or v.lower() in _JUNK:
        return None, None
    m = re.match(r"^(?P<place>[A-Za-z][A-Za-z .'\-]+?)\s*,?\s+(?P<st>[A-Za-z]{2}|[A-Za-z .]{4,})\.?$", v)
    base = re.sub(r"\s*,?\s*(?:ME|Maine|Me)\.?$", "", v).strip()
    if _is_place(base, towns):
        return None, base.title() if base.isupper() else base           # "Portland", "Cape Elizabeth, ME", "NEWPORT"
    st = (m.group("st") if m else "").strip()
    if m and "," in v and st.upper() != "ME" and st.lower() != "maine" and (st.upper() in _US_STATES or st.lower() in _STATE_NAMES):
        return None, v                                                   # "Washington, DC", "San Diego, CA"
    return v, None

def index():
    snum = config.SESSION
    cmtes = fetch.get_json(TESTIMONY_ENDPOINTS["committees"].format(snum=snum), refresh=True)
    rows, seen = [], set()
    towns = _town_set()
    for c in cmtes:
        code = c["committeeCode"]
        if code not in config.COMMITTEES:
            print(f"warning: testimony committee {code} ({c['committeeName']}) is not in config.COMMITTEES", file=sys.stderr)
        dates = fetch.get_json(TESTIMONY_ENDPOINTS["dates"].format(snum=snum, code=code), refresh=True)
        n0 = len(rows)
        for d in dates:
            day = _date(d.get("presentedDate"))
            if not day:
                continue
            recs = fetch.get_json(TESTIMONY_ENDPOINTS["records"].format(date=day, code=code))
            for r in recs:
                if str(r.get("sessionNumber")) != snum or r.get("id") in seen:
                    continue
                seen.add(r["id"])
                rows.append({
                    "id": str(r["id"]),
                    "ld": int(r.get("ld") or 0) or None,
                    "paper": _clean(r.get("paperNumber")),
                    "name": _name(r),
                    **dict(zip(("organization", "town"), split_org_town(r.get("organization"), towns))),
                    "date": day,
                    "hearing_date": day,
                    "committee_code": code,
                    "topic": _clean(r.get("topic")),
                    "position": None,              # the Legislature does not record one
                    "position_source": None,
                    "url": config.TESTIMONY_DOC.format(id=r["id"]),
                })
        print(f"{code}: {len(dates)} dates, {len(rows) - n0} documents", file=sys.stderr)
    RAW_T.mkdir(parents=True, exist_ok=True)
    old = {}
    if (RAW_T / "index.json").exists():   # keep has_text / inferred positions from earlier extract runs
        old = {r["id"]: r for r in json.loads((RAW_T / "index.json").read_text(encoding="utf-8"))}
    for r in rows:
        o = old.get(r["id"])
        if o:
            for k in ("has_text", "position", "position_source", "fetch_error", "shared_with"):
                if o.get(k) is not None:
                    r[k] = o[k]
    (RAW_T / "index.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"{len(rows)} testimony rows -> {RAW_T/'index.json'}")

def clean_text(t: str) -> str:
    t = t.replace("\f", "\n").replace("\x00", "")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

def _apply_text(r: dict, text: str):
    r["has_text"] = bool(text.strip())

def _doc_key(r: dict, text: str):
    """Same witness, same hearing, same committee, same text -> one document filed under several bills."""
    body = re.sub(r"[^a-z0-9]", "", text.lower())
    return (r["name"].lower(), r.get("hearing_date"), r.get("committee_code"), hashlib.sha1(body.encode()).hexdigest()) if body else None

def positions():
    """Find documents filed under several bills (shared_with) and infer each row's
    position on its own bill. Re-runnable without re-extracting text."""
    idx = RAW_T / "index.json"
    rows = json.loads(idx.read_text(encoding="utf-8"))
    txt_dir = RAW_T / "text"
    texts, groups = {}, {}
    for r in rows:
        tp = txt_dir / f"{r['id']}.txt"
        t = tp.read_text(encoding="utf-8") if tp.exists() else ""
        texts[r["id"]] = t
        k = _doc_key(r, t)
        if k and r.get("ld"):
            groups.setdefault(k, set()).add(r["ld"])
    n = {"support": 0, "oppose": 0, "neutral": 0, None: 0}
    shared = 0
    for r in rows:
        k = _doc_key(r, texts[r["id"]])
        others = sorted(groups.get(k, set()) - {r.get("ld")}) if k else []
        r["shared_with"] = others
        shared += bool(others)
        if r.get("position_source") != "filed":
            inf = infer_position_for_ld(texts[r["id"]], r.get("ld"), others)
            r["position"], r["position_source"] = (inf, "inferred") if inf else (None, None)
        n[r.get("position")] = n.get(r.get("position"), 0) + 1
    idx.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"positions: {n}; {shared} rows are documents also filed under other bills")

def extract(limit: int | None = None):
    idx = RAW_T / "index.json"
    rows = json.loads(idx.read_text(encoding="utf-8"))
    pdf_dir = RAW_T / "pdf"; pdf_dir.mkdir(parents=True, exist_ok=True)
    txt_dir = RAW_T / "text"; txt_dir.mkdir(parents=True, exist_ok=True)
    done = failed = 0
    todo = rows[:limit] if limit else rows
    for i, r in enumerate(todo, 1):
        if not r.get("url") or not r.get("id"):
            continue
        tp = txt_dir / f"{r['id']}.txt"
        if tp.exists():
            _apply_text(r, tp.read_text(encoding="utf-8"))
            continue
        pp = pdf_dir / f"{r['id']}.pdf"
        try:
            if not pp.exists():
                pp.write_bytes(fetch.get(r["url"], binary=True, cache=False))
        except Exception as e:
            r["fetch_error"] = str(e)[:120]; failed += 1; continue
        r.pop("fetch_error", None)
        text = clean_text(pdf_to_text(pp, layout=False))
        tp.write_text(text, encoding="utf-8")
        _apply_text(r, text)
        done += 1
        if done % 250 == 0:
            idx.write_text(json.dumps(rows, indent=1), encoding="utf-8")
            print(f"...{done} extracted ({i}/{len(todo)})", file=sys.stderr, flush=True)
    idx.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"extracted {done} new documents; {failed} failed to download; "
          f"{sum(1 for r in rows if r.get('has_text'))}/{len(rows)} have text")
    positions()

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "discover"
    {"discover": discover, "index": index, "positions": positions, "extract": lambda: extract(int(sys.argv[2]) if len(sys.argv) > 2 else None)}[cmd]()
