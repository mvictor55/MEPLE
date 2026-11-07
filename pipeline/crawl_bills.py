"""
Fetch and parse every bill status page for the session.

The LD list comes from LegiScan if pipeline/raw/legiscan/bills.json exists
(run pipeline.legiscan first); otherwise we walk LD 1..N until the status page
reports "Cannot find requested paper" for 25 LDs in a row.
"""
import json, sys
from . import config
from .bills import fetch_status_page, parse_status_page

def run(start: int = 1, end: int | None = None, refresh=False):
    out = config.RAW / "bills"; out.mkdir(parents=True, exist_ok=True)
    lg = config.RAW / "legiscan" / "bills.json"
    lds = sorted(int(k) for k in json.loads(lg.read_text()).keys()) if lg.exists() else None
    if lds and end:
        lds = [x for x in lds if start <= x <= end]
    misses, ld, n = 0, start, 0
    it = iter(lds) if lds else None
    while True:
        if it:
            try: ld = next(it)
            except StopIteration: break
        elif end and ld > end:
            break
        p = out / f"LD{ld}.json"
        if p.exists() and not refresh:
            ld += 1; continue
        try:
            parsed = parse_status_page(fetch_status_page(ld, refresh=refresh), ld)
        except Exception as e:
            parsed = {"ld": ld, "error": str(e)[:200]}
        p.write_text(json.dumps(parsed, indent=1))
        n += 1
        if parsed.get("missing"):
            misses += 1
            if not it and misses >= 25:
                break
        else:
            misses = 0
        if n % 100 == 0:
            print(f"...{n} pages (LD {ld})", file=sys.stderr)
        ld += 1
    print(f"fetched {n} status pages -> {out}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1); ap.add_argument("--end", type=int)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    run(a.start, a.end, a.refresh)
