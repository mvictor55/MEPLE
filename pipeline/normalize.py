"""
Normalization rules: Maine's final-disposition and committee-report vocabulary
mapped to the handful of statuses MEPLE displays.

Statuses the site understands: enacted, vetoed, held, died, withdrawn, pending.
Anything unmatched is returned as 'pending' with the raw label kept, so nothing
is silently mislabeled; unmatched labels are logged for review.
"""
import re

# Ordered: first match wins. Patterns are matched against the lowercased label.
DISPOSITION_RULES = [
    # Became law
    (r"public law|private law|resolve.*chapter|constitutional resolution|emergency enacted|^enacted|became law without|(?<!not )finally passed", "enacted"),   # resolves are "(emergency) finally passed"
    # Governor
    (r"veto sustained|vetoed", "vetoed"),
    (r"held by (the )?governor|pocket", "held"),
    # Pending / carried
    (r"carried over|committed to|referred to|in committee|pending", "pending"),
    # Withdrawn
    (r"leave to withdraw|withdrawn|withdrawal", "withdrawn"),
    # Dead
    (r"died|ought not to pass|ontp|indefinitely postponed|placed in the legislative files|failed|rejected|not finally passed|not enacted|unanimous ought not", "died"),
]

def normalize_disposition(label: str | None) -> tuple[str, str]:
    """Return (status, display_label)."""
    if not label:
        return "pending", "Pending"
    low = label.lower()
    for pat, status in DISPOSITION_RULES:
        if re.search(pat, low):
            return status, _pretty(label)
    return "pending", _pretty(label)

def _pretty(label: str) -> str:
    label = re.sub(r"\s+", " ", label).strip()
    # "Died Between Houses, Apr 13, 2026" -> "Died between houses"
    label = re.sub(r",?\s+[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}$", "", label)
    return label[:1].upper() + label[1:].lower() if label.isupper() else label

# Committee report vocabulary -> short label and meaning
REPORT_VOCAB = {
    "OTP": ("Ought to Pass", "advance"),
    "OTP-AM": ("Ought to Pass as Amended", "advance"),
    "OTP-ND": ("Ought to Pass in New Draft", "advance"),
    "ONTP": ("Ought Not to Pass", "kill"),
    "UNANIMOUS ONTP": ("Unanimous Ought Not to Pass", "kill"),
    "REFER": ("Refer to Another Committee", "refer"),
    "LTW": ("Leave to Withdraw", "withdraw"),
}

def expand_report(code: str) -> str:
    code = (code or "").strip().upper()
    return REPORT_VOCAB.get(code, (code, None))[0]

# Testimony position vocabulary as it may appear in the Legislature's data.
POSITION_MAP = {
    "support": "support", "proponent": "support", "for": "support", "in support": "support",
    "oppose": "oppose", "opponent": "oppose", "against": "oppose", "in opposition": "oppose",
    "neither": "neutral", "neither for nor against": "neutral", "nfna": "neutral", "neutral": "neutral",
}

def normalize_position(raw: str | None) -> str | None:
    if not raw:
        return None
    return POSITION_MAP.get(raw.strip().lower())

POSITION_TEXT_PATTERNS = [
    (r"\bvote \W?ought to pass\b|\bplease (support|pass|fund)\b|\b(we|i) (strongly |fully )?support (this|the) (bill|legislation|proposal)\b|\bsupport (its|the bill's) passage\b|\bin (strong |full )?support of\b|\btestifying in support\b|\bsupport(s|ing)? (ld|l\.d\.)\b|\burge you to (support|pass|vote ought to pass)\b", "support"),
    (r"\bvote \W?ought not to pass\b|\bdo not pass\b|\bplease (oppose|reject|vote no|vote against)\b|\b(we|i) (strongly )?oppose (this|the) (bill|legislation|proposal)\b|\bin (strong )?opposition to\b|\btestifying (in opposition|against)\b|\boppos(e|es|ing) (ld|l\.d\.)\b|\burge you to (oppose|vote ought not to pass|reject)\b", "oppose"),
    (r"\bneither for nor against\b|\btestifying neither\b", "neutral"),
]

def infer_position_from_text(text: str | None) -> str | None:
    """Best-effort inference from the opening and the closing ask of the
    testimony, ignoring which bill a stance is about. Used by
    infer_position_for_ld only for documents that concern a single bill."""
    if not text:
        return None
    head = text.lower() if len(text) <= 2300 else (text[:1500] + " \n " + text[-800:]).lower()
    hits = {}
    for pat, pos in POSITION_TEXT_PATTERNS:
        m = re.search(pat, head)
        if m:
            hits[pos] = min(hits.get(pos, 10**9), m.start())
    if not hits:
        return None
    # Earliest statement wins; ties -> unknown
    best = sorted(hits.items(), key=lambda kv: kv[1])
    if len(best) > 1 and best[0][1] == best[1][1]:
        return None
    return best[0][0]

# ---- Bill-specific inference -------------------------------------------------
# A letter is often filed under several bills heard together ("in support of
# LD 1822 and in opposition to LDs 1088, 1224 and 1284"). A stance only counts
# for a bill when the phrase names that bill:
#   cue + LD list     "in support of LD 1822", "oppose LDs 1224, 1088, and 1284",
#                     "in Opposition to 1822", "introducing LD 1822" (sponsor)
#   LD list + cue     "LD 1822 -- Ought to Pass", "Position on LD 1822: Against",
#                     "Re: LD 1822, Maine Data Privacy Act -- SUPPORT"
# A cue that names no bill ("I urge you to support it") is used only when the
# document is filed under one bill and states no stance on any other bill.

