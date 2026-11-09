"""
Profiles for organizations, state agencies, and public officials who testified.

Decided Nov 2026: profiles are created for
  - organizations named on testimony, when the same normalized name appears on
    2 or more testimonies;
  - state agencies and other public bodies in pipeline/agencies.py;
  - public officials testifying in their official role for one of those bodies
    (e.g. a commissioner, an authority's president), identified from the
    testimony's organization field or the letter's self-introduction.
Private individuals do NOT get profiles. Their testimony stays on bill pages
and in the archive's name search, as on the Legislature's own site. Once MEPLE
has accounts, individuals can opt in.

Runs after pipeline.build. Reads site/data, writes site/data/profiles/, and adds
`person_profile_id` / `org_profile_id` to testimony rows in
site/data/bills/LD*.json and site/data/testimony/index.json.

Merge rule: names are merged only when they match exactly after normalization
(case, punctuation, "The", "Inc."/"LLC" and similar suffixes, "&" vs "and").
Known variants that don't normalize together go in ORG_ALIASES below.
Officials are keyed by (first + last name, agency), so the same name at two
different agencies stays two profiles.
"""
import json, re, sys, unicodedata
from collections import Counter, defaultdict
from . import config
from .agencies import AGENCIES, OFFICIAL_TITLES

MIN_ORG_TESTIMONIES = 2
ORG_ALIASES: dict[str, str] = {
    # "variant as written": "canonical as written"   (add after reviewing profiles/review.json)
    "Alliance for Addiction & Mental Health Services, Maine": "Alliance for Addiction and Mental Health Services",
    "Alliance for Addiction and Mental Health Services, Maine": "Alliance for Addiction and Mental Health Services",
    # the two joint filings are one profile; Maine Tourism Association and HospitalityMaine on their own stay separate
    "HospitalityMaine and Maine Tourism Association": "HospitalityMaine/Maine Tourism Association",
}
JUNK_ORG = {"", "self", "myself", "none", "n a", "na", "citizen", "private citizen", "resident", "individual",
            "personal", "retired", "constituent", "concerned citizen", "parent", "voter", "student", "taxpayer",
            "member of the public", "public", "me", "na na", "not applicable", "unaffiliated", "independent"}
LEGISLATOR_ORG = re.compile(r"^(?:maine )?(?:state legislature|legislature|maine house of representatives|house of representatives|maine senate|senate"
                            r"|(?:house|senate) district \d+|state (?:representative|senator)( district \d+)?)$")
ORG_SUFFIXES =r"(,?\s+(inc|incorporated|llc|l l c|corp|corporation|co|company|ltd|pllc|pc|p a|lp|llp))+$"

def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:70]

def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^the\s+", "", s)
    s = re.sub(ORG_SUFFIXES, "", s).strip()
    return s

ORG_ALIASES_N = {norm(k): v for k, v in ORG_ALIASES.items()}   # aliases also match "&" vs "and", case and punctuation

def norm_agency(s: str) -> str:
    s = norm(s)
    return re.sub(r"^maine\s+", "", s)

def person_key(name: str) -> str | None:
    """First + last name, dropping middle names/initials, honorifics and suffixes."""
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    n = re.sub(r"\b(dr|mr|mrs|ms|mx|rev|hon|sen|rep|senator|representative|commissioner)\.?\s+", "", n, flags=re.I)
    n = re.sub(r",?\s+(jr|sr|ii|iii|iv|esq|phd|md|jd|rn|mph|mba)\.?$", "", n.strip(), flags=re.I)
    parts = [p for p in re.split(r"[\s,]+", n) if re.match(r"^[A-Za-z][A-Za-z'\-]+$", p) and len(p) > 1]
    if len(parts) < 2:
        return None
    return f"{parts[0].lower()} {parts[-1].lower()}"

# ---- agency matching
AG_BY_ID = {a["id"]: a for a in AGENCIES}
AG_ALIASES = sorted(((norm_agency(x), a["id"]) for a in AGENCIES for x in [a["name"], *a["aliases"]] if norm_agency(x)),
                    key=lambda kv: -len(kv[0]))   # longest first, so "department of education" beats "education"

