# MEPLE phase 2: legislator and committee records, lobbying, campaign finance

This drop adds to the repo after the first data build has landed. Everything
in it is additive: new pipeline modules write new files under `site/data/`,
and the site loads those files only if they exist (a 404 means "not built
yet"), so nothing here can break the phase-1 build.

## What's in the drop

| Path | What it is |
|---|---|
| `pipeline/legislator_pages.py` | Derives `site/data/legislators/{id}.json` (roll-call votes, committee-report signatures, sponsorship) and `site/data/committees/{code}.json` (hearings by date, reports with splits, testimony volume) from the built bill files. No new sources. Runs after `build`. |
| `pipeline/lobbying.py` | Maine Ethics Commission monthly lobbyist reports (mainelobbying.com). `discover` logs the portal's data endpoints with Playwright; `index` pulls the session's reports; `build` writes `site/data/lobbying/LD{ld}.json` and `lobbying/index.json`. |
| `pipeline/finance.py` | Campaign finance summaries per legislator from CSV exports of mainecampaignfinancedisclosure.com (2024 cycle for the 132nd). Writes `site/data/finance/{legislator_id}.json`. |
| `pipeline/profiles.py`, `pipeline/agencies.py` | Profiles for organizations (same normalized name on 2+ testimonies), state agencies and public bodies, and public officials testifying in their official role (identified from the organization field or the letter's opening line, matched against `agencies.py`). Private individuals get no profile. Writes `site/data/profiles/` and adds `person_profile_id` / `org_profile_id` to testimony rows. Name variants that were merged are listed in `pipeline/raw/profiles_review.json`. |
| `pipeline/synthesis.py` | AI synthesis of the testimony on the 50 bills with the most testimony, with Claude Sonnet 5.5 through the Message Batches API. `plan` estimates cost and spends nothing; `run --yes` submits, waits and builds; state is saved so it resumes. Writes `site/data/synthesis/`. Needs `ANTHROPIC_API_KEY` in `.env`. |
| `run.py`, `run.sh` | After `build`: `pages`, `profiles`, `lobbying`, `finance`, `synthesis` (rebuild from saved results). `build` clears site/data, so these re-create their files each run; each skips if its inputs don't exist; none calls a paid API. |
| `.env.example` | Adds `ANTHROPIC_API_KEY`. |
| `site/index.html` | New panels (below). |
| `phase2/index.scaffold.html` | The phase-1 `site/index.html` exactly as shipped, as the merge base. |

## Site changes (site/index.html)

- Bill page: "Lobbying on this bill" (clients, lobbyist/firm, months reported) under the testimony panel; "Watch and read" panel in the sidebar if a bill JSON carries `links` (`hearing_video`, `floor_debate[]`, `analyses[]`), which a later module can fill.
- Legislator page: roll-call votes table with a tally, committee reports signed, and a campaign finance block (funding type, raised, spent, share from Maine, contributor types, source).
- Committee page: public hearings by date with testimony counts, and "Most lobbied bills here" from the lobbying index.
- Bill page testimony is paged: the first 25 render, "Show 50 more" adds the next batch, and bills with more than 10 testimonies get a position filter and a name/organization/town search. Filters live in the URL (`#/bill/1822?tpos=oppose&tq=portland`) so a filtered view can be shared. Tested with a 920-testimony bill: first render about 0.25 s.
- Bills heard jointly: if a bill JSON has `heard_with: [ld, ...]`, the testimony panel says the hearing was held together with those bills and that the Legislature lists each letter under every bill in it.
- Former members: legislators with `current: false` (optional `served_until`, `replaced_by`) are labeled "Served until … · succeeded by …", and the Legislators page defaults to current members with a filter for former members.
- "Who testified" (nav and footer): a searchable list of organizations, agencies and officials, and a page for each with every bill it testified on, its positions, the people who testified for it (organizations), its officials (agencies), name variants, and a lobbying cross-reference when the lobbying data is built. Names on testimony link to these pages.
- "What the testimony says" on bills with a synthesis: a short summary, then arguments for, arguments against, changes requested, and other points, each with a count of letters that make it. The count is a button that filters the letter list below to those letters (`#/bill/1822?theme=2`). One line says it's an AI-generated summary, with the model and date.
- The position bar no longer draws slivers for zero counts; the header nav fits on one line at laptop widths.
- Phone-width fixes for the new tables.

Both legislator and committee views are now `async` and awaited in the router.

### Merging

If `site/index.html` in the repo is unchanged since the scaffold, copy the
phase-2 `site/index.html` over it. If Claude Code changed it during the phase-1
build, do a three-way merge: `phase2/index.scaffold.html` is the common base,
the repo's current file is "ours", the phase-2 file is "theirs".

## Run order

```
python run.py pages            # immediately: derived from the phase-1 build
python -m pipeline.lobbying discover    # one-time: find the portal endpoints
#   -> fill LOBBYING_ENDPOINTS and check normalize_record() field names
python -m pipeline.lobbying index
python run.py lobbying
#   export 2024 legislative candidates and their transactions as CSV from
#   mainecampaignfinancedisclosure.com into pipeline/raw/finance/
python run.py finance          # prints the CSV headers it matched; fix COLUMN if needed
python run.py profiles         # then open pipeline/raw/profiles_review.json and the Who testified page
python -m pipeline.synthesis plan           # prints the 50 bills, request count and cost estimate; spends nothing
python -m pipeline.synthesis run --yes      # only after Matt approves the estimate
```

Then commit `site/data` and push.

Before `profiles`: on the live data, a testimony card showed "Andrew Butcher, Portland" with no town beside the date, which
suggests the town is landing in the `organization` field. Check the field mapping in `pipeline/testimony.py` against the
Legislature's feed, fix it, and rebuild. `profiles.py` refuses to run if organization values look like towns
(override with `--force` only if you've confirmed they aren't).

Synthesis depends on the bill-specific position fix and `heard_with`: run it after those are in, so jointly heard
bills are summarized only from what letters say about that bill.

Until the lobbying and finance steps have run, the browser console shows one
404 each for `lobbying/index.json` and `finance/index.json`. That is expected:
the site checks those indexes once and skips the panels when they are absent.

## Rules the pages follow (keep them)

- Lobbying reports name bills and monthly compensation per client; they do not
  state a position. The panel says so and never shows a for/against.
- Compensation is per client per month across all bills, so it is never shown
  as a per-bill dollar figure.
- Campaign finance lives on the legislator page only, never beside a vote or a
  bill. Clean Election Act participation is shown as a fact.
- Every panel links to its source.

## Still unverified until a machine with network access runs it

- mainelobbying.com data endpoints and record field names.
- Column names in the campaign finance CSV exports.
- Hearing video links: the Legislature's streaming archive is reached from its
  calendar page by date (meetings since June 2022; YouTube before that). A
  `links` module can resolve a (committee, date) pair to the archive URL once
  the calendar page's structure is inspected.
- Legislative Record (floor debate) links: House and Senate records are
  published per sitting day; indexing LD mentions in those PDFs would give
  each bill its floor-debate days.
