"""
Legislators and committee rosters from legislature.maine.gov.

The roster pages exist (House alphabetical list, Senate home, committee list)
but their table structure has not been inspected yet. On first run:
    python -m pipeline.rosters --debug
dumps the raw HTML so the selectors below can be fixed in one pass.

Strategy: LegiScan's people file gives names, party, chamber, district, and a
stable id for all 186 members. This module adds towns, leadership roles,
committee assignments (with chair flags) and the official profile URL, keyed
by (chamber, last name, district) to join onto the LegiScan ids.
"""
import json, re, sys
from bs4 import BeautifulSoup
from . import config, fetch

def _text(el): return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""

def house_members() -> list[dict]:
    html = fetch.get(config.HOUSE_ROSTER)
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select("a[href*='MemberProfiles']"):
        name = _text(a)
        if not name or len(name) < 4:
            continue
        row = a.find_parent("tr") or a.parent
        t = _text(row)
        dist = re.search(r"District\s*(\d+)", t)
        party = re.search(r"\b(Democrat|Republican|Independent|Unenrolled|Green)\b", t)
        town = re.search(r"\(([A-Z][A-Za-z .'-]+)\)", t)
        out.append({"chamber": "House", "name": name, "district": dist.group(1) if dist else None,
                    "party": party.group(1)[0] if party else None, "town": town.group(1) if town else None,
                    "url": a["href"] if a["href"].startswith("http") else config.BASE + a["href"]})
    return out

def senate_members() -> list[dict]:
    html = fetch.get(config.SENATE_ROSTER)
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select("a[href*='senator'], a[href*='Senator']"):
        name = _text(a)
        if not name or len(name) < 4:
            continue
        t = _text(a.parent)
        dist = re.search(r"District\s*(\d+)", t)
        out.append({"chamber": "Senate", "name": name, "district": dist.group(1) if dist else None,
                    "url": a["href"] if a["href"].startswith("http") else config.BASE + a["href"]})
    return out

def committee_rosters() -> dict[str, list[dict]]:
    """code -> [{name, chamber, role}] ; role in {'Senate Chair','House Chair','Member'}"""
    html = fetch.get(config.COMMITTEE_LIST)
    soup = BeautifulSoup(html, "lxml")
    rosters = {}
    for code, cname in config.COMMITTEES.items():
        link = soup.find("a", string=re.compile(re.escape(cname), re.I))
        if not link:
            continue
        href = link["href"] if link["href"].startswith("http") else config.BASE + link["href"]
        chtml = fetch.get(href)
        cs = BeautifulSoup(chtml, "lxml")
        members = []
        for li in cs.select("li, tr, p"):
            t = _text(li)
            m = re.match(r"^(Senator|Representative|Sen\.|Rep\.)\s+([A-Z][^,(]+?)(?:\s*[,(]\s*(Senate Chair|House Chair|Chair))?\s*$", t)
            if m:
                members.append({"chamber": "Senate" if m.group(1).startswith("Sen") else "House", "name": m.group(2).strip(), "role": m.group(3) or "Member"})
        rosters[code] = {"url": href, "members": members}
    return rosters

if __name__ == "__main__":
    debug = "--debug" in sys.argv
    d = config.RAW / "rosters"; d.mkdir(parents=True, exist_ok=True)
    if debug:
        (d / "house.html").write_text(fetch.get(config.HOUSE_ROSTER))
        (d / "senate.html").write_text(fetch.get(config.SENATE_ROSTER))
        (d / "committees.html").write_text(fetch.get(config.COMMITTEE_LIST))
        print("raw HTML written to", d)
    data = {"house": house_members(), "senate": senate_members(), "committees": committee_rosters()}
    (d / "rosters.json").write_text(json.dumps(data, indent=1))
    print(f"house={len(data['house'])} senate={len(data['senate'])} committees={len(data['committees'])}")
