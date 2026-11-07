"""
Bill status pages -> per-bill JSON.

Source: legislature.maine.gov/legis/bills/display_ps.asp?LD={ld}&snum={snum}
Observed sections (2026-11-06, LD 1822): title block (LD/paper, title, sponsor),
"Text and Disposition" (printed document PDF, fiscal status, final disposition),
"Amendments" (id, sponsor, status, fiscal note links, printed item numbers),
"Status in Committee" (committee, referred date, latest action/report, and a
dated action table: Work Session Held / Voted / Reported Out with results),
"Divided Reports" (Report A/B/C, recommendation, signers), "Sections Affected".

Selectors verified against the live LD 1822 page on 2026-11-06. The page has no
bill-level sponsor line (LegiScan supplies sponsors). Public hearings and work
sessions are loaded by XHR from getPHWS.asp (see attach_hearings()); the
recID in the page's script keys the testimony lookup.
"""
import re, json, sys
from bs4 import BeautifulSoup
from . import config, fetch
from .normalize import normalize_disposition, expand_report

DATE_RE = re.compile(r"([A-Z][a-z]{2} \d{1,2}, \d{4})")

def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""

def _iso(d: str | None) -> str | None:
    """'Apr 13, 2026' -> '2026-04-13'"""
    if not d:
        return None
    from datetime import datetime
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(d.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return d

def fetch_status_page(ld: int, refresh=False) -> str:
    return fetch.get(config.BILL_STATUS.format(ld=ld, snum=config.SESSION), refresh=refresh)

def _abs(href: str | None) -> str | None:
    """Status-page links are relative to /legis/bills/ (e.g. 'getPDF.asp?...')."""
    if not href:
        return None
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return config.BASE + href
    return config.BASE + "/legis/bills/" + href

_TZ = {"EDT": -4, "EST": -5}

def parse_phws_date(s: str):
    """'Mon May 5 10:00:00 EDT 2025' -> aware datetime (Eastern)."""
    from datetime import datetime, timedelta, timezone
    m = re.match(r"\w{3} (\w{3}) +(\d{1,2}) (\d{2}):(\d{2}):\d{2} (E[DS]T) (\d{4})", s or "")
    if not m:
        return None
    mon, day, hh, mm, tz, yr = m.groups()
    d = datetime.strptime(f"{mon} {day} {yr} {hh}:{mm}", "%b %d %Y %H:%M")
    return d.replace(tzinfo=timezone(timedelta(hours=_TZ[tz])))

def fetch_phws(paper: str, refresh=False) -> list[dict]:
    """Public hearings and work sessions for a paper (JSON the status page loads via XHR).
    Each entry carries the `date` value getTestyRecords.asp expects: epoch seconds
    of local midnight on that day, plus the entry's index in this list."""
    raw = fetch.get(config.PHWS.format(paper=paper, snum=config.SESSION), refresh=refresh)
    try:
        rows = json.loads(raw)
    except ValueError:
        return []
    out = []
    for i, r in enumerate(rows):
        dt = parse_phws_date(r.get("hearingDate"))
        midnight = dt.replace(hour=0, minute=0, second=0) if dt else None
        out.append({
            "public_hearing": (r.get("publicHearing") or 0) < 0,
            "date": dt.date().isoformat() if dt else None,
            "time": dt.strftime("%I:%M %p").lstrip("0") if dt else None,
            "location": (r.get("publicHearingLocation") or "").strip() or None,
            "topic": (r.get("topic") or "").strip() or None,
            "testimony_count": r.get("testimonyCount") or 0,
            "testimony_key": int(midnight.timestamp()) + i if midnight else None,
        })
    return out

def parse_status_page(html: str, ld: int) -> dict:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style"]):
        t.extract()
    page = _text(soup)
    b = {"ld": ld, "session": config.SESSION, "sources": [config.BILL_STATUS.format(ld=ld, snum=config.SESSION)]}

    if "Cannot find requested paper" in page:
        b["missing"] = True
        return b

    # Paper number and title ("Paper HP 1220 LD # 1822 An Act to ... Text and Disposition")
    # HP/SP = House/Senate paper; IB = citizen-initiated bill
    m = re.search(r"\bPaper\s+([HS]P|IB)\s?(\d{1,4})\b", page) or re.search(r"\b([HS]P|IB)\s?(\d{1,4})\b", page)
    if not m:   # initiated bills show no "Paper" line; the page script still carries it ("IB0001")
        m = re.search(r'var paper_number_string = "([A-Z]{2})0*(\d+)"', html)
    b["paper"] = (m.group(1) + m.group(2)) if m else None
    m = re.search(r"LD #\s*" + str(ld) + r"\s+(.+?)\s+Text and Disposition", page)
    b["title"] = m.group(1).strip() if m else None
    if not b["title"]:
        h = soup.find(string=re.compile(r"^\s*(An Act|Resolve|Resolution|Joint Resolution|Joint Order|RESOLUTION)", re.I))
        b["title"] = _text(h.parent) if h else None

    # The bill-level sponsor is not on the status page (LegiScan provides it).
    b["sponsor"] = None
    b["cosponsors_raw"] = None

    # Request id: keys the testimony lookup (getTestyRecords.asp?recid=...)
    m = re.search(r'var recID = "(\d+)"', html)
    b["recid"] = m.group(1) if m else None
    # getPHWS.asp needs the zero-padded paper number the page's script uses ("HP0065", not "HP65")
    m = re.search(r'var paper_number_string = "([^"]+)"', html)
    pm = re.match(r"([HS]P|IB)(\d+)$", b["paper"] or "")
    b["paper_query"] = m.group(1) if m else (f"{pm.group(1)}{int(pm.group(2)):04d}" if pm else None)

    # Documents and Disposition block
    sec0 = soup.find(id="sec0") or soup.find("div", class_="data_block")
    fin = soup.select_one("span.tlnk-final")
    # "Enacted, May 29, 2025<br>Governor's Action: Signed, May 30, 2025"
    full = _text(fin) or ""
    disp, _, gov = full.partition("Governor's Action:")
    disp, gov = disp.strip().rstrip(",") or None, gov.strip() or None
    dm = DATE_RE.search(disp or "")
    label = re.sub(r",?\s*" + re.escape(dm.group(1)) + r".*$", "", disp).strip() if dm else disp
    gm = DATE_RE.search(gov or "")
    b["final_disposition"] = {"label": label, "date": _iso(dm.group(1)) if dm else None,
                              "governor_action": re.sub(r",?\s*" + re.escape(gm.group(1)) + r".*$", "", gov).strip() if gm else gov,
                              "governor_action_date": _iso(gm.group(1)) if gm else None}
    b["status"], b["status_label"] = normalize_disposition(label)
    fis = soup.select_one("span.tlnk-fiscal span[style*='nowrap']")
    b["fiscal_status"] = _text(fis) or None

    # Amendments (div#sec1): one span.tlnk-amdblk per amendment
    amendments = []
    sec1 = soup.find(id="sec1")
    for blk in (sec1.select("span.tlnk-amdblk") if sec1 else []):
        sub = blk.select_one("span.story_subhead")
        if not sub:
            continue
        info = _text(blk.select_one("span.infoText"))
        meta = _text(blk.select_one("span.tlnk-amd"))
        sp = re.search(r"Sponsored By\s+(Senator|Representative|Speaker|President)\s+(.+?) of ", meta)
        st = re.search(r"(Adopted by [A-Za-z &]+|Pending|Failed|Indefinitely Postponed|Withdrawn)", meta)
        pdf = blk.select_one("a[href*='getPDF.asp']")
        fn = blk.select_one("a[href*='fiscalpdfs']") or blk.select_one("a[href*='fiscalnotes']")
        amendments.append({
            "id": _text(sub), "description": info or None,
            "sponsor": f"{sp.group(1)} {sp.group(2).strip()}" if sp else None,
            "status": st.group(1).strip() if st else None,
            "url": _abs(pdf["href"]) if pdf else None,
            "fiscal_note_url": _abs(fn["href"]) if fn else None,
        })
    b["amendments"] = amendments

    # Documents: the printed bill plus each amendment, deduped by URL
    docs, seen = [], set()
    first = (sec0 or soup).select_one("a[href*='getPDF.asp']")
    if first:
        u = _abs(first["href"]); seen.add(u)
        it = re.search(r"item=(\d+)", u)
        docs.append({"label": f"Bill text (LD {ld})", "url": u, "item": int(it.group(1)) if it else 1})
    for a in amendments:
        if a["url"] and a["url"] not in seen:
            seen.add(a["url"]); it = re.search(r"item=(\d+)", a["url"])
            docs.append({"label": f"Amendment {a['id']}", "url": a["url"], "item": int(it.group(1)) if it else None})
    b["documents"] = docs

    # Committee block (div#sec3)
    sec3 = soup.find(id="sec3")
    s3 = _text(sec3)
    # "Referred to Committee on X on <date>" or, for committee bills, "Originated from Committee on X on <date>"
    m = re.search(r"(?:Referred to|Originated from) (?:the )?(?:Committee on )?(.+?) on " + DATE_RE.pattern, s3)
    b["committee_name"] = m.group(1).strip() if m else None
    b["referred_date"] = _iso(m.group(2)) if m else None
    code = None
    lk = sec3.select_one("a[href*='phwksched']") if sec3 else None
    if lk:
        cm = re.search(r"CODE=([A-Z]+)", lk["href"]); code = cm.group(1) if cm else None
    if not code:
        cm = re.search(r"Latest Committee Report:.*?;\s*([A-Z]{2,5}),", s3); code = cm.group(1) if cm else None
    if not code and b["committee_name"]:
        code = next((k for k, v in config.COMMITTEES.items() if v.lower() == b["committee_name"].lower()), None)
    b["committee_code"] = code
    m = re.search(r"Latest Committee Action:\s*(.+?)(?:\s+Latest Committee Report:|\s+Committee Docket|$)", s3)
    b["latest_committee_action"] = m.group(1).strip() if m else None

    # Committee docket table: Date | Action | Result
    actions = []
    tab = sec3.find("table", attrs={"name": "CDtab"}) if sec3 else None
    for row in (tab.find_all("tr") if tab else []):
        cells = [_text(td) for td in row.find_all("td")]
        if len(cells) >= 2 and DATE_RE.fullmatch(cells[0] or ""):
            actions.append({"date": _iso(cells[0]), "action": cells[1], "result": (cells[2] if len(cells) > 2 else "") or None})
    b["committee_actions"] = actions

    # Divided reports (table.reportsTable). The markup leaves <tr>s unclosed, so
    # walk the cells in order: a non-blank valign=top cell starts a new report.
    reports, cur = [], None
    rt = soup.select_one("table.reportsTable")
    for td in (rt.find_all("td") if rt else []):
        own = "".join(s for s in td.find_all(string=True, recursive=False))
        t = re.sub(r"\s+", " ", own).strip()
        if td.get("valign") == "top":
            if t:
                # "A - Ought To Pass As Amended" (divided) or "MAJ - ..." / "MIN - ..." (majority/minority)
                lm = re.match(r"([A-Z]{1,3})\s*-\s*(.+)", t)
                lab = {"MAJ": "Majority report", "MIN": "Minority report"}.get(lm.group(1), f"Report {lm.group(1)}") if lm else t
                cur = {"label": lab, "recommendation": lm.group(2).strip() if lm else None,
                       "signer_count": 0, "signers": [], "signer_ids": []}
                reports.append(cur)
            continue
        if cur is None or not t:
            continue
        # "Senator Anne Carney of Cumberland, Chair" -> "Senator Anne Carney (Chair)"
        sm = re.match(r"(Senator|Representative)\s+(.+?) of ([^,]+?)(?:,\s*(.+))?$", t)
        cur["signers"].append(f"{sm.group(1)} {sm.group(2).strip()}" + (f" ({sm.group(4).strip()})" if sm and sm.group(4) else "") if sm else t)
        cur["signer_count"] = len(cur["signers"])
    if not reports:
        # Unanimous report: no signer table; read "Latest Committee Report: Jun 13, 2025; JUD, Ought To Pass" or the docket
        m = re.search(r"Latest Committee Report:\s*" + DATE_RE.pattern + r";\s*[A-Z]{2,5},\s*(.+?)(?:\s+Loading|\s+Committee Docket|$)", s3)
        rec = m.group(2).strip() if m else None
        if not rec:
            latest = next((a for a in reversed(actions) if a["action"].lower().startswith("reported out") and a.get("result")), None)
            rec = expand_report(latest["result"].split("/")[0]) if latest else None
        if rec:
            reports.append({"label": "Committee report", "recommendation": rec, "signer_count": None, "signers": [], "signer_ids": []})
    b["committee_reports"] = reports

    # Affected statutes (table TStab: Title | Section | Subsection | Paragraph | Effect | Law Type | Chapter); dedupe
    seen, affected = set(), []
    ts = soup.find("table", attrs={"name": "TStab"})
    for row in (ts.find_all("tr") if ts else []):
        cells = [_text(td) for td in row.find_all("td")]
        if len(cells) >= 5 and cells[0]:
            key = (cells[0], cells[1], cells[2], cells[3])
            if key in seen:
                continue
            seen.add(key)
            affected.append({"title": cells[0], "section": cells[1] or None, "subsection": cells[2] or None,
                             "paragraph": cells[3] or None, "effect": cells[4] or None})
    b["affected_statutes"] = affected[:200]

    b["hearing"] = None   # filled from getPHWS.asp by attach_hearings()
    return b

def attach_hearings(b: dict, refresh=False) -> dict:
    """Add public hearings / work sessions (and the testimony lookup keys) from getPHWS.asp."""
    if b.get("missing") or not b.get("paper"):
        return b
    try:
        ph = fetch_phws(b.get("paper_query") or b["paper"], refresh=refresh)
    except Exception as e:
        b["phws_error"] = str(e)[:120]; return b
    b["hearings"] = ph
    h = next((x for x in ph if x["public_hearing"]), None)
    if h:
        b["hearing"] = {"date": h["date"], "time": h["time"], "location": h["location"],
                        "committee_code": b.get("committee_code"), "testimony_count": h["testimony_count"]}
    return b

def extract_summary_from_bill_text(pdf_text: str) -> str | None:
    """Maine bills end with a 'SUMMARY' section written by the Revisor's Office.
    Printed bills carry a line number at the start of each line and a
    'Page N - 132LR1521(01)' footer on each page; both are stripped. Lines are
    rejoined into paragraphs: a line ending in a period that is clearly shorter
    than a full line ends a paragraph."""
    heads = list(re.finditer(r"^\s*(?:\d+\s+)?SUMMARY\s*$", pdf_text, re.M))
    if not heads:
        return None
    body = pdf_text[heads[-1].end():]
    body = re.split(r"^\s*FISCAL NOTE", body, flags=re.M)[0]
    lines = []
    for l in body.replace("\f", "\n").splitlines():
        l = l.strip()
        if not l or re.match(r"^Page \d+ - \d+LR\d+\(\d+\)", l) or re.fullmatch(r"\d+", l):
            continue
        lines.append(re.sub(r"^\d{1,3}\s+", "", l, count=1))    # one leading line number
    if not lines:
        return None
    full = max(len(l) for l in lines)
    paras, cur = [], []
    for l in lines:
        cur.append(l)
        if l.endswith((".", ":")) and len(l) < 0.85 * full:
            paras.append(" ".join(cur)); cur = []
    if cur:
        paras.append(" ".join(cur))
    s = "\n\n".join(re.sub(r"\s{2,}", " ", p).strip() for p in paras)
    s = re.sub(r"\s*\b\d*LR ?\d+\(\d+\)\s*", " ", s).strip()   # drafting reference numbers
    return s or None

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ld", type=int, required=True)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    html = fetch_status_page(a.ld, refresh=a.refresh)
    parsed = attach_hearings(parse_status_page(html, a.ld), refresh=a.refresh)
    print(json.dumps(parsed, indent=1))
    if a.debug:
        d = config.RAW / "debug"; d.mkdir(parents=True, exist_ok=True)
        (d / f"LD{a.ld}.html").write_text(html)
        (d / f"LD{a.ld}.json").write_text(json.dumps(parsed, indent=1))
        print(f"wrote {d}/LD{a.ld}.html and .json", file=sys.stderr)
