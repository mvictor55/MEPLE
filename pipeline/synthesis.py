"""
AI synthesis of written testimony for the bills with the most testimony.

Decided Nov 2026: the 50 bills with the most testimony, using Claude Sonnet 5.5
through the Message Batches API (asynchronous, half the normal price).

Needs ANTHROPIC_API_KEY in .env (a Claude Console API key with credits; this is
separate from a Claude subscription).

    python -m pipeline.synthesis plan            # what would run, token and cost estimate; spends nothing
    python -m pipeline.synthesis run --yes       # submit, wait, collect, build (resumable)
    python -m pipeline.synthesis build           # rebuild site files from saved results only

Hand-written syntheses in synthesis_manual/LD{ld}.json (tracked in git) are
validated and copied into site/data/synthesis/ by `build`, so a rebuild keeps
them. A manual file wins over a batch result for the same bill.

Version 2 (a manual file with "version": 2; LD 676 so far) adds arguments with sides, common ground,
cruxes, checked claims and flags. `build` validates it, then computes from the bill's letters (no API):
coverage by side and label source, distinct people, place groups, campaigns (copied text), repeat writers
and per-argument shares, fills the {placeholders} in overview_lines, and checks test_fixtures if present.
A fixture mismatch stops the build.

How it works
  Pass 1  Each bill's letters are split into parts of about 100k tokens. For each
          part the model lists the distinct arguments made about THIS bill and the
          ids of the letters that make each one.
  Pass 2  For each bill, the model merges pass-1 arguments into at most 12 themes
          and writes a short neutral summary. It returns which pass-1 arguments
          each theme came from; the code (not the model) unions the letter ids,
          so every count on the site is a count of cited letters.
  Checks  Letter ids are kept only if they belong to that part / bill. Themes with
          no valid letters are dropped. Nothing is published for a bill whose
          pass 2 failed.

State is saved in pipeline/raw/synthesis/, so `run` can be re-run after an
interruption without resubmitting finished work.
"""
import json, os, re, sys, time
from datetime import date
from pathlib import Path
import requests
from . import config

MODEL = "claude-sonnet-5-5"
MODEL_LABEL = "Claude Sonnet 5.5"
TOP_N = 50
MIN_TESTIMONIES = 8
CHUNK_CHARS = 400_000          # ~100k tokens per pass-1 request
MAX_LETTER_CHARS = 8_000       # long attachments are cut; the full PDF stays linked
MAX_THEMES = 12
# Batch prices per million tokens (input, output), from platform.claude.com/docs pricing, Nov 2026. Re-check before running.
PRICE_IN, PRICE_OUT = 1.00, 5.00
API = "https://api.anthropic.com"
RAW_S = config.RAW / "synthesis"
MANUAL_S = config.ROOT / "synthesis_manual"
OUT_S = config.OUT / "synthesis"

SYSTEM = """You summarize written public testimony submitted to a committee of the Maine Legislature, for a nonpartisan civic website read by the public and by legislators.

Rules:
- Describe the arguments people made. Do not judge whether they are right, and do not add facts that are not in the letters.
- Only use content about the bill named in the request. A hearing can cover several bills and a letter may discuss other bills; ignore those parts.
- Attribute every argument to the specific letters that make it, by letter id. Cite a letter only if it actually makes that argument.
- Use neutral, plain language. Do not write "many", "most", "few", "overwhelming", "a majority", or any number of letters: counts are computed from your citations.
- Do not characterize people's motives or identities beyond what they state about themselves (for example "a school nurse" or "a town manager").
- Quote at most a few words, and only when the exact wording matters.
- Output only JSON matching the requested schema, with no other text."""

# ---------------------------------------------------------------- env / http
def env(name: str) -> str | None:
    v = os.environ.get(name)
    p = config.ROOT / ".env"
    if not v and p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(name + "="):
                v = line.split("=", 1)[1].strip().strip("'\"")
    return v or None

