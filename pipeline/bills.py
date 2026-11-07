"""
Bill status pages -> per-bill JSON.

Source: legislature.maine.gov/legis/bills/display_ps.asp?LD={ld}&snum={snum}
Observed sections (2026-11-06, LD 1822): title block (LD/paper, title, sponsor),
"Text and Disposition" (printed document PDF, fiscal status, final disposition),
"Amendments" (id, sponsor, status, fiscal note links, printed item numbers),
"Status in Committee" (committee, referred date, latest action/report, and a
dated action table: Work Session Held / Voted / Reported Out with results),
"Divided Reports" (Report A/B/C, recommendation, signers), "Sections Affected".

The parser below is written against that description, not against saved HTML.
Expect to adjust selectors on first run: run `python -m pipeline.bills --ld 1822
--debug` to dump the parsed result next to the raw HTML.
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

def parse_status_page(html: str, ld: int) -> dict:
    soup = BeautifulSoup(html, "lxml")
    page = _text(soup)
    b = {"ld": ld, "session": config.SESSION, "sources": [config.BILL_STATUS.format(ld=ld, snum=config.SESSION)]}

    if "Cannot find requested paper" in page:
        b["missing"] = True
        return b

    # Paper number and title
    m = re.search(r"\b([HS]P\s?\d{3,4})\b", page)
    b["paper"] = m.group(1).replace(" ", "") if m else None
    h = soup.find(string=re.compile(r"^(An Act|Resolve|Resolution|Joint Resolution|Joint Order|Senate Order|House Order|RESOLUTION)", re.I))
    b["title"] = _text(h.parent) if h and h.parent else None
    if not b["title"]:
        m = re.search(r"((?:An Act|Resolve,|RESOLUTION,|Joint Resolution)[^\n]{10,400}?)(?: Sponsored by| Presented by| Text and Disposition)", page)
        b["title"] = m.group(1).strip() if m else None

    # Sponsor: "Sponsored by Representative Amy Kuhn of Falmouth" / "Presented by Senator ..."
    m = re.search(r"(?:Sponsored|Presented) by (Representative|Senator|Speaker|President) ([A-Z][^\n]+?) of ([A-Z][A-Za-z .'-]+?)(?:\.|,|\s{2}| Cosponsored| Text)", page)
    if m:
        b["sponsor"] = {"chamber": "Senate" if m.group(1) in ("Senator", "President") else "House", "name": m.group(2).strip(), "town": m.group(3).strip()}
    cos = re.search(r"Cosponsored by (.+?)(?:\.\s|Text and Disposition)", page)
    b["cosponsors_raw"] = cos.group(1).strip() if cos else None

    # Final disposition and fiscal status
    m = re.search(r"Final Disposition:?\s*(.+?)(?:\s{2}|Amendments|Fiscal|$)", page)
    disp = m.group(1).strip() if m else None
    dm = DATE_RE.search(disp or "")
    b["final_disposition"] = {"label": re.sub(r",?\s*" + re.escape(dm.group(1)), "", disp).strip() if dm else disp, "date": _iso(dm.group(1)) if dm else None}
    b["status"], b["status_label"] = normalize_disposition(disp)
    m = re.search(r"Fiscal (?:Status|Note)?:?\s*(Not Yet Determined|[^\s].{0,80}?)(?:\s{2}|Final Disposition)", page)
    b["fiscal_status"] = m.group(1).strip() if m else None

    # Documents: every getPDF link with its label
    docs, amendments = [], []
    for a in soup.select("a[href*='getPDF.asp']"):
        href = a["href"]
        if href.startswith("/"):
            href = config.BASE + href
        item = re.search(r"item=(\d+)", href)
        label = _text(a) or "Printed document"
        docs.append({"label": label, "url": href, "item": int(item.group(1)) if item else None})
    b["documents"] = docs

    # Amendments: rows mentioning C-A (H-716), H-A to C-A (H-788), S-A (S-528)...
    for row in soup.find_all("tr"):
        t = _text(row)
        am = re.match(r"^((?:[CHS]-[A-Z]{1,2})(?: to [CHS]-[A-Z]{1,2})?\s*\([HS]-\d+\))\s*(.*)$", t)
        if am:
            rest = am.group(2)
            sp = re.search(r"(?:Sponsored by|Sponsor:)\s*(Senator|Representative)\s+([A-Z][^,]+?)(?:,| of )", rest)
            link = row.select_one("a[href*='getPDF.asp']")
            fn = row.select_one("a[href*='fiscalpdfs']")
            amendments.append({
                "id": am.group(1),
                "sponsor": f"{sp.group(1)} {sp.group(2).strip()}" if sp else None,
                "status": "Adopted" if re.search(r"\badopted\b", rest, re.I) else ("Pending" if re.search(r"pending", rest, re.I) else None),
                "url": (config.BASE + link["href"]) if link and link["href"].startswith("/") else (link["href"] if link else None),
                "fiscal_note_url": (config.BASE + fn["href"]) if fn and fn["href"].startswith("/") else (fn["href"] if fn else None),
                "raw": rest[:200],
            })
    b["amendments"] = amendments

    # Committee block
    m = re.search(r"Committee:?\s*([A-Z][A-Za-z,&' ]+?)\s*\(([A-Z]{2,5})\)", page)
    if m:
        b["committee_name"], b["committee_code"] = m.group(1).strip(), m.group(2)
    else:
        b["committee_name"] = b["committee_code"] = None
    m = re.search(r"Referred:?\s*" + DATE_RE.pattern, page)
    b["referred_date"] = _iso(m.group(1)) if m else None

    # Committee action history table: Date | Action | Result
    actions = []
    for row in soup.find_all("tr"):
        cells = [_text(td) for td in row.find_all(["td", "th"])]
        if len(cells) >= 2 and DATE_RE.fullmatch(cells[0] or "") and re.search(r"Work Session|Public Hearing|Voted|Reported Out|Hearing Held|Carry Over|Tabled", cells[1], re.I):
            actions.append({"date": _iso(cells[0]), "action": cells[1], "result": cells[2] if len(cells) > 2 and cells[2] else None})
    b["committee_actions"] = actions
    hearing = next((a for a in actions if re.search(r"Public Hearing", a["action"], re.I)), None)
    b["hearing"] = {"date": hearing["date"], "committee_code": b["committee_code"]} if hearing else None

    # Divided reports: "Report A, Ought To Pass As Amended (8 signers): Senator Anne Carney (Chair), Representative ..."
    reports = []
    for m in re.finditer(r"Report ([A-Z]),?\s*(Ought (?:Not )?To Pass(?: As Amended| In New Draft)?|[A-Za-z ]+?)\s*\((\d+) signers?\):?\s*(.+?)(?=Report [A-Z],|Committee docket|Affected|Titles|$)", page, re.S):
        signers = [s.strip() for s in re.split(r",\s*(?:and\s+)?|\s+and\s+", m.group(4)) if s.strip()]
        reports.append({"label": f"Report {m.group(1)}", "recommendation": m.group(2).strip(), "signer_count": int(m.group(3)), "signers": signers, "signer_ids": []})
    if not reports:
        # Unanimous report: "Reported Out ... OTP-AM" with no divided reports
        latest = next((a for a in reversed(actions) if a["action"].lower().startswith("reported out") and a.get("result")), None)
        if latest:
            reports.append({"label": "Committee report", "recommendation": expand_report(latest["result"].split("/")[0]), "signer_count": None, "signers": [], "signer_ids": []})
    b["committee_reports"] = reports

    # Affected statutes (dedupe; the page repeats rows)
    seen, affected = set(), []
    for m in re.finditer(r"Title (\d+[A-Z]?),?\s*(?:Section|§)\s*([\d\-A-Z.]+)", page):
        key = (m.group(1), m.group(2))
        if key not in seen:
            seen.add(key); affected.append({"title": m.group(1), "section": m.group(2)})
    b["affected_statutes"] = affected[:200]
    return b

def extract_summary_from_bill_text(pdf_text: str) -> str | None:
    """Maine bills end with a 'SUMMARY' section written by the Revisor's Office."""
    m = re.search(r"\n\s*SUMMARY\s*\n(.+?)(?:\n\s*(?:FISCAL NOTE|Page \d+|$))", pdf_text, re.S | re.I)
    if not m:
        return None
    s = re.sub(r"[ \t]+", " ", m.group(1)).strip()
    s = re.sub(r"\n(?!\n)", " ", s)      # unwrap lines
    s = re.sub(r" {2,}", " ", s)
    s = re.sub(r"\n{2,}", "\n\n", s)
    s = re.sub(r"\s*LR \d+\(\d+\)\s*", "", s)   # drafting reference numbers
    return s.strip() or None

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ld", type=int, required=True)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    html = fetch_status_page(a.ld, refresh=a.refresh)
    parsed = parse_status_page(html, a.ld)
    print(json.dumps(parsed, indent=1))
    if a.debug:
        d = config.RAW / "debug"; d.mkdir(parents=True, exist_ok=True)
        (d / f"LD{a.ld}.html").write_text(html)
        (d / f"LD{a.ld}.json").write_text(json.dumps(parsed, indent=1))
        print(f"wrote {d}/LD{a.ld}.html and .json", file=sys.stderr)
