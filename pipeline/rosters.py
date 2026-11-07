"""
Legislators and committee rosters from legislature.maine.gov.

Sources (verified 2026-11-06):
  House: /house/Home/ExportActiveMembers?format=csv
         Name, Party, District, County, Seat, Email, Phone, Residence (town),
         City, Zip, Position (leadership), Committees ("(Chair) Ethics|Judiciary")
         /house/MemberProfiles/List?sort=alpha -> profile URL for each member; it
         also keeps former members, marked "Resigned"/"Deceased" (the date is on
         the profile: "Resigned: Oct 31, 2025"), and tribal representatives, who
         are not in the CSV export
  Senate: /senate/district-listing/9526 -> district, name, party, county, profile link
          each profile (/districtNN) -> towns in the district, committee assignments
          ("Judiciary (Chair)")

    python -m pipeline.rosters [--debug]   # --debug keeps the raw pages in pipeline/raw/rosters/

LegiScan's people file stays the spine for ids; build.py joins these rows on
(chamber, last name).
"""
import csv, io, json, re, sys
from bs4 import BeautifulSoup
from . import config, fetch

def _text(el): return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""

def _norm_cname(name: str) -> str:
    # Pages differ on the Oxford comma ("Energy, Utilities, and Technology")
    n = re.sub(r"\s+", " ", name).strip().lower()
    n = re.sub(r"^joint standing committee on ", "", n)
    return re.sub(r",? and ", " and ", n).replace(",", "")

_COMMITTEE_BY_NAME = {_norm_cname(v): k for k, v in config.COMMITTEES.items()}

def _committee_code(name: str) -> str | None:
    return _COMMITTEE_BY_NAME.get(_norm_cname(name))

_LEADERSHIP = re.compile(r"(?<![A-Za-z])(President|Speaker|Leader|Whip)(?![A-Za-z])")

def _house_list() -> list[dict]:
    """Rows of the House list page: name, title, status ('Resigned'/'Deceased'/None),
    party, district, town, committees, profile url."""
    soup = BeautifulSoup(fetch.get(config.HOUSE_ROSTER, refresh=True), "lxml")
    heads = [_text(th) for th in soup.select("thead th")]
    out = []
    for tr in soup.select("tbody tr"):
        cells = dict(zip(heads, tr.find_all("td")))
        a = cells["Name"].select_one("a[href*='MemberProfiles/Details']") if "Name" in cells else None
        if not a:
            continue
        parts = [x.strip() for x in cells["Name"].get_text("|", strip=True).split("|") if x.strip()]
        status = next((x for x in parts if x in ("Resigned", "Deceased")), None)
        coms = cells["Committees"].get_text("|", strip=True).split("|") if "Committees" in cells else []
        out.append({"name": _text(a), "title": parts[1] if len(parts) > 1 else None, "status": status,
                    "party": _text(cells.get("Party")) or None, "district": _text(cells.get("District")) or None,
                    "town": _text(cells.get("Legal Residence")) or None,
                    "committees": [c.strip() for c in coms if c.strip() and c.strip() != "None"],
                    "url": config.BASE + a["href"] if a["href"].startswith("/") else a["href"]})
    return out

_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

def _left_on(url: str) -> str | None:
    """'Resigned: Oct 31, 2025' / 'Deceased: Jan 10, 2026' on a House profile -> ISO date."""
    t = re.sub(r"\s+", " ", BeautifulSoup(fetch.get(url, refresh=True), "lxml").get_text(" "))
    m = re.search(r"(?:Resigned|Deceased)\s*:\s*([A-Z][a-z]{2})[a-z]*\.? (\d{1,2}), (\d{4})", t)
    if not m or m.group(1) not in _MONTHS:
        return None
    return f"{m.group(3)}-{_MONTHS.index(m.group(1)) + 1:02d}-{int(m.group(2)):02d}"

