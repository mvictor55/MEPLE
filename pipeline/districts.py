"""
District pages and the slim testimony archive index, from the full testimony index.

Inputs:  pipeline/raw/testimony_index_full.json (written by pipeline.build, profile ids added by pipeline.profiles)
         site/data/legislators.json, site/data/towns.json
Outputs: site/data/districts/{hd-N|sd-N}.json   one per district, so a district page never loads the archive index
         site/data/testimony/index.json           the archive index in a short form the site expands on load:
           i id · n name · o organization · t town · p position (s/n/o) · d date · l LD · c committee
           pp person profile id · op organization profile id · x 0 when the letter has no extracted text
           Omitted keys are null. Every position in the index is inferred from the text, so there is no
           position_source key; the official PDF URL is derived from the id.

    python -m pipeline.districts
"""
import json
from collections import defaultdict
from . import config

FULL = config.RAW / "testimony_index_full.json"
URL = "https://legislature.maine.gov/legis/bills/getTestimonyDoc.asp?id="
POS = {"support": "s", "neutral": "n", "oppose": "o"}

def slim(rows):
    out = []
    for r in rows:
        assert r.get("url") in (None, URL + str(r["id"])), r.get("url")
        assert r.get("position_source") in (None, "inferred"), r.get("position_source")
        s = {"i": r["id"], "n": r.get("name"), "o": r.get("organization"), "t": r.get("town"),
             "p": POS.get(r.get("position")), "d": r.get("date"), "l": r["ld"], "c": r.get("committee_code"),
             "pp": r.get("person_profile_id"), "op": r.get("org_profile_id"), "x": 0 if not r.get("has_text") else None}
        out.append({k: v for k, v in s.items() if v is not None})
    return out

def district_towns(chamber, n, legislators, towns):
    if chamber == "Senate":
        s = next((l for l in legislators if l["chamber"] == "Senate" and str(l.get("district")) == n and l.get("current") is not False), None)
        if s and s.get("towns"): return s["towns"]
        return [t["town"] for t in towns if n in (t.get("senate") or [])]
    return [t["town"] for t in towns if n in (t.get("house") or [])]

def run():
    if not FULL.exists():
        print("districts: no pipeline/raw/testimony_index_full.json (run pipeline.build first); skipping"); return
    rows = json.loads(FULL.read_text(encoding="utf-8"))
    legislators = json.loads((config.OUT / "legislators.json").read_text(encoding="utf-8"))
    towns = json.loads((config.OUT / "towns.json").read_text(encoding="utf-8"))
    by_town = defaultdict(list)
    for r in rows:
        if r.get("town"): by_town[r["town"].lower()].append(r)   # exact match, as the archive town filter does
    out = config.OUT / "districts"; out.mkdir(parents=True, exist_ok=True)
    for p in out.glob("*.json"): p.unlink()
    n_written = 0
    for chamber, prefix, count in (("House", "hd", 151), ("Senate", "sd", 35)):
        for i in range(1, count + 1):
            n = str(i)
            tw = district_towns(chamber, n, legislators, towns)
            tt = [r for t in {x.lower() for x in tw} for r in by_town.get(t, [])]
            com, bills = defaultdict(int), {}
            for r in tt:
                com[r["committee_code"]] += 1
                e = bills.setdefault(r["ld"], {"ld": r["ld"], "n": 0, "c": {"support": 0, "neutral": 0, "oppose": 0}})
                e["n"] += 1
                if r.get("position") in e["c"]: e["c"][r["position"]] += 1
            (out / f"{prefix}-{n}.json").write_text(json.dumps({
                "key": f"{prefix}-{n}", "chamber": chamber, "n": n, "towns": tw,
                "testimonies": len(tt), "names": len({r.get("name") for r in tt}), "bills": len(bills),
                "committees": sorted(com.items(), key=lambda kv: (-kv[1], kv[0])),
                "top_bills": sorted(bills.values(), key=lambda b: (-b["n"], b["ld"]))[:10],
            }, ensure_ascii=False), encoding="utf-8")
            n_written += 1
    full_size = len(json.dumps(rows, ensure_ascii=False).encode("utf-8"))
    s = json.dumps(slim(rows), ensure_ascii=False, separators=(",", ":"))
    (config.OUT / "testimony" / "index.json").write_text(s, encoding="utf-8")
    print(f"districts: {n_written} district files; testimony/index.json {full_size/1024:,.0f} KB -> {len(s.encode('utf-8'))/1024:,.0f} KB ({len(rows)} rows)")

if __name__ == "__main__":
    run()
