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
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

def _force_remove(func, path, exc):
    """OneDrive marks synced folders read-only; clear the flag and retry."""
    import os, stat
    os.chmod(path, stat.S_IWRITE)
    func(path)

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
    status_pages = {int(p.stem[2:]): json.loads(p.read_text(encoding="utf-8")) for p in (R / "bills").glob("LD*.json")} if (R / "bills").exists() else {}

    # ---- legislators: LegiScan people as the spine; rosters add towns/roles/urls/committees.
    # Join on (chamber, district) when the last names agree (or one contains the other:
    # "Roberts" / "Roberts-Lovell"); a different name in the same district is a
    # replacement member and gets their own record.
    def _dist(d):
        d = str(d or "").strip()
        return str(int(d)) if d.isdigit() else (d or None)
    def _same_person(a, b):
        a, b = last_name(a), last_name(b)
        return a == b or a in b or b in a
    legislators, by_key, by_district = {}, {}, {}
    for pid, p in lg_people.items():
        lid = f"{p['chamber'][0].lower()}-{slug(p.get('last') or last_name(p['name']))}-{slug(p.get('first') or '')}".rstrip("-")
        legislators[lid] = {"id": lid, "legiscan_id": p["legiscan_id"], "name": p["name"], "party": (p.get("party") or "")[:1] or None,
                            "chamber": p["chamber"], "district": _dist(p.get("district")), "towns": [], "town": None, "role": None,
                            "committees": [], "bio": None, "url": None, "current": False}
        by_district.setdefault((p["chamber"], _dist(p.get("district"))), []).append(lid)
    roster_lids = []
    for m in rosters.get("house", []) + rosters.get("senate", []):
        cands = [l for l in by_district.get((m["chamber"], _dist(m.get("district"))), []) if _same_person(legislators[l]["name"], m["name"])]
        if not cands:   # fall back to a unique last-name match in the chamber
            cands = [l for l, L in legislators.items() if L["chamber"] == m["chamber"] and L.get("legiscan_id") and last_name(L["name"]) == last_name(m["name"])]
            cands = cands if len(cands) == 1 else []
        if cands:
            lid = cands[0]
        else:
            lid = f"{m['chamber'][0].lower()}-{slug(m['name'])}"
            legislators[lid] = {"id": lid, "legiscan_id": None, "name": m["name"], "party": m.get("party"), "chamber": m["chamber"],
                                "district": _dist(m.get("district")), "towns": [], "town": None, "role": None, "committees": [], "bio": None, "url": None}
        L = legislators[lid]
        L["current"] = True
        L["url"] = m.get("url") or L["url"]; L["district"] = _dist(m.get("district")) or L["district"]
        L["role"] = m.get("role") or L["role"]
        if m["chamber"] == "House" and m.get("town"):
            L["town"] = m["town"]; L["towns"] = [m["town"]]
        elif m["chamber"] == "Senate":
            # Maine styles senators by county ("Senator Carney of Cumberland"); keep the district's towns for search
            L["town"] = m.get("county") or L["town"]; L["towns"] = m.get("towns") or L["towns"]
        if m.get("party") and not L["party"]: L["party"] = m["party"]
        for c in m.get("committees", []):
            L["committees"].append({"code": c["code"], "role": c.get("role") or "Member"})
        roster_lids.append(lid)
    # Members who left during the session: the House list page marks them Resigned/Deceased;
    # their replacement (if one has been elected) is the current member for the same district.
    for f in rosters.get("former", []):
        cands = [l for l in by_district.get((f["chamber"], _dist(f.get("district"))), [])
                 if _same_person(legislators[l]["name"], f["name"]) and not legislators[l].get("current")]
        if not cands:
            print(f"warning: former member {f['name']} not found in LegiScan people", file=sys.stderr)
            continue
        L = legislators[cands[0]]
        succ = [l for l in roster_lids if legislators[l]["chamber"] == f["chamber"] and legislators[l]["district"] == _dist(f.get("district"))]
        L.update({"served_until": f.get("served_until"), "left_reason": {"deceased": "died"}.get(f["status"], f["status"]),
                  "replaced_by": succ[0] if succ else None, "url": f.get("url") or L["url"],
                  "town": L["town"] or f.get("town")})
        if not L["towns"] and f.get("town"): L["towns"] = [f["town"]]
    for lid, L in legislators.items():
        if not L.get("current") and "served_until" not in L:
            print(f"warning: {L['name']} ({L['chamber']} {L['district']}) is in LegiScan but on no current roster", file=sys.stderr)
    # Name -> id for sources that only give a name (report signers, status-page sponsors).
    # Current members win a last-name collision; ambiguous names (two current members) are left out.
    counts = {}
    for lid, L in legislators.items():
        if L.get("current"):
            counts[(L["chamber"], last_name(L["name"]))] = counts.get((L["chamber"], last_name(L["name"])), 0) + 1
    for lid, L in sorted(legislators.items(), key=lambda kv: kv[1].get("current", False)):
        k = (L["chamber"], last_name(L["name"]))
        if counts.get(k, 0) <= 1:
            by_key[k] = lid
        else:
            by_key.pop(k, None)
    by_full = {(L["chamber"], re.sub(r"[^a-z]", "", (L["name"].split()[0] + last_name(L["name"])).lower())): lid for lid, L in legislators.items()}
    pid_to_lid = {str(p["legiscan_id"]): lid for lid, p in legislators.items() if p.get("legiscan_id")}

    # ---- committees (membership straight from the roster rows matched above)
    committees = []
    for code, name in config.COMMITTEES.items():
        r = rosters.get("committees", {}).get(code, {})
        members = [{"id": lid, "role": c["role"]} for lid in dict.fromkeys(roster_lids) for c in legislators[lid]["committees"] if c["code"] == code]
        order = {"Senate Chair": 0, "House Chair": 1}
        members.sort(key=lambda x: (order.get(x["role"], 2), legislators[x["id"]]["chamber"] != "Senate", last_name(legislators[x["id"]]["name"])))
        committees.append({"code": code, "name": name, "chamber": "joint", "members": members, "url": r.get("url"), "bill_count": 0})
    for L in legislators.values():
        L["current"] = bool(L.get("current"))
        if L["current"]:
            for k in ("served_until", "left_reason", "replaced_by"):
                L.pop(k, None)
        else:
            L.setdefault("served_until", None); L.setdefault("replaced_by", None)
    com_by_name = {c["name"].lower(): c["code"] for c in committees}
    com_members = {c["code"]: {m["id"] for m in c["members"]} for c in committees}

    # ---- testimony by LD
    t_by_ld = {}
    for t in t_rows:
        t_by_ld.setdefault(t.get("ld"), []).append(t)
    text_dir = R / "testimony" / "text"

    # ---- bills
    lds = sorted(set(int(k) for k in lg_bills.keys()) | set(status_pages.keys()))
    index, out_bills = [], config.OUT / "bills"
    if config.OUT.exists():
        shutil.rmtree(config.OUT, onexc=_force_remove)
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
            status, status_label = normalize_disposition(sp["final_disposition"]["label"])   # re-derive: rules may have changed since the crawl
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
                txt = tp.read_text(encoding="utf-8")
                if txt.strip(): texts[t["id"]] = txt
            t["has_text"] = t["id"] in texts
        t_total += len(trows)
        # Public hearing: status page (getPHWS) first; else the date the testimony was filed for; else LegiScan's calendar
        t_dates = sorted({t["hearing_date"] for t in trows if t.get("hearing_date")})
        hearing = sp.get("hearing") or ({"date": t_dates[0], "committee_code": code} if t_dates else None) or next(({"date": c["date"], "time": c.get("time"), "location": c.get("location")} for c in lg.get("calendar", []) if "hearing" in (c.get("type") or c.get("description") or "").lower()), None)
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
            "committee_reports": _link_signers(sp.get("committee_reports", []), by_key, by_full, com_members.get(code)),
            "chamber_actions": [{**a, "chamber": {"H": "House", "S": "Senate"}.get(a.get("chamber"), a.get("chamber"))} for a in lg.get("chamber_actions", [])],
            "roll_calls": rolls, "hearing": hearing,
            # other bills whose testimony includes the same documents at the same hearing
            # (one letter filed under several bills); see testimony.positions()
            "heard_with": sorted({x for t in trows for x in (t.get("shared_with") or [])}),
            "tags": lg.get("subjects", []), "affected_statutes": sp.get("affected_statutes", []),
            "testimony": [{k: t.get(k) for k in ("id", "name", "organization", "town", "position", "position_source", "date", "hearing_date", "url", "has_text", "shared_with")} for t in trows],
            "sources": list(dict.fromkeys((sp.get("sources") or []) + ([f"https://legiscan.com/ME/bill/LD{ld}/2025"] if lg else []))),
            "sample": False,
        }
        (out_bills / f"LD{ld}.json").write_text(json.dumps(full, ensure_ascii=False), encoding="utf-8")
        if texts:
            (config.OUT / "testimony" / f"LD{ld}.json").write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")
        index.append({"ld": ld, "paper": full["paper"], "title": title, "sponsor_id": sponsor_id, "cosponsor_ids": cos_ids,
                      "committee_code": code, "status": status, "status_label": status_label,
                      "final_date": full["final_disposition"].get("date"), "hearing_date": (hearing or {}).get("date"),
                      "testimony_count": len(trows), "tags": full["tags"][:6]})
        for t in trows:
            t_index.append({k: t.get(k) for k in ("id", "name", "organization", "town", "position", "position_source", "date", "url", "has_text")} | {"ld": ld, "committee_code": code})
    for c in committees:
        c["bill_count"] = sum(1 for b in index if b["committee_code"] == c["code"])

    (out_bills / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    (config.OUT / "testimony" / "index.json").write_text(json.dumps(t_index, ensure_ascii=False), encoding="utf-8")
    (config.OUT / "legislators.json").write_text(json.dumps(sorted(legislators.values(), key=lambda l: (l["chamber"], last_name(l["name"]))), ensure_ascii=False, indent=0), encoding="utf-8")
    (config.OUT / "committees.json").write_text(json.dumps(committees, ensure_ascii=False, indent=0), encoding="utf-8")
    top = [b["ld"] for b in sorted(index, key=lambda b: -b["testimony_count"])[:3]]
    meta = {"session": config.SESSION, "legislature": config.LEGISLATURE_LABEL, "years": config.YEARS,
            "generated_at": date.today().isoformat(), "sample": bool(sample_notice), "notice": sample_notice,
            "featured": top,
            # current members only; former members stay in legislators.json with current: false
            "counts": {"bills_introduced": len(index), "bills_loaded": len(index),
                       "legislators": sum(1 for l in legislators.values() if l["current"]),
                       "house": sum(1 for l in legislators.values() if l["current"] and l["chamber"] == "House"),
                       "house_tribal": sum(1 for l in legislators.values() if l["current"] and l["district"] == "TRIBE"),
                       "house_vacancies": 151 - sum(1 for l in legislators.values() if l["current"] and l["chamber"] == "House" and l["district"] != "TRIBE"),
                       "senate": sum(1 for l in legislators.values() if l["current"] and l["chamber"] == "Senate"),
                       "former": sum(1 for l in legislators.values() if not l["current"]),
                       "committees": len(committees), "testimony": t_total}}
    (config.OUT / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"built {len(index)} bills, {len(legislators)} legislators, {t_total} testimony rows -> {config.OUT}")

def _link_signers(reports, by_key, by_full=None, members=None):
    """'Senator Anne Carney (Chair)' -> legislator id. Prefer an exact first+last
    match, then a unique last name; restrict to committee members when known."""
    for r in reports:
        ids = []
        for s in r.get("signers", []):
            m = re.match(r"(Senator|Representative|Sen\.|Rep\.)\s+(.+?)(?:\s*\(.*\))?$", s.strip())
            if not m:
                continue
            ch = "Senate" if m.group(1).startswith("Sen") else "House"
            nm = m.group(2)
            lid = (by_full or {}).get((ch, re.sub(r"[^a-z]", "", (nm.split()[0] + last_name(nm)).lower())))
            if not lid or (members and lid not in members):
                lid = by_key.get((ch, last_name(nm))) or lid
            if lid: ids.append(lid)
        r["signer_ids"] = ids
    return reports

if __name__ == "__main__":
    run()