def match_agency(text: str | None, max_start: int | None = None) -> str | None:
    t = " " + norm_agency(text or "") + " "
    for alias, aid in AG_ALIASES:
        # Acronyms and one-word aliases only as the whole string: "Maine Housing" normalizes to "housing",
        # which must not match "Community Housing of Maine".
        if len(alias) <= 4 or " " not in alias:
            if t.strip() == alias:
                return aid
            continue
        if f" {alias} " in t:
            return aid
    return None

TITLE_RE = "|".join(sorted((re.escape(t) for t in OFFICIAL_TITLES), key=len, reverse=True))
AGENCY_CLAUSE = r"(?P<agency>[A-Z][^.;()\u2014\n]{3,110})"   # up to the end of the clause
INTRO_PATTERNS = [
    # "I serve as President of the Maine Connectivity Authority"; "I am the Commissioner of the Department of ..."
    re.compile(r"(?:I serve as|I am|I'm|in my (?:role|capacity) as|I currently serve as|and I am|and I serve as)\s+(?:the\s+)?(?:Maine'?s?\s+)?"
               r"(?P<title>(?:[A-Z][\w.'\-]*\s+){0,4}?(?:" + TITLE_RE + r"))\s+(?:of|for|at|with)\s+(?:the\s+)?" + AGENCY_CLAUSE),
    # "My name is Jane Doe, Commissioner of the Department of X"
    re.compile(r"My name is\s+[A-Z][\w.'\- ]{2,60}?,\s*(?:the\s+)?(?P<title>(?:[A-Z][\w.'\-]*\s+){0,4}?(?:" + TITLE_RE + r"))\s+(?:of|for|at)\s+(?:the\s+)?" + AGENCY_CLAUSE),
    # constitutional officers named by title alone: "I'm Shenna Bellows, Secretary of State"
    re.compile(r"(?:My name is|I am|I'm)\s+[A-Z][\w.'\- ]{2,60}?,\s*(?:and I am\s+|and I serve as\s+)?(?:Maine'?s\s+)?(?:the\s+)?(?:Maine\s+)?"
               r"(?P<title>Secretary of State|Attorney General|State Treasurer|State Auditor|Public Advocate)(?P<agency>)"),
]
# A clause like "Executive Director of Maine Equal Justice, and we work with the Department of ..." must not
# match the department, so cut the clause where a new thought starts, and require the body near its start.
CLAUSE_STOP = re.compile(r",\s+(?:and|where|which|who|but)\b|\s+and\s+(?:I|we|our|my|am|have)\b|\s+(?:where|which|who|because)\b")
LEGISLATOR_TITLE = re.compile(r"\b(Senator|Representative|Speaker|Senate President)\b")

# ---- titles: which officials get a person profile (decided Nov 2026)
# Senior roles only: commissioners and their deputy/associate/acting commissioners; heads of independent agencies
# and authorities (executive director, president, CEO, chancellor, superintendent of a bureau); constitutional
# officers and other statutory office heads; commission chairs and members; bureau or office directors. Staff
# (policy director, legislative liaison, general counsel, analyst, deputy director, assistant AG, ...) get no
# person profile; their letters still count on the agency's profile.
_QUAL = r"(?:Acting|Interim|Senior|Deputy|Associate|Assistant|Executive|Chief|Bureau|Division|Program|Policy|Legislative|Communications|Government Relations|Medical|Field Services|EMS|Vice|Special|Staff|State)"
TITLE_WORDS = sorted({*OFFICIAL_TITLES, "Analyst", "Policy Advisor", "Legislative Director", "Commission Member", "Manager", "Coordinator",
                      "Specialist", "Advisor", "Counsel", "Liaison", "Fire Marshal", "Chief of the Maine State Police"}, key=len, reverse=True)
_TW = "|".join(re.escape(t) for t in TITLE_WORDS)
# a title with its "of ..." tail: "Director of Policy and Legislative Affairs", "Director of the Bureau of Parks and Lands"
# a title with its "of ..." tail: "Director of Policy and Legislative Affairs", "Director of the Maine Center for Disease Control"
_TITLE = rf"(?:{_QUAL}\s+)*(?:{_TW})(?:\s+(?:of|for)\s+(?:the\s+)?(?:Maine\s+)?[A-Z][\w&'\-]*(?:\s+(?:and(?=\s+[A-Z][a-z])|of|the|for(?!\s+the\b)|&|[A-Z][\w&'\-]*)){{0,7}})?"
# senior if the head ends in one of these (and has no staff words), and the "of ..." part is not a staff area
_SENIOR_HEAD = re.compile(r"\b(?:Commissioner|Executive Director|President|Chief Executive Officer|CEO|Chancellor|Superintendent|"
                          r"Public Advocate|State Court Administrator|Fire Marshal|Colonel|Chair|Chairman|Chairwoman|Chairperson|"
                          r"Commission Member|Director)$")
