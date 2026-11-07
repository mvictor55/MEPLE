"""
Testimony: discover the Legislature's data endpoint, index every document per
bill, download the PDFs, extract text, and (optionally) infer positions.

STEP 0 — discover the endpoint (first run only):
    python -m pipeline.testimony discover
  Opens the JS-rendered testimony pages in headless Chromium and logs every
  XHR/fetch request. Paste the endpoint(s) into TESTIMONY_ENDPOINTS below.

STEP 1 — index:
    python -m pipeline.testimony index
  Writes pipeline/raw/testimony/index.json: one row per document with
  name, organization, town, date, hearing date, committee, LD, position (if the
  Legislature records one), and the document URL.

STEP 2 — download + extract:
    python -m pipeline.testimony extract
  Downloads each PDF to pipeline/raw/testimony/pdf/ and extracts text with
  pdftotext (falls back to pdfplumber). Scanned PDFs are flagged has_text=false
  and left for an OCR pass.
"""
import json, re, subprocess, sys
from pathlib import Path
from . import config, fetch
from .normalize import normalize_position, infer_position_from_text

RAW_T = config.RAW / "testimony"
TESTIMONY_ENDPOINTS: list[str] = []   # filled in after `discover`

def discover():
    from playwright.sync_api import sync_playwright
    seen = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.on("request", lambda r: seen.append((r.resource_type, r.method, r.url)) if r.resource_type in ("xhr", "fetch", "document", "script") else None)
        for url in (config.TESTIMONY_BY_CMTE_STUB, config.TESTIMONY_BY_DATE_STUB):
            page.goto(url.format(snum=config.SESSION), wait_until="networkidle")
            page.wait_for_timeout(2500)
            # try selecting the first committee / date option if a select exists
            for sel in page.query_selector_all("select"):
                opts = sel.query_selector_all("option")
                if len(opts) > 1:
                    sel.select_option(index=1); page.wait_for_timeout(2500)
                    break
        b.close()
    RAW_T.mkdir(parents=True, exist_ok=True)
    (RAW_T / "discovered_requests.json").write_text(json.dumps(seen, indent=1))
    for s in seen:
        if s[0] in ("xhr", "fetch"):
            print(*s)
    print(f"\n{len(seen)} requests logged -> {RAW_T/'discovered_requests.json'}")

def index():
    if not TESTIMONY_ENDPOINTS:
        sys.exit("Run `discover` first and fill TESTIMONY_ENDPOINTS in pipeline/testimony.py")
    rows = []
    for ep in TESTIMONY_ENDPOINTS:
        data = fetch.get_json(ep, refresh=True)
        # Shape unknown until discovered; normalize the obvious field names.
        for r in (data if isinstance(data, list) else data.get("data") or data.get("rows") or []):
            rows.append({
                "id": str(r.get("id") or r.get("DocumentId") or r.get("docid")),
                "ld": int(re.sub(r"\D", "", str(r.get("LD") or r.get("ld") or 0)) or 0),
                "name": r.get("Name") or r.get("name") or " ".join(filter(None, [r.get("FirstName"), r.get("LastName")])),
                "organization": r.get("Organization") or r.get("org"),
                "town": r.get("Town") or r.get("City") or r.get("town"),
                "date": r.get("Date") or r.get("SubmittedDate") or r.get("date"),
                "hearing_date": r.get("HearingDate") or r.get("hearing_date"),
                "committee_code": r.get("Committee") or r.get("CommitteeCode"),
                "position": normalize_position(r.get("Position") or r.get("position")),
                "position_source": "filed" if (r.get("Position") or r.get("position")) else None,
                "url": r.get("url") or (config.TESTIMONY_DOC.format(id=r.get("id")) if r.get("id") else None),
            })
    RAW_T.mkdir(parents=True, exist_ok=True)
    (RAW_T / "index.json").write_text(json.dumps(rows, indent=1))
    print(f"{len(rows)} testimony rows -> {RAW_T/'index.json'}")

def _pdf_text(pdf: Path) -> str:
    try:
        out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, timeout=120)
        if out.stdout.strip():
            return out.stdout
    except Exception:
        pass
    try:
        import pdfplumber
        with pdfplumber.open(pdf) as doc:
            return "\n".join((pg.extract_text() or "") for pg in doc.pages)
    except Exception:
        return ""

def clean_text(t: str) -> str:
    t = t.replace("\f", "\n")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

def extract(limit: int | None = None):
    rows = json.loads((RAW_T / "index.json").read_text())
    pdf_dir = RAW_T / "pdf"; pdf_dir.mkdir(parents=True, exist_ok=True)
    txt_dir = RAW_T / "text"; txt_dir.mkdir(parents=True, exist_ok=True)
    done = 0
    for r in rows[:limit] if limit else rows:
        if not r.get("url") or not r.get("id"):
            continue
        tp = txt_dir / f"{r['id']}.txt"
        if tp.exists():
            continue
        try:
            data = fetch.get(r["url"], binary=True)
        except Exception as e:
            r["fetch_error"] = str(e)[:120]; continue
        pp = pdf_dir / f"{r['id']}.pdf"; pp.write_bytes(data)
        text = clean_text(_pdf_text(pp))
        tp.write_text(text)
        r["has_text"] = bool(text)
        if not r.get("position") and text:
            inf = infer_position_from_text(text)
            if inf:
                r["position"], r["position_source"] = inf, "inferred"
        done += 1
    (RAW_T / "index.json").write_text(json.dumps(rows, indent=1))
    print(f"extracted {done} documents")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "discover"
    {"discover": discover, "index": index, "extract": lambda: extract(int(sys.argv[2]) if len(sys.argv) > 2 else None)}[cmd]()