_CUE = re.compile(
    r"(?P<neutral>neither for nor against|neither in support nor in opposition|testifying neither)"
    r"|(?P<sponsor>\b(?:introduc(?:e|ing)|present(?:ing)?)\s+(?:my bill\s*,?:?\s*)?(?=LDs?\s*\d))"
    r"|(?P<oppose>\bin opposition (?:to|of)\b|\boppos(?:e|es|ing|ed to)\b|\bagainst\b|\bought not to pass\b|\breject\b)"
    r"|(?P<support>\bin (?:strong |full )?support (?:of|for)\b|\bin support\b|\bsupport(?:s|ing)?\b(?: for)?|\bin favou?r of\b|\bought to pass\b|\bproponent of\b)",
    re.I)
_LDLIST = re.compile(r"LDs?\s*#?\s*\d{1,4}(?:\s*(?:,|&|and|or|,\s*and|\.(?=\s*LD))\s*(?:LDs?\s*)?\d{1,4}\b)*", re.I)
# "in opposition to the bills before the committee", "LD 1432 and all such bills before you today"
_ALL_BILLS = re.compile(r"\s*(?:to|of|on|for)?\s*(?:all )?(?:of )?(?:the|these|such|those) bills\b", re.I)
# an Act's title between a cue and the LD, or between LDs in a header list
_TITLE_THEN_LD = re.compile(r"""\s*(?:of|for|to)?\s*[:,]?\s*(?:the\s+)?["“']?An A[\W_]?ct\b[^.;]{0,180}?(?:,?\s*(?:and|&)\s*)?\(?(?=LDs?\s*#?\s*\d)""", re.I)
_SEP_CUE = re.compile(r"\s*(?:,[^.,]{0,70}?)?\s*[-\u2013\u2014:]+\s*(?P<cue>ought not to pass|ought to pass|support|oppose|opposition|against|neither for nor against)\b", re.I)

def _norm_lds(text: str) -> str:
    t = re.sub(r"\s+", " ", text)
    t = re.sub(r"\b(?:L|l|I|1)\.\s?D\.?\s?s\b", "LDs", t)          # L.D.s 1088
    t = re.sub(r"\b(?:L|l|I)\.\s?D\.?(?=\s?#?\s?\d)", "LD", t)       # L.D. 1822, l.D 1822 (OCR)
    t = re.sub(r"\bLD(?=\d)", "LD ", t)                               # LD1822
    return t

def _lds_in(s: str) -> set[int]:
    return {int(n) for m in _LDLIST.finditer(s) for n in re.findall(r"\d{1,4}", m.group(0))}

def _cue_kind(m) -> str:
    k = m.lastgroup
    return "support" if k == "sponsor" else k

def stance_by_ld(text: str, known_lds=()) -> list[tuple[int, int, str]]:
    """[(char offset, ld, position)] for every stance phrase tied to a named LD.
    Bare numbers ("In Opposition to 1822") count only when they are in known_lds."""
    t = _norm_lds(text)
    known = {int(x) for x in known_lds}
    cues = list(_CUE.finditer(t))
    out = []
    for i, m in enumerate(cues):
        stop = min(cues[i + 1].start() if i + 1 < len(cues) else len(t), m.end() + 300)
        after = t[m.end():stop]
        lm = re.match(r"[^.;]{0,40}?(?=LDs?\s*#?\s*\d)", after) or _TITLE_THEN_LD.match(after)
        lds = set()
        if lm:
            ll = _LDLIST.match(after, lm.end())
            end = 0
            while ll:                      # "LD 1088: An Act ..., LD 1224: An Act ..., LD 1822"
                lds |= _lds_in(ll.group(0)); end = ll.end()
                nxt = _TITLE_THEN_LD.match(after, ll.end())
                ll = _LDLIST.match(after, nxt.end()) if nxt else None
            if re.match(r"\s*,?\s*and all (?:such |other |of the )?(?:other )?bills\b", after[end:], re.I):
                lds |= known
        else:
            bm = re.match(r"\s*(?:of|to|on|for)?\s*\(?\s*(\d{3,4}(?:\s*(?:,|and|,\s*and)\s*\d{3,4})*)\b", after)
            if bm:
                lds = {int(n) for n in re.findall(r"\d{3,4}", bm.group(1))} & known
            elif _ALL_BILLS.match(after):
                lds = set(known)           # every bill this document is filed under
        for ld in lds:
            out.append((m.start(), ld, _cue_kind(m)))
    # LD list followed by a separator and a cue: "LD 1822 -- Ought to Pass"
    for ll in _LDLIST.finditer(t):
        sm = _SEP_CUE.match(t, ll.end())
        if sm:
            c = sm.group("cue").lower()
            pos = ("neutral" if c.startswith("neither") else "oppose" if c in ("ought not to pass", "oppose", "opposition", "against") else "support")
            for ld in _lds_in(ll.group(0)):
                out.append((ll.start(), ld, pos))
    return sorted(out)

def infer_position_for_ld(text: str | None, ld: int | None, shared_with=()) -> str | None:
    """Position on bill `ld`. shared_with: other LDs the same document is filed
    under. Callers must label the result position_source='inferred'."""
    if not text or not ld:
        return None
    hits = stance_by_ld(text, set(shared_with) | {ld})
    mine = [p for _, l, p in hits if l == ld]
    if mine:
        return mine[0]                                   # earliest statement about this bill
    if shared_with or any(l != ld for _, l, _ in hits):
        return None                                      # stances are about other bills only
    return infer_position_from_text(_norm_lds(text))
