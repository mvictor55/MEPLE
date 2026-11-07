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

def run(cycle: str):
    if not ((RAW_F / "candidates.csv").exists() and (RAW_F / "transactions.csv").exists()):
        print("finance: CSV exports not in pipeline/raw/finance/ yet; skipping"); return
    legislators = json.loads((config.OUT / "legislators.json").read_text())
    with open(RAW_F / "candidates.csv", newline="", encoding="utf-8-sig") as f:
        cand_rows = list(csv.DictReader(f)); cand_h = cand_rows[0].keys() if cand_rows else []
    with open(RAW_F / "transactions.csv", newline="", encoding="utf-8-sig") as f:
        tx_rows = list(csv.DictReader(f)); tx_h = tx_rows[0].keys() if tx_rows else []
    ck = {k: _resolve(list(cand_h), k) for k in ("cand_id", "cand_name", "office", "district", "party", "financing", "election_year")}
    tk = {k: _resolve(list(tx_h), k) for k in ("tx_filer", "tx_filer_id", "tx_type", "tx_amount", "tx_date", "tx_contrib_type", "tx_contrib_name", "tx_contrib_state")}
    print("candidates.csv headers:", list(cand_h)); print("matched:", ck)
    print("transactions.csv headers:", list(tx_h)); print("matched:", tk)
    missing = [k for k, v in {**ck, **tk}.items() if v is None and k in ("cand_name", "office", "tx_filer", "tx_amount", "tx_type")]
    if missing:
        sys.exit(f"Fix COLUMN in pipeline/finance.py for: {missing}")

    # candidates -> legislators by chamber + last name (+ district when available)
    by_key = {}
    for l in legislators:
        by_key.setdefault((l["chamber"], last_name(l["name"])), []).append(l)
    matched = {}
    for c in cand_rows:
        office = (c.get(ck["office"]) or "").lower()
        chamber = "Senate" if "senat" in office else ("House" if "represent" in office or "house" in office else None)
        if not chamber:
            continue
        if ck["election_year"] and str(c.get(ck["election_year"]) or "") not in ("", cycle):
            continue
        cands = by_key.get((chamber, last_name(c.get(ck["cand_name"]))), [])
        if ck["district"] and len(cands) > 1:
            d = re.sub(r"\D", "", str(c.get(ck["district"]) or ""))
            cands = [x for x in cands if str(x.get("district") or "") == d] or cands
        if len(cands) == 1:
            matched[cands[0]["id"]] = c

    # transactions aggregated by filer
    agg = defaultdict(lambda: {"raised": 0.0, "spent": 0.0, "n_contrib": 0, "by_type": defaultdict(float), "in_state": 0.0, "out_state": 0.0})
    for t in tx_rows:
        filer = t.get(tk["tx_filer"]) or ""
        typ = (t.get(tk["tx_type"]) or "").lower()
        amt = _money(t.get(tk["tx_amount"]))
        a = agg[_norm(filer)]
        if "contrib" in typ or "receipt" in typ:
            a["raised"] += amt; a["n_contrib"] += 1
            if tk["tx_contrib_type"]: a["by_type"][t.get(tk["tx_contrib_type"]) or "Unknown"] += amt
            if tk["tx_contrib_state"]:
                st = (t.get(tk["tx_contrib_state"]) or "").strip().upper()
                a["in_state" if st in ("ME", "MAINE") else "out_state"] += amt
        elif "expend" in typ or "disburse" in typ:
            a["spent"] += amt

    out = config.OUT / "finance"; out.mkdir(parents=True, exist_ok=True)
    n = 0
    for lid, c in matched.items():
        a = agg.get(_norm(c.get(ck["cand_name"]) or ""), None)
        fin_raw = (c.get(ck["financing"]) or "") if ck["financing"] else ""
        funding = "Maine Clean Election Act (publicly financed)" if re.search(r"mcea|clean|public", fin_raw, re.I) else ("Traditionally financed" if fin_raw else None)
        rec = {"legislator_id": lid, "cycle": cycle, "candidate_name": c.get(ck["cand_name"]), "party": c.get(ck["party"]) if ck["party"] else None,
               "funding_type": funding, "funding_type_raw": fin_raw or None,
               "raised": round(a["raised"], 2) if a else None, "spent": round(a["spent"], 2) if a else None,
               "n_contributions": a["n_contrib"] if a else None,
               "in_state_share": round(a["in_state"] / (a["in_state"] + a["out_state"]), 3) if a and (a["in_state"] + a["out_state"]) else None,
               "top_contributor_types": sorted(({"type": k, "amount": round(v, 2)} for k, v in a["by_type"].items()), key=lambda x: -x["amount"])[:6] if a else [],
               "source": SOURCE, "note": "Totals are summed from itemized transactions in the Ethics Commission's disclosure system for this cycle; publicly financed candidates receive most of their funds from the Maine Clean Election Fund."}
        (out / f"{lid}.json").write_text(json.dumps(rec, ensure_ascii=False)); n += 1
    (out / "index.json").write_text(json.dumps({"cycle": cycle, "ids": sorted(matched.keys()), "source": SOURCE}))
    print(f"finance: {n} legislators matched of {len(legislators)} ({len(cand_rows)} candidates, {len(tx_rows)} transactions)")

if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "2024")