_TOP_OFFICES = re.compile(r"^(?:Secretary of State|Attorney General|State Treasurer|State Auditor|Chief Justice|Chief of the Maine State Police)$")
_STAFF_HEAD = re.compile(r"\b(Policy|Legislative|Liaison|Counsel|Analyst|Communications|Government|Advisor|Manager|Coordinator|Specialist|"
                         r"Deputy Director|Assistant|Associate Director|Senior Director|Program Director|Medical Director|Field Services|"
                         r"Vice Chancellor|Vice President|Deputy Attorney General|Deputy Secretary|Officer)\b", re.I)
_STAFF_TAIL = re.compile(r"^(?:the\s+)?(?:Policy|Legislative|Government|Communications|Public Affairs|Public Relations|External|Strategy|Strategic|"
                         r"Actuarial|Constituent|Legal Affairs)\b|Legislative Affairs|Government Relations|Public Affairs|Public Relations|Communications", re.I)
# rank for choosing among a person's senior titles (lower = higher rank)
_RANK = [("Attorney General|Secretary of State|State Treasurer|State Auditor|Chief Justice|Public Advocate|State Court Administrator", 0),
         ("^(?:Acting |Interim )?Commissioner", 1),
         ("Chancellor|President|CEO|Chief Executive Officer|Executive Director|Superintendent|Colonel|Fire Marshal|Chief of", 2),
         ("Deputy Commissioner|Associate Commissioner", 3), ("Chair", 4), ("Director", 5)]
_HONORIFIC = re.compile(r"^(dr|mr|mrs|ms|mx|rev|hon)\.?$", re.I)
_BAD = "�"   # a character the PDF extractor couldn't read

def _fold(s: str) -> str:
    """ASCII-fold, keeping U+FFFD."""
    return "".join(c if c == _BAD else unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode() for c in s or "")

def _split_title(t: str) -> tuple[str, str]:
    m = re.match(r"^(.*?)\s+(?:of|for)\s+(.*)$", t)
    if not m or re.fullmatch(r"(?:Chief|Secretary|State|Attorney)", m.group(1)):
        return t, ""
    return m.group(1), m.group(2)

def clean_title(title: str | None, person: str | None = None) -> str | None:
    """'Erik Jorgensen Senior Director' -> 'Senior Director': drop words of the person's own name and stray initials."""
    if not title:
        return None
    t = re.sub(r"\s+", " ", _fold(title)).strip(" ,.;:")
    name_words = {w.lower().strip(".,") for w in re.split(r"[\s\-]+", _fold(person or "")) if w}
    words = t.split()
    while words and (words[0].lower().strip(".,") in name_words or re.fullmatch(r"[A-Z]\.?", words[0])):
        words.pop(0)
    t = " ".join(words)
    while True:   # dangling "... of the Maine", "... Subject" (a memo's next line)
        u = re.sub(r"\s+(?:of|for|the|and|&|Maine|Maine Department|Subject|Re|Date|Testimony)$", "", t)
        if u == t: break
        t = u
    return t or None

def is_senior(title: str | None) -> bool:
    if not title:
        return False
    if _TOP_OFFICES.match(title):
        return True
    head, tail = _split_title(title)
    return bool(_SENIOR_HEAD.search(head)) and not _STAFF_HEAD.search(head) and not (tail and _STAFF_TAIL.search(tail))

def _is_staff(title: str) -> bool:
    head, tail = _split_title(title)
    return bool(_STAFF_HEAD.search(head) or (tail and _STAFF_TAIL.search(tail)))

def title_rank(title: str) -> int:
    return next((r for pat, r in _RANK if re.search(pat, title)), 9)

def best_title(titles) -> tuple[str | None, bool]:
    """(title to show, senior?). A bare 'Director' next to a staff title ('Director of Policy ...') is staff."""
    ts = [t for t in titles if t]
    if not ts:
        return None, False
    senior = [t for t in ts if is_senior(t)]
    staff = [t for t in ts if _is_staff(t)]
    if senior and staff and all(re.fullmatch(r"(?:Acting |Interim )?Director", t) for t in senior):
        senior = []
    if senior:
        c = Counter(senior)
        return sorted(c, key=lambda t: (title_rank(t), -c[t], -len(t)))[0], True
    return Counter(ts).most_common(1)[0][0], False