def house_former(listing: list[dict]) -> list[dict]:
    """Members who left during the session; the list page keeps them, marked."""
    return [{"chamber": "House", "name": r["name"], "party": r["party"], "district": r["district"],
             "town": r["town"], "status": r["status"].lower(), "served_until": _left_on(r["url"]), "url": r["url"]}
            for r in listing if r["status"]]

def house_members(listing: list[dict]) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(fetch.get(config.HOUSE_ROSTER_CSV, refresh=True).lstrip("\ufeff"))))
    urls = {r["name"].lower(): r["url"] for r in listing}
    out = []
    for r in rows:
        name = (r.get("Name") or "").strip()
        if not name:
            continue
        committees, other = [], []
        for c in (r.get("Committees") or "").split("|"):
            c = c.strip()
            if not c:
                continue
            chair = "(Chair)" in c
            cname = re.sub(r"\((Chair|Ranking)\)", "", c).strip()
            code = _committee_code(cname)
            if code:
                committees.append({"code": code, "role": "House Chair" if chair else "Member"})
            else:
                other.append(cname + (" (Chair)" if chair else ""))
        out.append({"chamber": "House", "name": name, "district": (r.get("District") or "").strip() or None,
                    "party": (r.get("Party") or "?")[0] if r.get("Party") else None,
                    "town": (r.get("Residence") or r.get("City") or "").strip() or None,
                    "county": (r.get("County") or "").strip() or None,
                    "role": (r.get("Position") or "").strip() or None,
                    "email": (r.get("Email") or "").strip() or None,
                    "committees": committees, "other_committees": other,
                    "url": urls.get(name.lower())})
    # Tribal representatives (non-voting; LegiScan district "TRIBE") are on the list page but not in the CSV.
    in_csv = {m["name"].lower() for m in out}
    for r in listing:
        if not r["status"] and r["name"].lower() not in in_csv and "Tribal" in (r["title"] or ""):
            coms = [{"code": c, "role": "Member"} for c in (_committee_code(x) for x in r["committees"]) if c]
            out.append({"chamber": "House", "name": r["name"], "district": "TRIBE", "party": None,
                        "town": r["town"], "county": None, "role": r["title"], "email": None,
                        "committees": coms, "other_committees": [], "url": r["url"]})
    return out

def senate_members() -> list[dict]:
    soup = BeautifulSoup(fetch.get(config.SENATE_ROSTER, refresh=True), "lxml")
    out = []
    for p in soup.find_all("p"):
        t = _text(p)
        m = re.match(r"District (\d+)\s*\(Map\)\s*-\s*Senator\s+(.+?)\s*\(\s*([A-Z])\s*-\s*([A-Za-z .]+?)\s*\)", t)
        if not m:
            continue
        link = next((a for a in p.find_all("a") if "Senator" in _text(a)), None)
        url = (config.BASE + link["href"] if link["href"].startswith("/") else link["href"]) if link else None
        s = {"chamber": "Senate", "name": m.group(2).strip(), "district": m.group(1), "party": m.group(3),
             "county": m.group(4).strip(), "town": None, "towns": [], "role": None,
             "committees": [], "other_committees": [], "url": url}
        s["name"] = re.sub(r'\s*"[^"]+"\s*', " ", s["name"]).strip()   # Harold "Trey" L. Stewart -> Harold L. Stewart
        if url:
            try:
                _senate_profile(s, fetch.get(url))
            except Exception as e:
                s["profile_error"] = str(e)[:120]
        out.append(s)
    return out

