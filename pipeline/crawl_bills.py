"""
Fetch and parse every bill status page for the session.

The LD list is the union of LegiScan's LDs (pipeline/raw/legiscan/bills.json, if
present) and every LD from 1 to the highest LD listed in the Legislature's own
bill directory (billdirectory_ps.asp, 200 LDs per page), so a bill LegiScan
lacks is still fetched. If the directory can't be read, we walk LD 1..N until
25 status pages in a row report "Cannot find requested paper".
"""
import json, re, sys
from . import config, fetch
from .bills import fetch_status_page, parse_status_page, attach_hearings

def directory_max_ld() -> int | None:
    """Highest LD in the bill directory. Pages past the last LD say
    'Legislative documents from N to M are not available at this time.'"""
    best, start = None, 1
    while start < 10000:
        html = fetch.get(config.BILL_DIRECTORY.format(snum=config.SESSION, ld_from=start), refresh=True)
        found = [int(x) for x in re.findall(r"\bLD (\d{1,4}), [HS]P ?\d+", html)]
        if not found:
            break
        best = max(found + [best or 0])
        start += 200
    return best

def run(start: int = 1, end: int | None = None, refresh=False):
    out = config.RAW / "bills"; out.mkdir(parents=True, exist_ok=True)
    lg = config.RAW / "legiscan" / "bills.json"
    lg_lds = {int(k) for k in json.loads(lg.read_text(encoding="utf-8")).keys()} if lg.exists() else set()
    try:
        top = directory_max_ld()
    except Exception as e:
        print(f"bill directory unreadable ({str(e)[:80]}); falling back", file=sys.stderr); top = None
    if top:
        extra = set(range(1, top + 1)) - lg_lds
        print(f"bill directory: highest LD {top}; LegiScan has {len(lg_lds)}; {len(extra)} LDs only on the Legislature's site"
              + (f": {sorted(extra)[:20]}" if extra else ""), file=sys.stderr)
        lds = sorted(lg_lds | set(range(1, top + 1)))
    else:
        lds = sorted(lg_lds) or None
    if lds:
        lds = [x for x in lds if x >= start and (not end or x <= end)]
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
            parsed = attach_hearings(parse_status_page(fetch_status_page(ld, refresh=refresh), ld), refresh=refresh)
        except Exception as e:
            parsed = {"ld": ld, "error": str(e)[:200]}
        p.write_text(json.dumps(parsed, indent=1), encoding="utf-8")
        n += 1
        if parsed.get("missing"):
            misses += 1
            if not it and misses >= 25:
                break
        else:
            misses = 0
        if n % 100 == 0:
            print(f"...{n} pages (LD {ld})", file=sys.stderr, flush=True)
        ld += 1
    print(f"fetched {n} status pages -> {out}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1); ap.add_argument("--end", type=int)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    run(a.start, a.end, a.refresh)
