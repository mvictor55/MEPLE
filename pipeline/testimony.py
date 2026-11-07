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
The Legislature does not record a position; "organization" is free text that
often holds the person's town. A per-bill view also exists
(getTestyRecords.asp?recid=..&date=.., keyed from getPHWS.asp) but the
committee/date walk covers every bill in fewer requests.

    python -m pipeline.testimony discover   # re-log the XHR calls (diagnostic)
    python -m pipeline.testimony index      # -> pipeline/raw/testimony/index.json
    python -m pipeline.testimony extract [N]  # PDFs -> text/*.txt, inferred positions
"""
import json, re, sys
from datetime import datetime
from pathlib import Path
from . import config, fetch
from .normalize import infer_position_from_text
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

def index():
    snum = config.SESSION
    cmtes = fetch.get_json(TESTIMONY_ENDPOINTS["committees"].format(snum=snum), refresh=True)
    rows, seen = [], set()
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
                    "organization": _clean(r.get("organization")),
                    "town": None,
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
            for k in ("has_text", "position", "position_source", "fetch_error"):
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
    if r.get("position_source") != "filed":
        inf = infer_position_from_text(text) if text else None
        r["position"], r["position_source"] = (inf, "inferred") if inf else (None, None)

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

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "discover"
    {"discover": discover, "index": index, "extract": lambda: extract(int(sys.argv[2]) if len(sys.argv) > 2 else None)}[cmd]()