def _senate_profile(s: dict, html: str):
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "nav", "header", "footer"]):
        t.extract()
    lines = [l.strip() for l in soup.get_text("\n").splitlines() if l.strip()]
    txt = "\n".join(lines)
    # "In Cumberland County: Cape Elizabeth; South Portland; and part of Scarborough."
    towns = []
    for m in re.finditer(r"In ([A-Za-z ]+) County:\s*([^\n]+)", txt):
        for part in re.split(r";\s*", m.group(2).rstrip(".")):
            part = re.sub(r"^(and )?(part of |the )?", "", part.strip()).strip(" .")
            part = re.sub(r"^and ", "", part)
            if part and len(part) < 60:
                towns.append(part)
    s["towns"] = towns
    # "Committee Assignment\n:\nJudiciary (Chair)\n..." or "Committee Assignments:\n..."
    m = re.search(r"Committee Assignments?\s*:\s*\n(.+?)(?:\nMaine Government|\Z)", txt, re.S)
    for line in (m.group(1).splitlines() if m else []):
        chair = "(Chair)" in line
        cname = re.sub(r"\((Chair|Ranking)\)", "", line).strip()
        code = _committee_code(cname)
        if code:
            s["committees"].append({"code": code, "role": "Senate Chair" if chair else "Member"})
        elif _LEADERSHIP.search(cname) and not s.get("role"):
            s["role"] = cname          # leadership posts are listed with the committees
        elif cname:
            s["other_committees"].append(cname + (" (Chair)" if chair else ""))

def maine_towns() -> list[dict]:
    """Every municipality and township, with county, from the House district-towns list.
    testimony.py uses it to tell a town typed into the 'organization' field from an organization."""
    soup = BeautifulSoup(fetch.get(config.HOUSE_DISTRICT_TOWNS, refresh=True), "lxml")
    heads = [_text(th) for th in soup.select("thead th")]
    seen, out = set(), []
    for tr in soup.select("tbody tr"):
        c = dict(zip(heads, (_text(td) for td in tr.find_all("td"))))
        # "Portland Part": a town split between House districts
        t = re.sub(r"\s*(?:\((?:part|pt\.?)\)|\bPart)\s*$", "", c.get("Town") or "", flags=re.I).strip()
        if t and t.lower() not in seen:
            seen.add(t.lower()); out.append({"town": t, "county": c.get("County")})
    return out

def committee_rosters(house: list[dict], senate: list[dict]) -> dict[str, dict]:
    """code -> {url, members: [{name, chamber, role}]}; role in {'Senate Chair','House Chair','Member'}"""
    rosters = {code: {"url": config.COMMITTEE_SCHEDULE.format(code=code, snum=config.SESSION), "members": []} for code in config.COMMITTEES}
    for m in senate + house:
        for c in m["committees"]:
            rosters[c["code"]]["members"].append({"chamber": m["chamber"], "name": m["name"], "role": c["role"]})
    return rosters

if __name__ == "__main__":
    debug = "--debug" in sys.argv
    d = config.RAW / "rosters"; d.mkdir(parents=True, exist_ok=True)
    if debug:
        (d / "house.csv").write_text(fetch.get(config.HOUSE_ROSTER_CSV), encoding="utf-8")
        (d / "house_list.html").write_text(fetch.get(config.HOUSE_ROSTER), encoding="utf-8")
        (d / "senate.html").write_text(fetch.get(config.SENATE_ROSTER), encoding="utf-8")
        print("raw pages written to", d)
    listing = _house_list()
    house, senate, former = house_members(listing), senate_members(), house_former(listing)
    data = {"house": house, "senate": senate, "former": former, "committees": committee_rosters(house, senate),
            "towns": maine_towns()}
    print(f"towns: {len(data['towns'])} municipalities/townships")
    for f in former:
        print(f"former member: {f['name']} (House {f['district']}) {f['status']} {f['served_until']}")
    (d / "rosters.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    sizes = {k: len(v["members"]) for k, v in data["committees"].items()}
    print(f"house={len(house)} senate={len(senate)} committees={len(data['committees'])} members per committee={sizes}")
    # Joint standing committees seat 3 senators and (in the 132nd) 9 House members; allow vacancies.
    if len(house) < 140 or len(senate) < 34 or min(sizes.values()) < 10:
        sys.exit("roster counts look wrong; inspect pipeline/raw/rosters/ with --debug")
