"""
Campaign finance summaries per legislator, from the Maine Ethics Commission's
campaign finance disclosure system (mainecampaignfinancedisclosure.com).

Inputs (CSV exports; the system lets you download CSVs from its search pages,
and keeps a datasets page for the current cycle):
  pipeline/raw/finance/candidates.csv     candidate list for the cycle
  pipeline/raw/finance/transactions.csv   itemized transactions (contributions/expenditures)

Column names are UNVERIFIED: on first run the script prints the headers it
found and the ones it matched, so the COLUMN map below can be corrected once.

For the 132nd Legislature, members were elected in the 2024 cycle, so export
2024 legislative candidates. For the 133rd, the 2026 cycle.

Output: site/data/finance/{legislator_id}.json with funding type (Maine Clean
Election Act participant or traditionally financed), totals, contribution
count, top contributor types, and a link to the source. Nothing is joined to
votes; the site shows this on the legislator page only.
"""
import csv, json, re, sys
from collections import defaultdict
from pathlib import Path
from . import config

RAW_F = config.RAW / "finance"
SOURCE = "https://mainecampaignfinancedisclosure.com/"

# candidate-name keys we look for, in order; first match wins (case/space-insensitive)
COLUMN = {
    "cand_id":      ["CandidateID", "Candidate Id", "FilerID", "Filer Id", "Id"],
    "cand_name":    ["CandidateName", "Candidate Name", "Name", "FilerName", "Filer Name"],
    "office":       ["Office", "OfficeSought", "Office Sought"],
    "district":     ["District", "DistrictNumber", "District Number"],
    "party":        ["Party", "PartyAffiliation"],
    "financing":    ["FinancingType", "Financing Type", "CandidateType", "Candidate Type", "MCEA", "Public Financing"],
    "election_year":["ElectionYear", "Election Year", "Cycle", "Year"],
    # transactions
    "tx_filer":     ["FilerName", "Filer Name", "CandidateName", "Candidate Name", "Committee Name"],
    "tx_filer_id":  ["FilerID", "Filer Id", "CandidateID"],
    "tx_type":      ["TransactionType", "Transaction Type", "Type"],
    "tx_amount":    ["Amount", "TransactionAmount", "Transaction Amount"],
    "tx_date":      ["Date", "TransactionDate", "Transaction Date"],
    "tx_contrib_type": ["ContributorType", "Contributor Type", "SourceType", "Source Type"],
    "tx_contrib_name": ["ContributorName", "Contributor Name", "Name", "Payee"],
    "tx_contrib_state": ["State", "ContributorState", "Contributor State"],
}

def _norm(s): return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def _resolve(headers: list[str], key: str) -> str | None:
    want = [_norm(x) for x in COLUMN[key]]
    for h in headers:
        if _norm(h) in want:
            return h
    return None

def _money(x):
    try: return float(str(x).replace("$", "").replace(",", "").strip() or 0)
    except ValueError: return 0.0

def last_name(name: str) -> str:
    name = re.sub(r",?\s+(Jr\.?|Sr\.?|II|III|IV)$", "", (name or "").strip())
    if "," in name:                      # "Kuhn, Amy"
        return name.split(",")[0].strip().lower()
    return name.split()[-1].lower() if name else ""

def _name_key(s: str) -> str:
    """'Andrew Stephen Dent' / 'Dent, Andrew S.' -> 'andrew stephen dent' (lowercase, no punctuation, first-last order)."""
    s = (s or "").strip()
    if "," in s:
        last, first = s.split(",", 1); s = f"{first} {last}"
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s.lower())
    s = re.sub(r"[^a-z ]+", "", re.sub(r"[.\-']", " ", s))
    return " ".join(s.split())

def _first_last(s: str) -> str:
    p = _name_key(s).split()
    return f"{p[0]} {p[-1]}" if len(p) >= 2 else " ".join(p)

CHAMBER = {"representative": "House", "senator": "Senate"}
ELECTION_DAY = {"2024": "2024-11-05", "2026": "2026-11-03"}
COMMITTEE_TYPES = {"political_action_committee", "political_party_committee", "candidate_committee", "ballot_question_committee"}
SOURCE_LABEL = {"individual": "Individuals", "committee": "Committees (PACs, party and candidate committees)",
                "candidate": "The candidate", "business": "Businesses and organizations"}
