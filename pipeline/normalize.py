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
    (r"public law|private law|resolve.*chapter|constitutional resolution|emergency enacted|^enacted|became law without", "enacted"),
    # Governor
    (r"veto sustained|vetoed", "vetoed"),
    (r"held by (the )?governor|pocket", "held"),
    # Pending / carried
    (r"carried over|committed to|referred to|in committee|pending", "pending"),
    # Withdrawn
    (r"leave to withdraw|withdrawn", "withdrawn"),
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
    (r"\bin (strong |full )?support of\b|\btestifying in support\b|\bsupport(s|ing)? (ld|l\.d\.)\b|\burge you to (support|pass|vote ought to pass)\b", "support"),
    (r"\bin (strong )?opposition to\b|\btestifying (in opposition|against)\b|\boppos(e|es|ing) (ld|l\.d\.)\b|\burge you to (oppose|vote ought not to pass|reject)\b", "oppose"),
    (r"\bneither for nor against\b|\btestifying neither\b", "neutral"),
]

def infer_position_from_text(text: str | None) -> str | None:
    """Best-effort inference from the opening of the testimony. Callers must
    label the result position_source='inferred'."""
    if not text:
        return None
    head = text[:1500].lower()
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