def display_title(title: str | None, agency_names=()) -> str | None:
    """'Commissioner of the Department of Environmental Protection' on the DEP profile -> 'Commissioner'."""
    if not title:
        return None
    head, tail = _split_title(title)
    words = lambda x: set(re.findall(r"[a-z]+", x.lower().replace("&", " and "))) - {"the", "maine", "of", "and", "for", "department", "office", "state"}
    # "Commissioner of the Department of Environmental Protection" / "Commissioner of Inland Fisheries & Wildlife" on that agency's profile
    if tail and any(words(n) and words(n) <= words(tail) | {"department"} and words(tail) <= words(n) for n in agency_names if n):
        return head
    return title

def titles_near_name(text: str | None, person: str) -> list[str]:
    """Titles written next to the person's own name ('Commissioner Sara Gagné-Holmes', a signature block
    'Aaron M. Frey / Attorney General', 'Nate Webb, Wildlife Division Director') or in their self-introduction."""
    if not text or not person:
        return []
    parts = [p.strip(".,") for p in re.split(r"\s+", _fold(person).strip()) if re.match(r"^[A-Za-z]", p) and not _HONORIFIC.match(p)]
    if len(parts) < 2:
        return []
    def pat(w):   # any letter may have come out of the PDF as U+FFFD
        return "".join(f"(?:{re.escape(c)}|{_BAD})" if c.isalpha() else re.escape(c) for c in w)
    name = rf"{pat(parts[0])}\s+(?:[A-Z]\.?\s+|[A-Z][a-z]+\s+){{0,2}}{pat(parts[-1])}"
    t = re.sub(r"[ \t]+", " ", _fold(text[:2500] + "\n" + text[-1500:]))
    out = []
    for m in re.finditer(rf"\b({_TITLE})\s+(?:Dr\.?\s+)?{name}\b", t):
        out.append(m.group(1))
    after = rf"\b{name}(?:,?\s+(?:Ph\.?D|M\.?D|J\.?D|Esq)\.?)?\s*(?:,|\n|\s-\s|\s–\s)\s*(?:the\s+)?((?:[A-Z][a-z]+\s+){{0,3}}?{_TITLE})"
    for m in re.finditer(after, t):
        out.append(m.group(1))
    intro = rf"(?:I serve as|I am|I'm|my role as|I work as)\s+(?:the\s+)?(?:Maine'?s?\s+)?((?:[A-Z][a-z]+\s+){{0,3}}?{_TITLE})"
    for m in re.finditer(intro, t[:1500]):
        out.append(m.group(1))
    return [x for x in (clean_title(x, person) for x in out) if x]

def official_from_text(text: str | None) -> tuple[str | None, str | None]:
    """Return (title, agency_id) from a letter's opening, or (None, None)."""
    head = re.sub(r"\s+", " ", (text or "")[:1200])
    for pat in INTRO_PATTERNS:
        for m in pat.finditer(head):
            clause = CLAUSE_STOP.split(m.group("agency") or "")[0]
            aid = match_agency(clause, max_start=30) or match_agency(m.group("title"))
            if aid and not LEGISLATOR_TITLE.search(m.group("title")):
                return re.sub(r"\s+", " ", m.group("title")).strip(), aid
    return None, None

