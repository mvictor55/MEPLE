"""
Download each bill's printed text (item=1) and pull out the official SUMMARY
section, which the Office of the Revisor of Statutes writes for every bill.
Output: pipeline/raw/billtext/LD{ld}.txt (full text) and summaries.json.
"""
import json, subprocess, sys
from pathlib import Path
from . import config, fetch
from .bills import extract_summary_from_bill_text

RAW_B = config.RAW / "billtext"

def run(limit: int | None = None):
    status_dir = config.RAW / "bills"
    RAW_B.mkdir(parents=True, exist_ok=True)
    summaries = json.loads((RAW_B / "summaries.json").read_text()) if (RAW_B / "summaries.json").exists() else {}
    files = sorted(status_dir.glob("LD*.json"), key=lambda p: int(p.stem[2:]))
    n = 0
    for p in files[:limit] if limit else files:
        b = json.loads(p.read_text())
        if b.get("missing") or str(b["ld"]) in summaries:
            continue
        doc = next((d for d in b.get("documents", []) if d.get("item") == 1), None) or (b.get("documents") or [None])[0]
        if not doc:
            continue
        try:
            pdf = fetch.get(doc["url"], binary=True)
        except Exception as e:
            summaries[str(b["ld"])] = {"error": str(e)[:100]}; continue
        pp = RAW_B / f"LD{b['ld']}.pdf"; pp.write_bytes(pdf)
        out = subprocess.run(["pdftotext", "-layout", str(pp), "-"], capture_output=True, text=True, timeout=120)
        text = out.stdout
        (RAW_B / f"LD{b['ld']}.txt").write_text(text)
        pp.unlink(missing_ok=True)
        summaries[str(b["ld"])] = {"summary": extract_summary_from_bill_text(text), "url": doc["url"]}
        n += 1
        if n % 50 == 0:
            (RAW_B / "summaries.json").write_text(json.dumps(summaries, indent=1)); print(n, file=sys.stderr)
    (RAW_B / "summaries.json").write_text(json.dumps(summaries, indent=1))
    ok = sum(1 for v in summaries.values() if v.get("summary"))
    print(f"summaries: {ok} extracted of {len(summaries)} bills processed")

if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else None)