def _headers():
    key = env("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("Put ANTHROPIC_API_KEY=... in .env (a Claude Console API key with credits).")
    return {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

def _post(path, body):
    r = requests.post(API + path, headers=_headers(), json=body, timeout=300); r.raise_for_status(); return r.json()

def _get(url):
    r = requests.get(url if url.startswith("http") else API + url, headers=_headers(), timeout=300); r.raise_for_status(); return r

# ---------------------------------------------------------------- inputs
def select_bills(top_n=TOP_N, min_t=MIN_TESTIMONIES):
    idx = json.loads((config.OUT / "bills" / "index.json").read_text(encoding="utf-8"))
    ranked = sorted((b for b in idx if (b.get("testimony_count") or 0) >= min_t), key=lambda b: (-b["testimony_count"], b["ld"]))
    return ranked[:top_n]

def letters_for(ld):
    b = json.loads((config.OUT / "bills" / f"LD{ld}.json").read_text(encoding="utf-8"))
    tp = config.OUT / "testimony" / f"LD{ld}.json"
    texts = json.loads(tp.read_text(encoding="utf-8")) if tp.exists() else {}
    out = []
    for t in b.get("testimony", []):
        txt = (texts.get(t["id"]) or "").strip()
        if not txt:
            continue
        who = ", ".join(x for x in (t.get("name"), t.get("organization"), t.get("town")) if x)
        body = txt[:MAX_LETTER_CHARS] + (" [letter continues; cut for length]" if len(txt) > MAX_LETTER_CHARS else "")
        out.append({"id": t["id"], "who": who, "text": body})
    return b, out

def esc_attr(s): return (s or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")

def chunks(letters):
    parts, cur, size = [], [], 0
    for L in letters:
        n = len(L["text"]) + 200
        if cur and size + n > CHUNK_CHARS:
            parts.append(cur); cur, size = [], 0
        cur.append(L); size += n
    if cur: parts.append(cur)
    return parts

def heard_note(b):
    hw = b.get("heard_with") or []
    return (f"This bill's public hearing was held together with {', '.join('LD ' + str(x) for x in hw)}. "
            f"Letters may address those bills too; use only what concerns LD {b['ld']}.\n") if hw else ""

def pass1_prompt(b, part, i, k):
    letters = "\n".join(f'<letter id="{esc_attr(L["id"])}" from="{esc_attr(L["who"])}">\n{L["text"]}\n</letter>' for L in part)
    return f"""Bill: LD {b['ld']}, {b['title']}
{heard_note(b)}Below are {len(part)} letters (part {i+1} of {k}). List each distinct argument or request that these letters make about LD {b['ld']}, once, with every letter that makes it.

Return JSON: {{"arguments":[{{"key":"a1","stance":"support|oppose|amend|neutral","label":"under 12 words","detail":"one or two sentences","letter_ids":["..."]}}]}}
- stance: "support" is an argument for passing the bill; "oppose" is an argument against it; "amend" is a specific change requested; "neutral" is information or a concern without a position.
- A letter can appear under several arguments. Skip letters with nothing substantive about LD {b['ld']}.

<letters>
{letters}
</letters>"""

def pass2_prompt(b, args, n_letters, k):
    lines = "\n".join(f"{a['key']} [{a['stance']}] {a['label']}: {a['detail']} (letters: {len(a['letter_ids'])})" for a in args)
    return f"""Bill: LD {b['ld']}, {b['title']}
{heard_note(b)}{n_letters} letters with readable text were analyzed in {k} part{'s' if k != 1 else ''}. The arguments found are below, each with a key. Merge arguments that make the same point into themes.

Return JSON: {{"summary":"two or three sentences describing what the testimony covered, without numbers and without judging the bill","themes":[{{"stance":"support|oppose|amend|neutral","label":"under 12 words","detail":"one or two sentences","sources":["key", "..."]}}]}}
- At most {MAX_THEMES} themes. Keep the most substantive. Every theme lists the keys it merges.
- Do not state numbers anywhere; counts are computed from the sources.

<arguments>
{lines}
</arguments>"""

def parse_json(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.S)
    return json.loads(m.group(0) if m else t)

# ---------------------------------------------------------------- batch plumbing
def _state():
    p = RAW_S / "state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

def _save_state(s):
    RAW_S.mkdir(parents=True, exist_ok=True); (RAW_S / "state.json").write_text(json.dumps(s, indent=1), encoding="utf-8")

def submit(name, reqs):
    """Submit requests as one or more batches (each under ~200 MB). Returns list of batch ids."""
    ids, cur, size = [], [], 0
    def flush():
        nonlocal cur, size
        if cur:
            res = _post("/v1/messages/batches", {"requests": cur}); ids.append(res["id"])
            print(f"  {name}: submitted batch {res['id']} with {len(cur)} requests")
        cur, size = [], 0
    for r in reqs:
        n = len(json.dumps(r))
        if cur and (size + n > 200_000_000 or len(cur) >= 90_000):
            flush()
        cur.append(r); size += n
    flush()
    return ids

def wait_and_collect(name, batch_ids, poll=60):
    """Poll until every batch has ended; return {custom_id: text or None}."""
    results = {}
    for bid in batch_ids:
        while True:
            b = _get(f"/v1/messages/batches/{bid}").json()
            if b["processing_status"] == "ended":
                break
            c = b.get("request_counts", {})
            print(f"  {name} {bid}: {b['processing_status']} {c}", flush=True); time.sleep(poll)
        body = _get(b["results_url"]).text
        for line in body.splitlines():
            if not line.strip(): continue
            row = json.loads(line); res = row["result"]
            if res["type"] == "succeeded":
                results[row["custom_id"]] = "".join(c.get("text", "") for c in res["message"]["content"] if c.get("type") == "text")
            else:
                results[row["custom_id"]] = None
                print(f"  {row['custom_id']}: {res['type']} {res.get('error')}")
    return results

def request(custom_id, prompt, max_tokens):
    return {"custom_id": custom_id, "params": {"model": MODEL, "max_tokens": max_tokens, "system": SYSTEM,
                                                 "messages": [{"role": "user", "content": prompt}]}}

# ---------------------------------------------------------------- steps
def plan(top_n=TOP_N, min_t=MIN_TESTIMONIES, quiet=False):
    sel = select_bills(top_n, min_t)
    rows, total_chars, n_req = [], 0, 0
    for row in sel:
        b, letters = letters_for(row["ld"])
        parts = chunks(letters)
        chars = sum(len(L["text"]) for L in letters)
        rows.append({"ld": row["ld"], "title": row["title"], "testimony": row["testimony_count"], "with_text": len(letters), "parts": len(parts), "chars": chars})
        total_chars += chars; n_req += len(parts)
    in_tok = total_chars / 4 + n_req * 600 + len(sel) * 3000
    out_tok = n_req * 3000 + len(sel) * 1500
    cost = in_tok / 1e6 * PRICE_IN + out_tok / 1e6 * PRICE_OUT
    if not quiet:
        for r in rows:
            print(f"LD {r['ld']:>5}  {r['testimony']:>4} testimonies  {r['with_text']:>4} with text  {r['parts']} part(s)  {r['title'][:60]}")
        print(f"\n{len(sel)} bills, {n_req} pass-1 requests + {len(sel)} pass-2 requests")
        print(f"Estimated ~{in_tok/1e6:.1f}M input and ~{out_tok/1e6:.2f}M output tokens -> about ${cost:,.0f} with {MODEL_LABEL} batch pricing "
              f"(${PRICE_IN}/${PRICE_OUT} per MTok; verify current prices before running)")
    return sel

def run(top_n=TOP_N, min_t=MIN_TESTIMONIES, yes=False):
    sel = plan(top_n, min_t)
    if not yes:
        sys.exit("\nNothing submitted. Re-run with --yes to spend the estimate above.")
    st = _state(); RAW_S.mkdir(parents=True, exist_ok=True)
    bills_meta, parts_by_ld = {}, {}
    for row in sel:
        b, letters = letters_for(row["ld"]); bills_meta[row["ld"]] = b; parts_by_ld[row["ld"]] = chunks(letters)

    # ---- pass 1
    p1 = st.get("pass1_results", {})
    todo = [(ld, i, part) for ld, parts in parts_by_ld.items() for i, part in enumerate(parts) if f"p1-LD{ld}-c{i}" not in p1 or p1[f"p1-LD{ld}-c{i}"] is None]
    if todo:
        if not st.get("pass1_batches"):
            reqs = [request(f"p1-LD{ld}-c{i}", pass1_prompt(bills_meta[ld], part, i, len(parts_by_ld[ld])), 8000) for ld, i, part in todo]
            st["pass1_batches"] = submit("pass 1", reqs); _save_state(st)
        p1.update(wait_and_collect("pass 1", st["pass1_batches"])); st["pass1_results"] = p1; st.pop("pass1_batches", None); _save_state(st)

    # parse pass 1 into keyed arguments per bill
    args_by_ld = {}
    for ld, parts in parts_by_ld.items():
        args = []
        for i, part in enumerate(parts):
            raw = p1.get(f"p1-LD{ld}-c{i}")
            valid = {L["id"] for L in part}
            try:
                for j, a in enumerate(parse_json(raw).get("arguments", [])):
                    ids = [x for x in a.get("letter_ids", []) if x in valid]
                    if ids:
                        args.append({"key": f"c{i}-a{j+1}", "stance": a.get("stance", "neutral"), "label": a.get("label", ""), "detail": a.get("detail", ""), "letter_ids": ids})
            except Exception as e:
                print(f"  LD {ld} part {i}: could not parse pass-1 output ({e})")
        args_by_ld[ld] = args
    (RAW_S / "pass1_arguments.json").write_text(json.dumps(args_by_ld, indent=1, ensure_ascii=False), encoding="utf-8")

    # ---- pass 2
    p2 = st.get("pass2_results", {})
    todo2 = [ld for ld in parts_by_ld if args_by_ld[ld] and (f"p2-LD{ld}" not in p2 or p2[f"p2-LD{ld}"] is None)]
    if todo2:
        if not st.get("pass2_batches"):
            reqs = [request(f"p2-LD{ld}", pass2_prompt(bills_meta[ld], args_by_ld[ld], sum(len(p) for p in parts_by_ld[ld]), len(parts_by_ld[ld])), 4000) for ld in todo2]
            st["pass2_batches"] = submit("pass 2", reqs); _save_state(st)
        p2.update(wait_and_collect("pass 2", st["pass2_batches"])); st["pass2_results"] = p2; st.pop("pass2_batches", None); _save_state(st)
    st["selected"] = [row["ld"] for row in sel]; st["letters_considered"] = {str(ld): sum(len(p) for p in parts) for ld, parts in parts_by_ld.items()}
    st["generated_at"] = date.today().isoformat(); _save_state(st)
    build()

# ---------------------------------------------------------------- version 2 (hand-written, computed counts)
V2_SIDES = ("support", "oppose", "amend", "neutral")
V2_CLAIM_STATUS = ("accurate", "partly", "inaccurate", "not_checked", "disputed")
V2_PLACEHOLDERS = {"letters", "people", "support", "oppose", "unclear", "no_text", "neutral",
                   "affected_support", "affected_oppose", "maine_support", "maine_oppose", "out_support", "out_oppose",
                   "unknown_support", "unknown_oppose", "campaign_letters", "campaigns", "repeat_writers"}
US_STATES = {"alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY"}
STATE_ABBR = set(US_STATES.values()) | {"DC"}
# Out-of-state places and organizations seen in LD 676 testimony that name no state. An unlisted out-of-state
# organization or place is counted as Maine.
OUT_PLACES = ("north conway", "derry", "greenland", "staten island", "north hero", "new london")
OUT_ORGS = ("conway scenic railroad", "mount washington valley trails association")
PLACE_ALIASES = {"steep falls": "standish"}   # villages counted as their town

def _out_of_state(s: str) -> bool:
    if not s: return False
    low = s.lower()
    if any(re.search(r"\b" + re.escape(n) + r"\b", low) for n in US_STATES): return True      # "New Hampshire"
    if re.search(r"\bmass\b", low): return True                                                 # "Mass"
    toks = re.findall(r"[A-Za-z]+", s)
    if any(t in STATE_ABBR for t in toks): return True                                           # "NH", "RI", "Keene, NH"
    if toks and len(toks[-1]) == 2 and toks[-1].upper() in STATE_ABBR: return True               # "Littleton Ma"
    m = re.search(r",\s*([A-Za-z]{2})\b", s)                                                     # "City, St"
    if m and m.group(1).upper() in STATE_ABBR: return True
    if any(re.search(r"\b" + re.escape(x) + r"\b", low) for x in OUT_PLACES): return True
    return any(o in low for o in OUT_ORGS)

def place_group(t: dict, affected: list[str]) -> str:
    """affected / out / maine / unknown, from the town and organization written on the letter."""
    town, org = (t.get("town") or "").strip(), (t.get("organization") or "").strip()
    def norm_place(x):
        x = x.lower()
        for a, b in PLACE_ALIASES.items(): x = re.sub(r"\b" + a + r"\b", b, x)
        return x
    aff = [a.lower() for a in affected]
    if town and norm_place(town) in aff: return "affected"
    if org and any(re.search(r"\b" + re.escape(a) + r"\b", norm_place(org)) for a in aff): return "affected"   # "resident of Baldwin, Maine"
    if _out_of_state(town) or _out_of_state(org): return "out"
    return "maine" if (town or org) else "unknown"

def person_key(name) -> str | None:
    w = re.sub(r"[^a-z ]", "", re.sub(r"\(.*?\)", "", (name or "").lower())).split()
    return " ".join(w) or None

# Campaigns: copied text, found with 8-word shingles. Each letter's "Name Town LD n" header is cut by matching the LD
# reference in its first 160 characters (PDF extraction often collapses a letter onto one line, so counting lines fails).
# Two letters match when half the shorter letter's shingles appear in the other; letters under 20 words are left out.
# A campaign is 3+ letters from 3+ names, joined by union-find.
SHINGLE, MATCH, MIN_WORDS = 8, 0.5, 20
def campaign_words(text: str, ld: int) -> list[str]:
    m = re.search(r"\bL\.?\s*D\.?[\s-]*" + str(ld) + r"\b", text[:160], re.I)
    if m: text = text[m.end():]
    return re.findall(r"[a-z0-9']+", text.lower())

def find_campaigns(letters: list[dict], texts: dict, ld: int) -> list[list[str]]:
    words = {t["id"]: campaign_words(texts[t["id"]], ld) for t in letters if texts.get(t["id"])}
    sh = {i: {tuple(w[k:k + SHINGLE]) for k in range(len(w) - SHINGLE + 1)} for i, w in words.items() if len(w) >= MIN_WORDS}
    ids = sorted(sh); par = {i: i for i in ids}
    def root(x):
        while par[x] != x: par[x] = par[par[x]]; x = par[x]
        return x
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            x, y = sh[ids[a]], sh[ids[b]]
            small, big = (x, y) if len(x) <= len(y) else (y, x)
            if small and len(small & big) / len(small) >= MATCH: par[root(ids[a])] = root(ids[b])
    groups = {}
    for i in ids: groups.setdefault(root(i), []).append(i)
    name = {t["id"]: person_key(t.get("name")) for t in letters}
    return sorted((sorted(g) for g in groups.values() if len(g) >= 3 and len({name[i] for i in g}) >= 3), key=lambda g: (-len(g), g))

def _v2_ids(rec: dict) -> list[tuple[str, list]]:
    out = [(f"argument {a.get('id')}", a.get("letter_ids", [])) for a in rec.get("arguments", [])]
    out += [(f"also_raised {i}", x.get("letter_ids", [])) for i, x in enumerate(rec.get("also_raised", []))]
    out += [(f"campaigns_manual {i}", x.get("letter_ids", [])) for i, x in enumerate(rec.get("campaigns_manual", []))]
    for key in ("common_ground", "cruxes"):
        for i, x in enumerate(rec.get(key, [])):
            out += [(f"{key} {i} support", x.get("support_ids", [])), (f"{key} {i} oppose", x.get("oppose_ids", []))]
    out.append(("flags", [f.get("letter_id") for f in rec.get("flags", [])]))
    out.append(("test_fixtures.campaign_18", (rec.get("test_fixtures") or {}).get("campaign_18", [])))
    return out

def _check_v2(rec: dict, ld: int, ids: set) -> list[str]:
    errs = []
    if rec.get("ld") != ld: errs.append(f"ld field is {rec.get('ld')}")
    if not rec.get("model"): errs.append("no model")
    if not rec.get("summary"): errs.append("no summary")
    seen = set()
    for a in rec.get("arguments", []):
        if not a.get("id") or a["id"] in seen: errs.append(f"argument id {a.get('id')!r} missing or repeated")
        seen.add(a.get("id"))
        if a.get("side") not in V2_SIDES: errs.append(f"argument {a.get('id')}: side {a.get('side')}")
        if not a.get("label"): errs.append(f"argument {a.get('id')}: no label")
    for where, lst in _v2_ids(rec):
        if len(set(lst)) != len(lst): errs.append(f"{where}: duplicate ids")
        bad = [x for x in lst if x not in ids]
        if bad: errs.append(f"{where}: ids not on LD {ld}: {bad[:5]}")
    for i, line in enumerate(rec.get("overview_lines", [])):
        unknown = set(re.findall(r"\{(\w+)\}", line)) - V2_PLACEHOLDERS
        if unknown: errs.append(f"overview_lines {i}: unknown placeholders {sorted(unknown)}")
    for i, c in enumerate(rec.get("claims", [])):
        if c.get("status") not in V2_CLAIM_STATUS: errs.append(f"claim {i}: status {c.get('status')}")
        if not c.get("claim"): errs.append(f"claim {i}: no text")
    for i, c in enumerate(rec.get("cruxes", [])):
        for k in ("question", "support_view", "oppose_view"):
            if not c.get(k): errs.append(f"crux {i}: no {k}")
    ap = rec.get("affected_places") or {}
    if not ap.get("label") or not ap.get("towns"): errs.append("affected_places needs a label and towns")
    return errs

def build_v2(rec: dict, ld: int) -> dict:
    """Computed counts for a version-2 synthesis. Raises SystemExit on a test_fixtures mismatch."""
    bill = json.loads((config.OUT / "bills" / f"LD{ld}.json").read_text(encoding="utf-8"))
    tp = config.OUT / "testimony" / f"LD{ld}.json"
    texts = json.loads(tp.read_text(encoding="utf-8")) if tp.exists() else {}
    letters = bill.get("testimony", [])
    affected = rec["affected_places"]["towns"]
    place = {t["id"]: place_group(t, affected) for t in letters}
    pos = {t["id"]: t.get("position") for t in letters}
    def src(t):
        s = t.get("position_source")
        if s == "none" or (not t.get("position") and not t.get("has_text")): return "none"
        if s in ("model", "person") and t.get("keyword_label") and t["keyword_label"] != t.get("position"): return "corrected"
        return {"inferred": "keyword", "keyword": "keyword", "model": "model", "person": "person"}.get(s, "keyword" if t.get("position") else "unlabeled")
    source = {t["id"]: src(t) for t in letters}
    c = {"letters": len(letters), "people": len({person_key(t.get("name")) for t in letters if person_key(t.get("name"))})}
    for k in ("support", "oppose", "neutral", "unclear"): c[k] = sum(1 for t in letters if t.get("position") == k)
    c["no_text"] = sum(1 for t in letters if source[t["id"]] == "none")
    c["no_side"] = sum(1 for t in letters if not t.get("position") and source[t["id"]] != "none")   # readable, no side
    for g in ("affected", "maine", "out", "unknown"):
        for k in ("support", "oppose", "neutral", "unclear"):
            c[f"{g}_{k}"] = sum(1 for t in letters if place[t["id"]] == g and t.get("position") == k)
        c[f"{g}_none"] = sum(1 for t in letters if place[t["id"]] == g and not t.get("position"))
    by_source = {k: sum(1 for v in source.values() if v == k) for k in ("keyword", "model", "corrected", "person", "none", "unlabeled")}
    # affected towns, each with its letters by side
    towns = []
    for town in affected:
        ids_t = [t["id"] for t in letters if place[t["id"]] == "affected" and town.lower() in
                 re.sub(r"\bsteep falls\b", "standish", f"{t.get('town') or ''} {t.get('organization') or ''}".lower())]
        towns.append({"town": town, "support": sum(pos[i] == "support" for i in ids_t), "oppose": sum(pos[i] == "oppose" for i in ids_t), "letters": len(ids_t)})
    camps = find_campaigns(letters, texts, ld)
    manual_sets = [set(m["letter_ids"]) for m in rec.get("campaigns_manual", [])]
    campaigns = []
    for n, g in enumerate(camps, 1):
        shortest = min(g, key=lambda i: len(texts[i]))
        body = texts[shortest]
        m = re.search(r"I support LD\s*" + str(ld) + r" because", body, re.I)
        campaigns.append({"id": f"c{n}", "letter_ids": g, "names": len({person_key(next(t for t in letters if t["id"] == i).get("name")) for i in g}),
                          "sides": {k: sum(pos[i] == k for i in g) for k in ("support", "oppose", "unclear")},
                          "text_from": shortest, "text": (body[m.start():] if m else body).strip(),
                          "same_as_manual": next((j for j, ms in enumerate(manual_sets) if ms == set(g)), None)})
    in_campaign = {i: cp["id"] for cp in campaigns if cp["same_as_manual"] is None for i in cp["letter_ids"]}
    c["campaigns"] = sum(1 for cp in campaigns if cp["same_as_manual"] is None)
    c["campaign_letters"] = len(in_campaign)
    # repeat writers: the same normalized name on 2+ letters
    by_name = {}
    for t in letters:
        k = person_key(t.get("name"))
        if k: by_name.setdefault(k, []).append(t)
    repeat = []
    for k, ts in by_name.items():
        if len(ts) < 2: continue
        places = {(t.get("town") or t.get("organization") or "").strip().lower() for t in ts}
        repeat.append({"name": ts[0]["name"], "letter_ids": [t["id"] for t in ts], "places": sorted({(t.get("town") or t.get("organization") or "") for t in ts}),
                       "sides": [t.get("position") for t in ts], "note": "may be different people" if len(places) > 1 else None})
    c["repeat_writers"] = len(repeat)
    side_total = {"support": c["support"], "oppose": c["oppose"], "amend": c["oppose"], "neutral": c["neutral"] + c["unclear"]}
    arguments = []
    for a in rec.get("arguments", []):
        ids_a = a["letter_ids"]
        arguments.append({"id": a["id"], "letters": len(ids_a),
                          "share": round(len(ids_a) / side_total[a["side"]], 4) if side_total[a["side"]] else 0,
                          "campaign_copies": sum(1 for i in ids_a if i in in_campaign),
                          "places": {g: sum(1 for i in ids_a if place[i] == g) for g in ("affected", "maine", "out", "unknown")}})
    computed = {"counts": c, "by_source": by_source, "towns": towns, "campaigns": campaigns, "repeat_writers": repeat,
                "arguments": arguments, "letters": {t["id"]: {"place": place[t["id"]], "source": source[t["id"]],
                                                              **({"campaign": in_campaign[t["id"]]} if t["id"] in in_campaign else {})} for t in letters},
                "method": {"places": "Place groups use the town and organization written on each letter. Towns listed as along the line (Steep Falls counts as Standish) are "
                                     f"“{rec['affected_places']['label']}”; a state name or abbreviation, a “City, ST” address, or a known out-of-state place or organization "
                                     "counts as out of state; any other town or organization counts as Maine. An unlisted out-of-state organization would be counted as Maine.",
                           "campaigns": f"Copied text is found by comparing {SHINGLE}-word sequences after each letter's “Name Town LD {ld}” header. Two letters match when "
                                        f"{int(MATCH*100)}% of the shorter letter's sequences appear in the other; letters under {MIN_WORDS} words are left out. A campaign is 3 or more letters from 3 or more names."}}
    out = {k: v for k, v in rec.items() if k != "test_fixtures"} | {"computed": computed}
    out["overview"] = [re.sub(r"\{(\w+)\}", lambda m: f"{c[m.group(1)]:,}" if isinstance(c.get(m.group(1)), int) else m.group(0), line) for line in rec.get("overview_lines", [])]
    fx = rec.get("test_fixtures")
    if fx:
        bad = [f"{k}: expected {v}, computed {c.get(k)}" for k, v in (fx.get("expected_counts") or {}).items() if c.get(k) != v]
        if "campaign_18" in fx:
            want = set(fx["campaign_18"])
            found = next((set(cp["letter_ids"]) for cp in campaigns if set(cp["letter_ids"]) & want), set())
            if found != want:
                bad.append(f"campaign_18: missing {sorted(want - found)}, extra {sorted(found - want)}")
        if bad:
            raise SystemExit(f"synthesis LD{ld}.json: TEST FIXTURE MISMATCH, build stopped:\n  " + "\n  ".join(bad))
        print(f"  LD {ld}: test fixtures pass ({len(fx.get('expected_counts') or {})} counts, campaign of {len(fx.get('campaign_18', []))})")
    others = [cp for cp in campaigns if not (fx and set(cp["letter_ids"]) == set(fx.get("campaign_18", [])))]
    for cp in others:
        print(f"  LD {ld}: also found a {len(cp['letter_ids'])}-letter copied-text group {cp['letter_ids']}"
              + (f" (same letters as campaigns_manual {cp['same_as_manual']})" if cp["same_as_manual"] is not None else ""))
    return out

def _check_manual(rec: dict, ld: int) -> list[str]:
    """Problems with a hand-written synthesis: ids must belong to the bill and have text, counts must match."""
    errs = []
    bp = config.OUT / "bills" / f"LD{ld}.json"
    if not bp.exists():
        return [f"no built bill file for LD {ld}"]
    if rec.get("version") == 2:
        return _check_v2(rec, ld, {t["id"] for t in json.loads(bp.read_text(encoding="utf-8")).get("testimony", [])})
    ids = {t["id"] for t in json.loads(bp.read_text(encoding="utf-8")).get("testimony", [])}
    tp = config.OUT / "testimony" / f"LD{ld}.json"
    with_text = set(json.loads(tp.read_text(encoding="utf-8"))) if tp.exists() else set()
    if rec.get("ld") != ld: errs.append(f"ld field is {rec.get('ld')}")
    if not rec.get("model"): errs.append("no model")
    if len(rec.get("themes", [])) > MAX_THEMES: errs.append("too many themes")
    for i, t in enumerate(rec.get("themes", [])):
        tid = t.get("testimony_ids", [])
        if t.get("stance") not in ("support", "oppose", "amend", "neutral"): errs.append(f"theme {i}: stance {t.get('stance')}")
        if t.get("count") != len(tid): errs.append(f"theme {i}: count {t.get('count')} != {len(tid)} ids")
        if len(set(tid)) != len(tid): errs.append(f"theme {i}: duplicate ids")
        bad = [x for x in tid if x not in ids]
        if bad: errs.append(f"theme {i}: ids not on LD {ld}: {bad[:5]}")
        notext = [x for x in tid if x in ids and x not in with_text]
        if notext: errs.append(f"theme {i}: ids without text: {notext[:5]}")
    return errs

def build():
    st = _state()
    OUT_S.mkdir(parents=True, exist_ok=True)
    for p in OUT_S.glob("*.json"): p.unlink()
    done, models = [], {}
    have_batch = bool(st.get("selected")) and (RAW_S / "pass1_arguments.json").exists()
    if not have_batch:
        print("synthesis: no saved batch results (run `python -m pipeline.synthesis plan`, then `run --yes`)")
    args_by_ld = json.loads((RAW_S / "pass1_arguments.json").read_text(encoding="utf-8")) if have_batch else {}
    for ld in (st.get("selected", []) if have_batch else []):
        raw = st.get("pass2_results", {}).get(f"p2-LD{ld}")
        args = {a["key"]: a for a in args_by_ld.get(str(ld), [])}
        if not raw or not args:
            print(f"  LD {ld}: no synthesis (missing pass-2 result)"); continue
        try:
            out = parse_json(raw)
        except Exception as e:
            print(f"  LD {ld}: could not parse pass-2 output ({e})"); continue
        themes = []
        for t in out.get("themes", [])[:MAX_THEMES]:
            ids = sorted({i for k in t.get("sources", []) if k in args for i in args[k]["letter_ids"]})
            if not ids: continue
            stance = t.get("stance") if t.get("stance") in ("support", "oppose", "amend", "neutral") else "neutral"
            themes.append({"stance": stance, "label": t.get("label", "").strip(), "detail": t.get("detail", "").strip(), "testimony_ids": ids, "count": len(ids)})
        if not themes:
            print(f"  LD {ld}: no valid themes"); continue
        order = {"support": 0, "oppose": 1, "amend": 2, "neutral": 3}
        themes.sort(key=lambda t: (order[t["stance"]], -t["count"]))
        rec = {"ld": int(ld), "model": MODEL_LABEL, "generated_at": st.get("generated_at"), "summary": (out.get("summary") or "").strip(),
               "letters_considered": st.get("letters_considered", {}).get(str(ld)), "themes": themes}
        (OUT_S / f"LD{ld}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8"); done.append(int(ld)); models[str(ld)] = MODEL_LABEL
    # hand-written syntheses (tracked in synthesis_manual/) are copied in after validation
    manual = []
    for p in sorted(MANUAL_S.glob("LD[0-9]*.json")) if MANUAL_S.exists() else []:
        ld = int(p.stem[2:])
        rec = json.loads(p.read_text(encoding="utf-8"))
        errs = _check_manual(rec, ld)
        if errs:
            if rec.get("version") == 2:   # a v2 file carries test fixtures; a broken one stops the build instead of quietly disappearing
                raise SystemExit(f"synthesis {p.name}: version 2 file failed validation:\n  " + "\n  ".join(errs))
            print(f"  {p.name}: not published: {'; '.join(errs)}"); continue
        if rec.get("version") == 2:
            rec = build_v2(rec, ld)
        (OUT_S / f"LD{ld}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        if ld not in done: done.append(ld)
        models[str(ld)] = rec["model"]; manual.append(ld)
    if not done:
        for p in OUT_S.glob("*.json"): p.unlink()
        return
    (OUT_S / "index.json").write_text(json.dumps({"lds": sorted(done), "models": models, "manual": sorted(manual),
                                                  "model": MODEL_LABEL if have_batch else None,
                                                  "generated_at": st.get("generated_at")}), encoding="utf-8")
    print(f"synthesis: {len(done)} bills written to {OUT_S} ({len(manual)} hand-written from {MANUAL_S.name}/)")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    top = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--top=")), TOP_N)
    if cmd == "plan": plan(top)
    elif cmd == "run": run(top, yes="--yes" in sys.argv)
    elif cmd == "build": build()
    else: sys.exit("usage: python -m pipeline.synthesis plan|run --yes|build [--top=50]")