FILER_URL = "https://www.mainecampaignfinancedisclosure.com/public/filers/{id}"
# Members whose candidate rows can't be told apart by name, office and district: the campaign is pinned by filer ID
# (checked by hand on the Commission's site), and only that filer's transactions are used.
PINNED = {
    "h-beck-matthew": {
        # Two 2024 races for HD 122: the March 5, 2024 special election to finish Lois Reckitt's term in the 131st
        # (filer ...fet941nj, $17,533.69, registered Dec 2023), and the 2024 primary/general that elected him to the 132nd.
        "filer": "f_01k89h8gcqe2xs2kmpf81w2aga", "candidate_total": "$10,889.48",
        "also": {"text": "He also won a March 2024 special election for this seat; that campaign reported receiving $17,534.",
                 "filer": "f_01k89h8gcqe2xs2kmpfet941nj"},
    },
}

def run(cycle: str):
    """Inputs (downloaded by hand from the public search; max 10,000 rows per CSV):
      pipeline/raw/finance/candidates_<cycle>.csv   Candidate Name, Election Year, Office, District, Party,
                                                   Candidate Status, Financing Type, Total Money Received, Total Disbursements
      pipeline/raw/finance/transactions_*.csv       Transaction ID, Filer ID, Filer Name, Filer Office, Filer District,
                                                   Transaction Type, Transaction Date, Amount, Contact Type, Contact Name, ...
    The candidate list has no filer ID, so filers are joined on (name, office, district)."""
    cpath = RAW_F / f"candidates_{cycle}.csv"
    tpaths = sorted(RAW_F.glob("transactions_*.csv"))
    if not cpath.exists() or not tpaths:
        print("finance: CSV exports not in pipeline/raw/finance/ yet; skipping"); return
    legislators = json.loads((config.OUT / "legislators.json").read_text(encoding="utf-8"))
    with open(cpath, newline="", encoding="utf-8-sig") as f:
        cand_rows = [c for c in csv.DictReader(f) if c["Office"].lower() in CHAMBER and str(c["Election Year"]) == cycle]
    seen, tx = set(), []
    n_raw = 0
    for p in tpaths:
        with open(p, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                n_raw += 1
                k = tuple(r.values())
                if k in seen: continue
                seen.add(k); tx.append(r)
    print(f"finance: {len(cand_rows)} legislative candidates for {cycle}; {n_raw} transaction rows from {len(tpaths)} files, "
          f"{n_raw - len(tx)} exact duplicates dropped")

    # ---- filers -> candidates on (name, office, district); fall back to first+last name
    cand_by_full, cand_by_fl = defaultdict(list), defaultdict(list)
    for c in cand_rows:
        key = (c["Office"].lower(), str(c["District"]).strip())
        cand_by_full[(_name_key(c["Candidate Name"]),) + key].append(c)
        cand_by_fl[(_first_last(c["Candidate Name"]),) + key].append(c)
    filers = {}
    for r in tx:
        if r["Filer Office"].lower() in CHAMBER:
            filers.setdefault(r["Filer ID"], (r["Filer Name"], r["Filer Office"].lower(), str(r["Filer District"]).strip()))
    filer_cand, ambiguous_filers, unmatched_filers = {}, [], []
    for fid, (name, office, dist) in filers.items():
        hits = cand_by_full.get((_name_key(name), office, dist)) or cand_by_fl.get((_first_last(name), office, dist)) or []
        if len(hits) == 1: filer_cand[fid] = hits[0]
        elif len(hits) > 1: ambiguous_filers.append((name, office, dist, [h["Candidate Name"] for h in hits]))
        else: unmatched_filers.append((name, office, dist))
    # pinned campaigns: the filer goes to the candidate row with the given total, never to a same-named row
    pinned_rows = {}
    for lid, pin in PINNED.items():
        row = next((c for c in cand_rows if c["Total Money Received"].strip() == pin["candidate_total"]
                    and pin["filer"] in filers and _name_key(c["Candidate Name"]) == _name_key(filers[pin["filer"]][0])), None)
        if row:
            pinned_rows[lid] = row; filer_cand[pin["filer"]] = row
            ambiguous_filers = [a for a in ambiguous_filers if _name_key(a[0]) != _name_key(row["Candidate Name"])]
    # A person can have several filer IDs in these files: the previous campaign closing out in 2023, the 2024
    # campaign, and the next campaign opened after the November 2024 election. Keep the one with the most
    # transactions dated 2024-01-01 .. election day; the others are different campaigns and are left out.
    in_2024 = defaultdict(int)
    for r in tx:
        if "2024-01-01" <= r["Transaction Date"] <= ELECTION_DAY.get(cycle, f"{cycle}-12-31"):
            in_2024[r["Filer ID"]] += 1
    all_filers = defaultdict(list)
    for fid, c in filer_cand.items():
        all_filers[id(c)].append(fid)
    cand_filer, other_campaigns = {}, []
    for c in cand_rows:
        fids = sorted(all_filers.get(id(c), []), key=lambda f: -in_2024[f])
        if fids:
            cand_filer[id(c)] = fids[:1]
            if len(fids) > 1:
                other_campaigns.append((c["Candidate Name"], c["Office"], c["District"], fids[0], fids[1:]))
    print(f"finance: {len(filers)} legislative filers in the transactions; {len(filer_cand)} joined to the candidate list; "
          f"{len(unmatched_filers)} not on it (earlier campaigns of people not running in {cycle}); {len(ambiguous_filers)} ambiguous; "
          f"{len(other_campaigns)} candidates also have other campaigns' filer IDs, which are left out")
    for a in ambiguous_filers: print("  ambiguous filer:", a)

    # ---- aggregate itemized transactions per filer
    def money(x): return _money(x)
    agg = defaultdict(lambda: {"contrib": 0.0, "n": 0, "by_source": defaultdict(float), "donors": defaultdict(lambda: [0.0, 0, ""]),
                               "mcea": 0.0, "mcea_n": 0, "mcea_returned": 0.0})
    for r in tx:
        fid = r["Filer ID"]
        if fid not in filer_cand: continue
        a = agg[fid]; typ = r["Transaction Type"]; amt = money(r["Amount"]); ct = r["Contact Type"]
        if typ in ("Monetary Contribution", "In-Kind Contribution"):
            src = "individual" if ct == "individual" else "committee" if ct in COMMITTEE_TYPES else "candidate" if ct == "candidate" else "business"
            a["contrib"] += amt; a["n"] += 1; a["by_source"][src] += amt
            d = a["donors"][(r["Contact Name"] or "Unknown").strip()]; d[0] += amt; d[1] += 1; d[2] = SOURCE_LABEL[src]
        elif typ == "Returned Monetary Contribution":
            a["contrib"] -= amt
        elif typ == "MCEA Payment":
            a["mcea"] += amt; a["mcea_n"] += 1
        elif typ == "Return of MCEA Funds":
            a["mcea_returned"] += amt

    # ---- candidates -> current members on chamber + district + name
    by_seat = defaultdict(list)
    for c in cand_rows:
        by_seat[(CHAMBER[c["Office"].lower()], str(c["District"]).strip())].append(c)
    def same_person(a, b):
        la, lb = _name_key(a).split()[-1:], _name_key(b).split()[-1:]
        return bool(la and lb) and (la[0] == lb[0] or la[0] in _name_key(b) or lb[0] in _name_key(a))
    current = [l for l in legislators if l.get("current") and l.get("district") not in (None, "TRIBE")]
    matched, unmatched, ambiguous = {}, [], []
    for l in current:
        hits = [c for c in by_seat.get((l["chamber"], str(l["district"])), []) if same_person(c["Candidate Name"], l["name"])]
        if len(hits) > 1:
            fl = [c for c in hits if _first_last(c["Candidate Name"]).split()[0] == _first_last(l["name"]).split()[0]]
            hits = fl if len(fl) == 1 else hits
        if l["id"] in pinned_rows: matched[l["id"]] = pinned_rows[l["id"]]
        elif len(hits) == 1: matched[l["id"]] = hits[0]
        elif hits: ambiguous.append((l["name"], l["chamber"], l["district"], [h["Candidate Name"] for h in hits]))
        else: unmatched.append((l["name"], l["chamber"], l["district"]))

    out = config.OUT / "finance"; out.mkdir(parents=True, exist_ok=True)
    for p in out.glob("*.json"): p.unlink()
    mismatches = []
    for lid, c in matched.items():
        fids = cand_filer.get(id(c), [])
        a = {"contrib": 0.0, "n": 0, "by_source": defaultdict(float), "donors": {}, "mcea": 0.0, "mcea_n": 0, "mcea_returned": 0.0}
        for fid in fids:
            x = agg.get(fid)
            if not x: continue
            a["contrib"] += x["contrib"]; a["n"] += x["n"]; a["mcea"] += x["mcea"]; a["mcea_n"] += x["mcea_n"]; a["mcea_returned"] += x["mcea_returned"]
            for k, v in x["by_source"].items(): a["by_source"][k] += v
            for k, v in x["donors"].items():
                d = a["donors"].setdefault(k, [0.0, 0, v[2]]); d[0] += v[0]; d[1] += v[1]
        ftype = c["Financing Type"].strip()
        clean = ftype == "MCEA Qualified"
        # cross-check: Clean Elections candidates receive MCEA payments; traditionally financed ones should not
        if clean and not a["mcea_n"]: mismatches.append((c["Candidate Name"], c["Office"], c["District"], ftype, "no MCEA Payment rows"))
        if not clean and a["mcea_n"]: mismatches.append((c["Candidate Name"], c["Office"], c["District"], ftype, f"{a['mcea_n']} MCEA Payment rows (${a['mcea']:,.2f})"))
        rec = {"legislator_id": lid, "cycle": cycle, "label": "2024 campaign (transactions dated 2023–2024)",
               "candidate_name": c["Candidate Name"], "office": c["Office"], "district": c["District"], "party": c["Party"],
               "financing_type": ftype, "clean_elections": clean,
               "total_received": round(_money(c["Total Money Received"]), 2), "total_disbursed": round(_money(c["Total Disbursements"]), 2),
               "contributions": round(a["contrib"], 2), "n_contributions": a["n"],
               "by_source": [{"source": SOURCE_LABEL[k], "key": k, "amount": round(v, 2)} for k, v in sorted(a["by_source"].items(), key=lambda kv: -kv[1]) if v],
               "public_funds": round(a["mcea"] - a["mcea_returned"], 2) if a["mcea_n"] else 0.0,
               "top_contributors": [{"name": k, "type": v[2], "amount": round(v[0], 2), "count": v[1]}
                                    for k, v in sorted(a["donors"].items(), key=lambda kv: -kv[1][0])[:8] if v[0] > 0],
               "filer_ids": fids, "source_url": FILER_URL.format(id=fids[0]) if fids else SOURCE,
               **({"also": {"text": PINNED[lid]["also"]["text"], "url": FILER_URL.format(id=PINNED[lid]["also"]["filer"])}} if lid in PINNED else {}),
               "note": "Totals raised and spent are the Ethics Commission's candidate totals for the 2024 election. Contributors are summed from itemized monetary and in-kind contributions dated 2023–2024."}
        (out / f"{lid}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    (out / "index.json").write_text(json.dumps({"cycle": cycle, "ids": sorted(matched), "source": SOURCE}), encoding="utf-8")
    RAW_F.mkdir(parents=True, exist_ok=True)
    (RAW_F / "match_report.json").write_text(json.dumps({"matched": len(matched), "current": len(current), "unmatched": unmatched,
        "ambiguous": ambiguous, "ambiguous_filers": ambiguous_filers, "clean_elections_mismatches": mismatches,
        "filers_not_on_candidate_list": unmatched_filers, "other_campaign_filer_ids_left_out": other_campaigns}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"finance: {len(matched)} of {len(current)} current members matched (tribal representatives excluded: they are not elected in these races)")
    for u in unmatched: print("  unmatched member:", u)
    for a in ambiguous: print("  ambiguous member:", a)
    for m in mismatches: print("  Clean Elections cross-check:", m)

if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "2024")