# "Testified on its behalf": one entry per person. Name variants of one person on the same profile are merged
# (case, nicknames, middle names and initials, credentials, notes such as "(corrected)", repeated words, a one-letter
# first-name typo). Jr. and Sr. stay separate people; a name without a suffix joins a suffixed one only when the
# profile has just one suffix for that name. Every merge is logged to raw/people_merges.json for review.
NICKNAMES = {"josh": "joshua", "jeff": "jeffrey", "phil": "philip", "dan": "daniel", "danny": "daniel", "dave": "david",
    "mike": "michael", "bob": "robert", "rob": "robert", "bill": "william", "will": "william", "jim": "james", "jimmy": "james",
    "tom": "thomas", "tim": "timothy", "andy": "andrew", "drew": "andrew", "chris": "christopher", "kate": "katherine",
    "katie": "katherine", "kathy": "katherine", "liz": "elizabeth", "beth": "elizabeth", "betsy": "elizabeth", "sam": "samuel",
    "matt": "matthew", "nick": "nicholas", "steve": "stephen", "steven": "stephen", "pete": "peter", "greg": "gregory",
    "jen": "jennifer", "jenn": "jennifer", "jenny": "jennifer", "becky": "rebecca", "becca": "rebecca", "ben": "benjamin",
    "ed": "edward", "ted": "edward", "joe": "joseph", "tony": "anthony", "rick": "richard", "rich": "richard", "ron": "ronald",
    "don": "donald", "pat": "patrick", "sue": "susan", "meg": "margaret", "peggy": "margaret", "alex": "alexander",
    "zach": "zachary", "zack": "zachary", "nate": "nathan", "jon": "jonathan", "ken": "kenneth", "charlie": "charles",
    "deb": "debra", "kim": "kimberly", "jess": "jessica", "brad": "bradley", "max": "maxwell", "cat": "catherine", "norm": "norman"}
_CRED = r"(md|m d|do|d o|esq|phd|ph d|rn|dds|dmd|lcsw|mph|jd|np|pa c|cna|lpn|msw)"

