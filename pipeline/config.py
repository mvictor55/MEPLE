"""
MEPLE data pipeline — configuration and the URL patterns we rely on.

Every pattern below was observed on legislature.maine.gov on 2026-11-06 unless
marked UNVERIFIED. Verify the UNVERIFIED ones first when the pipeline runs with
network access (see README.md, "First run").
"""
from pathlib import Path

SESSION = "132"                      # Maine Legislature number (snum)
LEGISLATURE_LABEL = "132nd Maine Legislature"
YEARS = "2025–2026"

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "pipeline" / "raw"       # cached downloads (gitignored)
OUT = ROOT / "site" / "data"          # what the site reads

BASE = "https://legislature.maine.gov"

# --- Bill status page (verified): title, sponsor, committee, committee action
#     history, divided reports with signers, amendments, fiscal notes,
#     final disposition, affected statutes.
BILL_STATUS = BASE + "/legis/bills/display_ps.asp?LD={ld}&snum={snum}"

# --- Public hearings / work sessions for one paper (verified 2026-11-06): JSON
#     list loaded by XHR on the status page; publicHearing=-1 marks a hearing.
PHWS = BASE + "/legis/bills/getPHWS.asp?paper={paper}&snum={snum}"

# --- Bill directory (verified link; page structure UNVERIFIED): list of LDs.
BILL_DIRECTORY = BASE + "/legis/bills/billdirectory_ps.asp?snum={snum}&ldFrom={ld_from}"

# --- Printed documents (verified): item=1 is the bill as printed; later items
#     are amendments (item numbers appear on the status page).
BILL_PDF = BASE + "/legis/bills/getPDF.asp?paper={paper}&item={item}&snum={snum}"
# HTML version of the bill text (UNVERIFIED — common pattern; check one bill).
BILL_HTML = BASE + "/legis/bills/bills_{snum}nd/billtexts/{paper}{item:02d}.asp"

# --- Fiscal notes (verified).
FISCAL_PDF = BASE + "/legis/bills/bills_{snum}nd/fiscalpdfs/FN{ld:04d}{n:02d}.pdf"
FISCAL_HTML = BASE + "/legis/bills/bills_{snum}nd/fiscalnotes/FN{ld:04d}{n:02d}.htm"

# --- Committee docket / hearings (verified link).
COMMITTEE_SCHEDULE = BASE + "/legis/bills/phwksched.html?CODE={code}&snum={snum}"
HEARINGS_BY_COMMITTEE = BASE + "/bills/phwkSched_ps.asp?snum={snum}&PID=0"

# --- Testimony. The Legislature's testimony pages are JavaScript-rendered
#     ("Gathering testimony data, one moment please...") and the underlying
#     endpoint is UNVERIFIED. Discover it on first run with
#     pipeline/discover_testimony_endpoint.py (Playwright, logs XHR requests).
TESTIMONY_LANDING = BASE + "/legis/bills/testimony.html?snum={snum}&PID=1456"
TESTIMONY_BY_CMTE_STUB = BASE + "/legis/bills/TestimonyByCmte-stub.asp?snum={snum}&PID=1456"
TESTIMONY_BY_DATE_STUB = BASE + "/legis/bills/TestimonyByDate-stub.asp?snum={snum}&PID=1456"
# Individual testimony documents have historically been served from
# /legis/bills/getTestimonyDoc.asp?id=NNNN (UNVERIFIED for the 132nd).
TESTIMONY_DOC = BASE + "/legis/bills/getTestimonyDoc.asp?id={id}"

# --- Rosters (verified 2026-11-06; see pipeline/rosters.py).
HOUSE_ROSTER_CSV = BASE + "/house/Home/ExportActiveMembers?format=csv"
HOUSE_ROSTER = BASE + "/house/MemberProfiles/List?sort=alpha"
SENATE_ROSTER = BASE + "/senate/district-listing/9526"
COMMITTEE_LIST = BASE + "/committee/#Committees"

# --- Roll calls (UNVERIFIED paths).
HOUSE_ROLLCALLS = BASE + "/house/house/RollCalls"
SENATE_ROLLCALLS = BASE + "/senate/roll-call-votes"

# --- LegiScan (free API key; datasets are CC BY 4.0). Set LEGISCAN_KEY in env.
LEGISCAN_API = "https://api.legiscan.com/?key={key}&op={op}"
# Open States v3 (free key). Set OPENSTATES_KEY in env.
OPENSTATES_API = "https://v3.openstates.org"

# Joint standing committees of the 132nd and the codes the Legislature uses in
# phwksched.html?CODE=... (all 17 confirmed 2026-11-06 against the CODE=
# links on bill status pages and against getTestyCmte.asp).
COMMITTEES = {
    "ACF": "Agriculture, Conservation and Forestry",
    "AFA": "Appropriations and Financial Affairs",
    "CJPS": "Criminal Justice and Public Safety",
    "EDU": "Education and Cultural Affairs",
    "EUT": "Energy, Utilities and Technology",
    "ENR": "Environment and Natural Resources",
    "HHS": "Health and Human Services",
    "HCIFS": "Health Coverage, Insurance and Financial Services",
    "HED": "Housing and Economic Development",
    "IFW": "Inland Fisheries and Wildlife",
    "JUD": "Judiciary",
    "LAB": "Labor",
    "MAR": "Marine Resources",
    "SLG": "State and Local Government",
    "TAX": "Taxation",
    "TRA": "Transportation",
    "VLA": "Veterans and Legal Affairs",
}

USER_AGENT = "MEPLE data pipeline (civic, non-commercial; contact: see repo README)"
REQUEST_DELAY_SECONDS = 0.75   # be polite to legislature.maine.gov
