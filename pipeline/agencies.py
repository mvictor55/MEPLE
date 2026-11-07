"""
Maine public bodies whose officials get profiles when they testify in their
official role. This is a STARTER list: verify it against the state's agency
directory (https://www.maine.gov/portal/government/agencies.html) on first run
and add what's missing. Matching is on normalized names, so list the common
forms each body goes by in `aliases`.

Scope (decided Nov 2026): state executive-branch departments and offices,
quasi-public authorities and independent agencies, constitutional officers,
and the Judicial Branch's administrative office. Legislators are excluded
here because they already have legislator pages. Municipal officials are not
in scope yet.
"""

AGENCIES = [
    # Executive departments
    {"id": "dafs",  "name": "Department of Administrative and Financial Services", "aliases": ["DAFS", "Administrative and Financial Services", "Department of Financial and Administrative Services"]},
    {"id": "dacf",  "name": "Department of Agriculture, Conservation and Forestry", "aliases": ["DACF", "Agriculture, Conservation and Forestry"]},
    {"id": "doc",   "name": "Department of Corrections", "aliases": ["Maine Department of Corrections", "MDOC"]},
    {"id": "dvem",  "name": "Department of Defense, Veterans and Emergency Management", "aliases": ["DVEM", "Maine Emergency Management Agency", "MEMA", "Bureau of Maine Veterans' Services"]},
    {"id": "decd",  "name": "Department of Economic and Community Development", "aliases": ["DECD", "Economic and Community Development", "Department of Economic and Community Develpment"]},
    {"id": "doe",   "name": "Department of Education", "aliases": ["Maine Department of Education", "Maine DOE"]},
    {"id": "dep",   "name": "Department of Environmental Protection", "aliases": ["Maine DEP", "DEP"]},
    {"id": "dhhs",  "name": "Department of Health and Human Services", "aliases": ["DHHS", "Maine DHHS", "Maine CDC", "Maine Center for Disease Control and Prevention", "MaineCare"]},
    {"id": "ifw",   "name": "Department of Inland Fisheries and Wildlife", "aliases": ["MDIFW", "Inland Fisheries and Wildlife"]},
    {"id": "dol",   "name": "Department of Labor", "aliases": ["Maine Department of Labor", "MDOL"]},
    {"id": "dmr",   "name": "Department of Marine Resources", "aliases": ["DMR", "Marine Resources"]},
    {"id": "pfr",   "name": "Department of Professional and Financial Regulation", "aliases": ["PFR", "Bureau of Insurance", "Bureau of Financial Institutions", "Office of Professional and Occupational Regulation"]},
    {"id": "dps",   "name": "Department of Public Safety", "aliases": ["DPS", "Maine State Police", "Office of the State Fire Marshal", "Public Safety", "State Bureau of Identification", "Bureau of Highway Safety", "MeBHS"]},
    {"id": "mdot",  "name": "Department of Transportation", "aliases": ["MaineDOT", "MDOT", "Maine Department of Transportation"]},
    # Constitutional officers
    {"id": "sos",   "name": "Secretary of State", "aliases": ["Department of the Secretary of State", "Bureau of Motor Vehicles", "Bureau of Corporations, Elections and Commissions"]},
    {"id": "ag",    "name": "Office of the Attorney General", "aliases": ["Attorney General", "Maine Attorney General"]},
    {"id": "treas", "name": "Office of the State Treasurer", "aliases": ["State Treasurer", "Treasurer of State"]},
    {"id": "audit", "name": "Office of the State Auditor", "aliases": ["State Auditor", "Department of Audit"]},
    # Governor's office and executive offices
    {"id": "gov",   "name": "Office of the Governor", "aliases": ["Governor's Office"]},
    {"id": "geo",   "name": "Governor's Energy Office", "aliases": ["GEO"]},
    {"id": "gopif", "name": "Governor's Office of Policy Innovation and the Future", "aliases": ["GOPIF", "Office of Policy Innovation and the Future"]},
    {"id": "opa",   "name": "Office of the Public Advocate", "aliases": ["Public Advocate"]},
    # Independent agencies and quasi-public authorities
    {"id": "mca",   "name": "Maine Connectivity Authority", "aliases": ["MCA"]},
    {"id": "msha",  "name": "Maine State Housing Authority", "aliases": ["MaineHousing", "Maine Housing"]},
    {"id": "fame",  "name": "Finance Authority of Maine", "aliases": ["FAME"]},
    {"id": "emt",   "name": "Efficiency Maine Trust", "aliases": ["Efficiency Maine"]},
    {"id": "mta",   "name": "Maine Turnpike Authority", "aliases": []},
    {"id": "mpa",   "name": "Maine Port Authority", "aliases": []},
    {"id": "puc",   "name": "Public Utilities Commission", "aliases": ["Maine Public Utilities Commission", "PUC", "MPUC"]},
    {"id": "ethics","name": "Commission on Governmental Ethics and Election Practices", "aliases": ["Maine Ethics Commission", "Ethics Commission"]},
    {"id": "mhrc",  "name": "Maine Human Rights Commission", "aliases": ["Human Rights Commission"]},
    {"id": "witsc", "name": "Maine Indian Tribal-State Commission", "aliases": ["MITSC"]},
    {"id": "wcb",   "name": "Workers' Compensation Board", "aliases": ["Maine Workers' Compensation Board"]},
    {"id": "mainepers", "name": "Maine Public Employees Retirement System", "aliases": ["MainePERS"]},
    {"id": "mmbb",  "name": "Maine Municipal Bond Bank", "aliases": []},
    {"id": "mhhefa","name": "Maine Health and Higher Educational Facilities Authority", "aliases": ["MHHEFA"]},
    {"id": "gcu",   "name": "Gambling Control Unit", "aliases": ["Maine Gambling Control Unit"]},
    {"id": "ocp",   "name": "Office of Cannabis Policy", "aliases": []},
    {"id": "ums",   "name": "University of Maine System", "aliases": ["UMS"]},
    {"id": "mccs",  "name": "Maine Community College System", "aliases": ["MCCS"]},
    # Judicial Branch (administrative office testifies on court bills)
    {"id": "jud",   "name": "Maine Judicial Branch", "aliases": ["Judicial Branch", "Administrative Office of the Courts", "State Court Administrator"]},
    {"id": "mccla", "name": "Maine Commission on Public Defense Services", "aliases": ["Commission on Public Defense Services", "Commission of Public Defense Services", "MCPDS", "Maine Commission on Indigent Legal Services", "MCILS"]},
]

# Titles that mark testimony in an official role.
OFFICIAL_TITLES = [
    "Commissioner", "Deputy Commissioner", "Acting Commissioner", "Associate Commissioner",
    "Director", "Executive Director", "Deputy Director", "Bureau Director", "Division Director",
    "President", "Chief Executive Officer", "CEO", "Chair", "Chairman", "Chairwoman", "Vice Chair",
    "Secretary of State", "Deputy Secretary of State", "Attorney General", "Deputy Attorney General",
    "Assistant Attorney General", "State Treasurer", "Deputy Treasurer", "State Auditor",
    "Public Advocate", "Chancellor", "State Court Administrator", "Chief Justice",
    "Superintendent", "Chief", "Colonel", "Fire Marshal", "General Counsel", "Senior Policy Advisor",
    "Policy Director", "Legislative Liaison", "Commissioner of Education",
]