def _person_parts(name: str):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"\(.*?\)|\"[^\"]*\"", " ", s)                      # "(corrected)", "(Maine Treasurer)", "Coralie \"Cokie\" Giles"
    s = re.sub(r"^\s*(dr|mr|mrs|ms|rev|hon)\.?\s+", "", s, flags=re.I)
    w = re.sub(r"[^a-z ]+", " ", s.lower()).split()
    while w and re.fullmatch(_CRED, w[-1]): w.pop()                  # credentials after the name
    if len(w) >= 2 and " ".join(w[-2:]) in ("m d", "d o", "ph d"): w = w[:-2]
    suffix = w.pop() if w and w[-1] in ("jr", "sr", "ii", "iii", "iv") else ""
    w = [x for i, x in enumerate(w) if i == 0 or x != w[i - 1]]      # "Ann Ann R. Robinson", "John Simoneau John Simoneau"
    if len(w) >= 4 and w[: len(w) // 2] == w[len(w) // 2:]: w = w[: len(w) // 2]
    if len(w) < 2: return None
    return NICKNAMES.get(w[0], w[0]), w[-1], suffix

def _close(a: str, b: str) -> bool:
    """Same first name: a prefix of 2+ letters (Kim / Kimberly), or one letter apart when 4+ long (Phillip / Philip)."""
    if a == b: return True
    if min(len(a), len(b)) >= 2 and (a.startswith(b) or b.startswith(a)): return True
    if min(len(a), len(b)) < 4: return False
    if len(a) == len(b): return sum(x != y for x, y in zip(a, b)) == 1
    lo, hi = sorted((a, b), key=len)
    return len(hi) - len(lo) == 1 and any(hi[:i] + hi[i + 1:] == lo for i in range(len(hi)))

FIRST_NAMES: Counter = Counter()   # first words of every name on every letter, set in run()

def merge_people(names: Counter, log: list, pid: str):
    groups = []                                                       # [first, last, suffix, Counter]
    for n, c in sorted(names.items(), key=lambda x: -x[1]):
        k = _person_parts(n)
        g = None
        if k:
            for x in groups:
                if x[1] == k[1] and x[2] == k[2] and _close(x[0], k[0]): g = x; break
        if g: g[3][n] += c
        else: groups.append([*(k or (n, n, "")), Counter({n: c})])
    # a plain name joins the one suffixed form of the same name (Robert Fisk -> Robert Fisk, Jr.), never when Jr. and Sr. both appear
    for g in [x for x in groups if not x[2]]:
        suf = [x for x in groups if x[2] and x[1] == g[1] and _close(x[0], g[0])]
        if len({x[2] for x in suf}) == 1 and len(suf) == 1:
            suf[0][3].update(g[3]); groups.remove(g)
    out = []
    for g in groups:
        forms = g[3]
        good = [f for f in forms if f != f.lower() and f != f.upper() and "(" not in f]
        # most-used form; on a tie, the one whose first name is more common across all letters (Justin over Juatin)
        show = max(good or list(forms), key=lambda f: (forms[f], FIRST_NAMES[(f.split() or [""])[0].lower()], len(f)))
        if not good: show = " ".join(w[:1].upper() + w[1:].lower() for w in show.split())   # only ever filed in one case
        if len(forms) > 1: log.append({"profile": pid, "shown_as": show, "merged": dict(forms)})
        out.append((show, sum(forms.values())))
    return [n for n, _ in sorted(out, key=lambda x: -x[1])]

def run():
    out = config.OUT / "profiles"; out.mkdir(parents=True, exist_ok=True)
    rows = json.loads((config.RAW / "testimony_index_full.json").read_text(encoding="utf-8"))
    FIRST_NAMES.update((r.get("name") or "").split()[0].lower() for r in rows if (r.get("name") or "").split())
    bills = {b["ld"]: b for b in json.loads((config.OUT / "bills" / "index.json").read_text(encoding="utf-8"))}
    legislator_keys = {person_key(l["name"]) for l in json.loads((config.OUT / "legislators.json").read_text(encoding="utf-8"))}

    # Guard: the organization field should not be holding towns. The Legislature's feed has a single free-text
    # field, so a row has an organization OR a town (testimony.split_org_town), never both; check that values
    # left in `organization` are not themselves towns that appear in `town` elsewhere.
    with_org = [r for r in rows if r.get("organization")]
    towns = {norm(r.get("town")) for r in rows if r.get("town")}
    looks_like_town = sum(1 for r in with_org if norm(r["organization"]) in towns)
    if with_org and looks_like_town / len(with_org) > 0.05 and "--force" not in sys.argv:
        sys.exit(f"{looks_like_town} of {len(with_org)} organization values are town names. "
                 "Check the field mapping in pipeline/testimony.py before building profiles (or pass --force).")

    texts_cache: dict[int, dict] = {}
    def text_for(ld, tid):
        if ld not in texts_cache:
            p = config.OUT / "testimony" / f"LD{ld}.json"
            texts_cache[ld] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        return texts_cache[ld].get(tid)

    org_rows, org_forms = defaultdict(list), defaultdict(Counter)
    off_rows, off_meta = defaultdict(list), {}
    agency_rows = defaultdict(list)

    for r in rows:
        raw_org = ORG_ALIASES.get(r.get("organization") or "") or ORG_ALIASES_N.get(norm(r.get("organization")), r.get("organization"))
        n_org = norm(raw_org)
        aid = match_agency(raw_org) if raw_org else None
        txt = text_for(r["ld"], r["id"]) if r.get("has_text") else None
        title, aid_text = official_from_text(txt) if txt else (None, None)
        # The letter's text may name an agency only when the organization field doesn't name a different body
        # ("Industrial Energy Consumer Group" writing about Efficiency Maine is not Efficiency Maine).
        if not aid and raw_org and n_org not in JUNK_ORG and not LEGISLATOR_ORG.match(n_org):
            aid_text = None
        aid = aid or aid_text
        pk = person_key(r.get("name") or "")
        if aid:
            agency_rows[aid].append(r)
            if pk and pk not in legislator_keys:
                key = (pk, aid)
                off_rows[key].append(r)
                m = off_meta.setdefault(key, {"names": Counter(), "titles": Counter()})
                m["names"][r.get("name")] += 1
                for tt in [clean_title(title, r.get("name"))] + titles_near_name(txt, r.get("name") or ""):
                    if tt: m["titles"][tt] += 1
            continue
        if raw_org and LEGISLATOR_ORG.match(n_org):
            continue                   # legislators testifying: they have legislator pages, not an organization profile
        if raw_org and n_org not in JUNK_ORG and n_org not in towns and n_org != norm(r.get("name")) and len(n_org) > 2:
            org_rows[n_org].append(r)
            org_forms[n_org][raw_org.strip()] += 1

    profiles, tag_person, tag_org = [], {}, {}

    def testimony_list(rs, with_names):
        items = []
        for r in sorted(rs, key=lambda x: (x.get("date") or "", x["ld"])):
            it = {"testimony_id": r["id"], "ld": r["ld"], "bill_title": (bills.get(r["ld"]) or {}).get("title"),
                  "position": r.get("position"), "position_source": r.get("position_source"), "date": r.get("date"), "url": r.get("url")}
            if with_names: it["name"] = r.get("name")
            items.append(it)
        return items

    def counts(rs):
        c = Counter((r.get("position") or "unknown") for r in rs)
        return {k: c.get(k, 0) for k in ("support", "neutral", "oppose", "unknown")}

    # person profiles only for senior roles (see _SENIOR); everyone else stays on the agency profile only
    staff_titles = Counter()
    for key in list(off_rows):
        m = off_meta[key]
        title, senior = best_title(m["titles"].elements())
        if senior:
            m["title"] = title
        else:
            staff_titles[title or "(no title found)"] += 1
            del off_rows[key]
    print(f"officials without a senior title (no person profile): {sum(staff_titles.values())}; "
          f"titles: {staff_titles.most_common(12)}")

    for aid, rs in agency_rows.items():
        a = AG_BY_ID[aid]; pid = f"a-{aid}"
        officials = [f"o-{slug(k[0])}-{k[1]}" for k in off_rows if k[1] == aid]
        prof = {"id": pid, "type": "agency", "name": a["name"], "aliases": a["aliases"], "testimony_count": len(rs),
                "bill_count": len({r["ld"] for r in rs}), "positions": counts(rs), "officials": officials,
                "testimony": testimony_list(rs, True)}
        profiles.append(prof)
        for r in rs: tag_org[r["id"]] = pid

    for (pk, aid), rs in off_rows.items():
        pid = f"o-{slug(pk)}-{aid}"; m = off_meta[(pk, aid)]
        prof = {"id": pid, "type": "official", "name": m["names"].most_common(1)[0][0],
                "title": display_title(m.get("title"), [AG_BY_ID[aid]["name"], *AG_BY_ID[aid]["aliases"]]),
                "titles": [t for t, _ in m["titles"].most_common()], "agency_id": f"a-{aid}", "agency_name": AG_BY_ID[aid]["name"],
                "testimony_count": len(rs), "bill_count": len({r["ld"] for r in rs}), "positions": counts(rs),
                "testimony": testimony_list(rs, False)}
        profiles.append(prof)
        for r in rs: tag_person[r["id"]] = pid

    review, people_log = [], []
    for n_org, rs in org_rows.items():
        if len(rs) < MIN_ORG_TESTIMONIES:
            continue
        pid = f"org-{slug(n_org)}"
        forms = org_forms[n_org]
        prof = {"id": pid, "type": "organization", "name": forms.most_common(1)[0][0], "aliases": [f for f, _ in forms.most_common()][1:6],
                "testimony_count": len(rs), "bill_count": len({r["ld"] for r in rs}), "positions": counts(rs),
                "people": merge_people(Counter(r.get("name") for r in rs if r.get("name")), people_log, pid)[:12],
                "testimony": testimony_list(rs, True)}
        profiles.append(prof)
        for r in rs: tag_org[r["id"]] = pid
        if len(forms) > 1: review.append({"id": pid, "forms": dict(forms)})

    # write profiles
    for p in out.glob("*.json"): p.unlink()
    for p in profiles:
        (out / f"{p['id']}.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    idx = sorted(({k: p.get(k) for k in ("id", "type", "name", "title", "agency_id", "agency_name", "testimony_count", "bill_count", "positions")} for p in profiles),
                 key=lambda p: -p["testimony_count"])
    (out / "index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    config.RAW.mkdir(parents=True, exist_ok=True)
    (config.RAW / "people_merges.json").write_text(json.dumps(people_log, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"profiles: {len(people_log)} people on organization profiles merged from name variants (raw/people_merges.json)")
    (config.RAW / "profiles_review.json").write_text(json.dumps(sorted(review, key=lambda x: -sum(x["forms"].values())), indent=1, ensure_ascii=False), encoding="utf-8")

    # tag testimony rows (idempotent)
    for r in rows:
        r["person_profile_id"] = tag_person.get(r["id"]); r["org_profile_id"] = tag_org.get(r["id"])
    (config.RAW / "testimony_index_full.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    for p in (config.OUT / "bills").glob("LD*.json"):
        b = json.loads(p.read_text(encoding="utf-8"))
        for t in b.get("testimony", []):
            t["person_profile_id"] = tag_person.get(t["id"]); t["org_profile_id"] = tag_org.get(t["id"])
        p.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")

    by = Counter(p["type"] for p in profiles)
    print(f"profiles: {by.get('organization',0)} organizations, {by.get('agency',0)} agencies, {by.get('official',0)} officials; "
          f"{len(tag_person)} testimonies linked to officials, {len(tag_org)} to organizations/agencies. "
          f"Name variants to review: {config.RAW / 'profiles_review.json'}")

if __name__ == "__main__":
    run()
