"""
LegiScan dataset -> bills, people, roll calls for the session.

Two ways in:
  1. A dataset zip downloaded from https://legiscan.com/ME/datasets (free login),
     passed as --zip. Contents: <state>/<session>/bill/*.json, people/*.json, vote/*.json.
  2. The API (free key, LEGISCAN_KEY env): getDatasetList -> getDataset (base64 zip).

LegiScan is CC BY 4.0: keep the attribution in site/index.html's About page.
What we take from it: the full LD list, official titles, sponsors with person
ids, status history (chamber actions with dates), roll calls with per-member
votes, hearing calendar entries, subjects (used as topic tags), bill text URLs.
What we do NOT take from it: committee-level detail and testimony (not there).
"""
import base64, io, json, os, re, zipfile
from pathlib import Path
from . import config, fetch

def load_zip(path: Path) -> dict:
    z = zipfile.ZipFile(path)
    return _load_members(z)

def _load_members(z: zipfile.ZipFile) -> dict:
    out = {"bill": {}, "people": {}, "vote": {}}
    for name in z.namelist():
        parts = name.split("/")
        if len(parts) < 2 or not name.endswith(".json"):
            continue
        kind = parts[-2]
        if kind in out:
            data = json.loads(z.read(name))
            key = next(iter(data.values())) if len(data) == 1 else data
            out[kind][parts[-1][:-5]] = key
    return out

def legiscan_key() -> str | None:
    """LEGISCAN_KEY from the environment, else from a .env file at the repo root."""
    key = os.environ.get("LEGISCAN_KEY")
    env = config.ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("LEGISCAN_KEY="):
                key = line.split("=", 1)[1].strip().strip("'\"")
    return key or None

def download_via_api(session_name_match: str = "132nd") -> dict:
    key = legiscan_key()
    if not key:
        raise SystemExit("Put LEGISCAN_KEY=... in a .env file at the repo root (free key at https://legiscan.com/legiscan)")
    lst = fetch.get_json(config.LEGISCAN_API.format(key=key, op="getDatasetList") + "&state=ME", refresh=True)
    ds = next(d for d in lst["datasetlist"] if session_name_match in d["session_name"])
    got = fetch.get_json(config.LEGISCAN_API.format(key=key, op="getDataset") + f"&id={ds['session_id']}&access_key={ds['access_key']}", refresh=True)
    raw = base64.b64decode(got["dataset"]["zip"])
    p = config.RAW / "legiscan_132.zip"; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw)
    return _load_members(zipfile.ZipFile(io.BytesIO(raw)))

def ld_number(bill_number: str) -> int | None:
    m = re.match(r"LD\s*(\d+)", bill_number or "", re.I)
    return int(m.group(1)) if m else None

def to_meple(ds: dict) -> tuple[dict, dict, dict]:
    """Return (bills_by_ld, people_by_id, votes_by_rollcall_id) in MEPLE's shape."""
    people = {}
    for pid, p in ds["people"].items():
        people[str(p["people_id"])] = {
            "legiscan_id": p["people_id"], "name": p["name"], "first": p.get("first_name"), "last": p.get("last_name"),
            "party": p.get("party"), "chamber": "Senate" if p.get("role") == "Sen" else "House",
            "district": re.sub(r"^[HS]D-?", "", p.get("district", "") or "") or None,
        }
    bills = {}
    for bid, b in ds["bill"].items():
        ld = ld_number(b.get("bill_number"))
        if not ld:
            continue
        bills[ld] = {
            "ld": ld, "legiscan_id": b["bill_id"], "title": b.get("title"), "description": b.get("description"),
            "status_code": b.get("status"), "status_date": b.get("status_date"),
            "sponsors": [{"people_id": str(s["people_id"]), "name": s["name"], "primary": s.get("sponsor_type_id") == 1} for s in b.get("sponsors", [])],
            "chamber_actions": [{"date": h["date"], "chamber": h.get("chamber"), "action": h["action"]} for h in b.get("history", [])],
            "subjects": [s["subject_name"] for s in b.get("subjects", [])],
            "texts": [{"label": t.get("type"), "date": t.get("date"), "url": t.get("state_link") or t.get("url")} for t in b.get("texts", [])],
            "calendar": [{"date": c.get("date"), "time": c.get("time"), "type": c.get("type"), "description": c.get("description"), "location": c.get("location")} for c in b.get("calendar", [])],
            "roll_call_ids": [str(v["roll_call_id"]) for v in b.get("votes", [])],
            "committee": (b.get("committee") or {}).get("name"),
        }
    votes = {}
    for vid, v in ds["vote"].items():
        votes[str(v["roll_call_id"])] = {
            "id": str(v["roll_call_id"]), "date": v.get("date"), "chamber": v.get("chamber"), "motion": v.get("desc"),
            "yea": v.get("yea"), "nay": v.get("nay"), "nv": v.get("nv"), "absent": v.get("absent"),
            "result": "Passed" if v.get("passed") else "Failed", "url": v.get("state_link") or v.get("url"),
            "votes": [{"people_id": str(x["people_id"]), "vote": x.get("vote_text")} for x in v.get("votes", [])],
        }
    return bills, people, votes

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--zip"); a = ap.parse_args()
    ds = load_zip(Path(a.zip)) if a.zip else download_via_api()
    bills, people, votes = to_meple(ds)
    out = config.RAW / "legiscan"; out.mkdir(parents=True, exist_ok=True)
    (out / "bills.json").write_text(json.dumps(bills, indent=1))
    (out / "people.json").write_text(json.dumps(people, indent=1))
    (out / "votes.json").write_text(json.dumps(votes, indent=1))
    print(f"bills={len(bills)} people={len(people)} roll_calls={len(votes)} -> {out}")
