"""
Derive per-legislator and per-committee detail files from the built bill data.

Runs AFTER pipeline.build (it reads site/data/bills/*.json) and adds:
  site/data/legislators/{id}.json   roll-call votes, committee-report signatures,
                                    sponsorship counts, hearing appearances
  site/data/committees/{code}.json  hearings by date, reports with splits,
                                    testimony volume by bill

No new sources: everything here is already on the bill pages, re-keyed so a
legislator's or a committee's record can be read in one place.
"""
import json
from collections import defaultdict
from pathlib import Path
from . import config

def run():
    bills_dir = config.OUT / "bills"
    index = json.loads((bills_dir / "index.json").read_text())
    legislators = {l["id"]: l for l in json.loads((config.OUT / "legislators.json").read_text())}
    committees = {c["code"]: c for c in json.loads((config.OUT / "committees.json").read_text())}
    by_ld = {b["ld"]: b for b in index}

    L = defaultdict(lambda: {"votes": [], "signatures": [], "sponsored": [], "cosponsored": [], "hearings_as_sponsor": []})
    C = defaultdict(lambda: {"hearings": [], "reports": [], "bills": [], "testimony_by_bill": []})

    for row in index:
        full = json.loads((bills_dir / f"LD{row['ld']}.json").read_text())
        ld, title = row["ld"], row["title"]
        short = {"ld": ld, "title": title, "status": row["status"], "status_label": row["status_label"]}
        if row.get("sponsor_id"):
            L[row["sponsor_id"]]["sponsored"].append(short)
        for cid in row.get("cosponsor_ids", []):
            L[cid]["cosponsored"].append(short)
        for rc in full.get("roll_calls", []):
            for v in rc.get("votes", []):
                if v.get("id"):
                    L[v["id"]]["votes"].append({"ld": ld, "title": title, "date": rc.get("date"), "chamber": rc.get("chamber"),
                                                "motion": rc.get("motion"), "vote": v.get("vote"), "result": rc.get("result"),
                                                "yea": rc.get("yea"), "nay": rc.get("nay"), "url": rc.get("url")})
        for rep in full.get("committee_reports", []):
            for sid in rep.get("signer_ids", []):
                L[sid]["signatures"].append({"ld": ld, "title": title, "report": rep.get("label"), "recommendation": rep.get("recommendation"),
                                             "signer_count": rep.get("signer_count")})
        code = row.get("committee_code")
        if code:
            C[code]["bills"].append(short)
            if full.get("hearing") and full["hearing"].get("date"):
                C[code]["hearings"].append({"date": full["hearing"]["date"], "ld": ld, "title": title, "testimony_count": row.get("testimony_count", 0)})
            if full.get("committee_reports"):
                C[code]["reports"].append({"ld": ld, "title": title,
                                           "reports": [{"label": r.get("label"), "recommendation": r.get("recommendation"), "signer_count": r.get("signer_count")} for r in full["committee_reports"]]})
            if row.get("testimony_count"):
                C[code]["testimony_by_bill"].append({"ld": ld, "title": title, "count": row["testimony_count"]})

    out_l = config.OUT / "legislators"; out_l.mkdir(exist_ok=True)
    for lid, rec in L.items():
        if lid not in legislators:
            continue
        rec["votes"].sort(key=lambda v: (v["date"] or "", v["ld"]), reverse=True)
        rec["signatures"].sort(key=lambda s: s["ld"])
        tally = defaultdict(int)
        for v in rec["votes"]:
            tally[(v["vote"] or "unknown").lower()] += 1
        rec["vote_tally"] = dict(tally)
        rec["counts"] = {"sponsored": len(rec["sponsored"]), "cosponsored": len(rec["cosponsored"]), "votes": len(rec["votes"]), "signatures": len(rec["signatures"])}
        (out_l / f"{lid}.json").write_text(json.dumps({"id": lid, **rec}, ensure_ascii=False))

    out_c = config.OUT / "committees"; out_c.mkdir(exist_ok=True)
    for code, rec in C.items():
        if code not in committees:
            continue
        rec["hearings"].sort(key=lambda h: (h["date"] or "", h["ld"]))
        rec["testimony_by_bill"].sort(key=lambda t: -t["count"])
        rec["counts"] = {"bills": len(rec["bills"]), "hearings": len(rec["hearings"]), "reports": len(rec["reports"]),
                         "testimony": sum(t["count"] for t in rec["testimony_by_bill"])}
        (out_c / f"{code}.json").write_text(json.dumps({"code": code, **rec}, ensure_ascii=False))
    print(f"legislator files: {len([k for k in L if k in legislators])}, committee files: {len([k for k in C if k in committees])}")

if __name__ == "__main__":
    run()
