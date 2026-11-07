"""
Merge every raw source into the JSON the site reads (site/data/).

Inputs (all optional; the build uses what exists):
  pipeline/raw/legiscan/{bills,people,votes}.json   from pipeline.legiscan
  pipeline/raw/bills/LD*.json                       from pipeline.crawl_bills
  pipeline/raw/billtext/summaries.json              from pipeline.billtext
  pipeline/raw/rosters/rosters.json                 from pipeline.rosters
  pipeline/raw/testimony/index.json + text/*.txt    from pipeline.testimony

Outputs:
  site/data/meta.json, committees.json, legislators.json,
  bills/index.json, bills/LD{ld}.json, testimony/index.json, testimony/LD{ld}.json
"""
import json, re, shutil, sys, unicodedata
from datetime import date
from pathlib import Path
from . import config
from .normalize import normalize_disposition

def _load(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default

def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

def last_name(name: str) -> str:
    name = re.sub(r",?\s+(Jr\.?|Sr\.?|II|III|IV)$", "", name.strip())
    return name.split()[-1].lower()

LEGISCAN_STATUS = {1: ("pending", "Introduced"), 2: ("pending", "Engrossed"), 3: ("pending", "Enrolled"),
                   4: ("enacted", "Passed"), 5: ("vetoed", "Vetoed"), 6: ("died", "Failed")}

def run(sample_notice: str | None = None):
    R = config.RAW
    lg_bills = _load(R / "legiscan" / "bills.json", {})
    lg_people = _load(R / "legiscan" / "people.json", {})
    lg_votes = _load(R / "legiscan" / "votes.json", {})
    summaries = _load(R / "billtext" / "summaries.json", {})
    rosters = _load(R / "rosters" / "rosters.json", {"house": [], "senate": [], "committees": {}})
    t_rows = _load(R / "testimony" / "index.json", [])
    status_pages = {int(p.stem[2:]): json.loads(p.read_text()) for p in (R / "bills").glob("LD*.json")} if (R / "bills").exists() else {}

    # ---- legislators: LegiScan people as the spine; rosters add towns/roles/urls
    legislators, by_key = {}, {}
    for pid, p in lg_people.items():
        lid = f"{p['chamber'][0].lower()}-{slug(p.get('last') or last_name(p['name']))}-{slug(p.get('first') or '')}".rstrip("-")
        legislators[lid] = {"id": lid, "legiscan_id": p["legiscan_id"], "name": p["name"], "party": (p.get("party") or "")[:1] or None,
                            "chamber": p["chamber"], "district": p.get("district"), "towns": [], "town": None, "role": None,
                            "committees": [], "bio": None, "url": None}
        by_key[(p["chamber"], last_name(p["name"]))] = lid
    for m in rosters.get("house", []) + rosters.get("senate", []):
        lid = by_key.get((m["chamber"], last_name(m["name"])))
        if not lid:
            lid = f"{m['chamber'][0].lower()}-{slug(m['name'])}"
            legislators[lid] = {"id": lid, "legiscan_id": None, "name": m["name"], "party": m.get("party"), "chamber": m["chamber"],
                                "district": m.get("district"), "towns": [], "town": None, "role": None, "committees": [], "bio": None, "url": None}
            by_key[(m["chamber"], last_name(m["name"]))] = lid
        L = legislators[lid]
        L["url"] = m.get("url") or L["url"]; L["district"] = L["district"] or m.get("district")
        if m.get("town"): L["town"] = m["town"]; L["towns"] = [m["town"]]
        if m.get("party") and not L["party"]: L["party"] = m["party"]
    pid_to_lid = {str(p["legiscan_id"]): lid for lid, p in legislators.items() if p.get("legiscan_id")}

    # ---- committees
    committees = []
    for code, name in config.COMMITTEES.items():
        r = rosters.get("committees", {}).get(code, {})
        members = []
        for m in r.get("members", []):
            lid = by_key.get((m["chamber"], last_name(m["name"])))
            if lid:
                members.append({"id": lid, "role": m.get("role") or "Member"})
                legislators[lid]["committees"].append({"code": code, "role": m.get("role") or "Member"})
        committees.append({"code": code, "name": name, "chamber": "joint", "members": members, "url": r.get("url"), "bill_count": 0})
    com_by_name = {c["name"].lower(): c["code"] for c in committees}

    # ---- testimony by LD
    t_by_ld = {}
    for t in t_rows:
        t_by_ld.setdefault(t.get("ld"), []).append(t)
    text_dir = R / "testimony" / "text"

    # ---- bills
    lds = sorted(set(int(k) for k in lg_bills.keys()) | set(status_pages.keys()))
    index, out_bills = [], config.OUT / "bills"
    if config.OUT.exists():
        shutil.rmtree(config.OUT)
    (out_bills).mkdir(parents=True); (config.OUT / "testimony").mkdir()
    t_index, t_total = [], 0
    for ld in lds:
        lg = lg_bills.get(str(ld), {}); sp = status_pages.get(ld, {})
        if sp.get("missing") and not lg:
            continue
        title = sp.get("title") or lg.get("title")
        # sponsor
        sponsor_id, cos_ids = None, []
        for s in lg.get("sponsors", []):
            lid = pid_to_lid.get(s["people_id"])
            if not lid: continue
            if s.get("primary") and not sponsor_id: sponsor_id = lid
            else: cos_ids.append(lid)
        if not sponsor_id and sp.get("sponsor"):
            sponsor_id = by_key.get((sp["sponsor"]["chamber"], last_name(sp["sponsor"]["name"])))
            if sponsor_id and sp["sponsor"].get("town") and not legislators[sponsor_id]["town"]:
                legislators[sponsor_id]["town"] = sp["sponsor"]["town"]; legislators[sponsor_id]["towns"] = [sp["sponsor"]["town"]]
        # committee
        code = sp.get("committee_code") or com_by_name.get((lg.get("committee") or "").lower())
        # status
        if sp.get("final_disposition", {}).get("label"):
            status, status_label = sp["status"], sp["status_label"]
        else:
            status, status_label = LEGISCAN_STATUS.get(lg.get("status_code"), ("pending", "Pending"))
        # roll calls
        rolls = []
        for rid in lg.get("roll_call_ids", []):
            v = lg_votes.get(rid)
            if v:
                rolls.append({**v, "votes": [{"id": pid_to_lid.get(x["people_id"]), "vote": x["vote"]} for x in v["votes"]]})
        # summary
        sm = summaries.get(str(ld), {})
        summary = {"text": sm.get("summary") or lg.get("description") or title, "official": bool(sm.get("summary")),
                   "source": ("Bill text, " + sm["url"]) if sm.get("summary") else ("LegiScan description" if lg.get("description") else None)}
        # testimony
        trows = sorted(t_by_ld.get(ld, []), key=lambda t: (t.get("date") or "", t.get("name") or ""))
        texts = {}
        for t in trows:
            tp = text_dir / f"{t['id']}.txt"
            if tp.exists():
                txt = tp.read_text()
                if txt.strip(): texts[t["id"]] = txt
            t["has_text"] = t["id"] in texts
        t_total += len(trows)
        hearing = sp.get("hearing") or next(({"date": c["date"], "time": c.get("time"), "location": c.get("location")} for c in lg.get("calendar", []) if "hearing" in (c.get("type") or c.get("description") or "").lower()), None)
        full = {
            "ld": ld, "paper": sp.get("paper"), "session": config.SESSION, "title": title,
            "sponsor_id": sponsor_id, "cosponsor_ids": cos_ids,
            "committee_code": code, "committee_name": config.COMMITTEES.get(code),
            "summary": summary, "status": status, "status_label": status_label,
            "final_disposition": sp.get("final_disposition") or {"label": status_label, "date": lg.get("status_date")},
            "fiscal_status": sp.get("fiscal_status"),
            "documents": sp.get("documents") or [{"label": t["label"] or "Bill text", "url": t["url"]} for t in lg.get("texts", []) if t.get("url")],
            "amendments": sp.get("amendments", []),
            "committee_actions": sp.get("committee_actions", []),
            "committee_reports": _link_signers(sp.get("committee_reports", []), by_key),
            "chamber_actions": lg.get("chamber_actions", []),
            "roll_calls": rolls, "hearing": hearing,
            "tags": lg.get("subjects", []), "affected_statutes": sp.get("affected_statutes", []),
            "testimony": [{k: t.get(k) for k in ("id", "name", "organization", "town", "position", "position_source", "date", "hearing_date", "url", "has_text")} for t in trows],
            "sources": list(dict.fromkeys((sp.get("sources") or []) + ([f"https://legiscan.com/ME/bill/LD{ld}/2025"] if lg else []))),
            "sample": False,
        }
        (out_bills / f"LD{ld}.json").write_text(json.dumps(full, ensure_ascii=False))
        if texts:
            (config.OUT / "testimony" / f"LD{ld}.json").write_text(json.dumps(texts, ensure_ascii=False))
        index.append({"ld": ld, "paper": full["paper"], "title": title, "sponsor_id": sponsor_id, "cosponsor_ids": cos_ids,
                      "committee_code": code, "status": status, "status_label": status_label,
                      "final_date": full["final_disposition"].get("date"), "hearing_date": (hearing or {}).get("date"),
                      "testimony_count": len(trows), "tags": full["tags"][:6]})
        for t in trows:
            t_index.append({k: t.get(k) for k in ("id", "name", "organization", "town", "position", "position_source", "date", "url", "has_text")} | {"ld": ld, "committee_code": code})
    for c in committees:
        c["bill_count"] = sum(1 for b in index if b["committee_code"] == c["code"])

    (out_bills / "index.json").write_text(json.dumps(index, ensure_ascii=False))
    (config.OUT / "testimony" / "index.json").write_text(json.dumps(t_index, ensure_ascii=False))
    (config.OUT / "legislators.json").write_text(json.dumps(sorted(legislators.values(), key=lambda l: (l["chamber"], last_name(l["name"]))), ensure_ascii=False, indent=0))
    (config.OUT / "committees.json").write_text(json.dumps(committees, ensure_ascii=False, indent=0))
    top = [b["ld"] for b in sorted(index, key=lambda b: -b["testimony_count"])[:3]]
    meta = {"session": config.SESSION, "legislature": config.LEGISLATURE_LABEL, "years": config.YEARS,
            "generated_at": date.today().isoformat(), "sample": bool(sample_notice), "notice": sample_notice,
            "featured": top,
            "counts": {"bills_introduced": len(index), "bills_loaded": len(index), "legislators": len(legislators),
                       "house": sum(1 for l in legislators.values() if l["chamber"] == "House"),
                       "senate": sum(1 for l in legislators.values() if l["chamber"] == "Senate"),
                       "committees": len(committees), "testimony": t_total}}
    (config.OUT / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"built {len(index)} bills, {len(legislators)} legislators, {t_total} testimony rows -> {config.OUT}")

def _link_signers(reports, by_key):
    for r in reports:
        ids = []
        for s in r.get("signers", []):
            m = re.match(r"(Senator|Representative|Sen\.|Rep\.)\s+(.+?)(?:\s*\(.*\))?$", s.strip())
            if m:
                lid = by_key.get(("Senate" if m.group(1).startswith("Sen") else "House", last_name(m.group(2))))
                if lid: ids.append(lid)
        r["signer_ids"] = ids
    return reports

if __name__ == "__main__":
    run()
