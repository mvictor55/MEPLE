#!/usr/bin/env bash
# Full pipeline for one session. Needs network access to legislature.maine.gov
# (and legiscan.com if LEGISCAN_KEY is set). Safe to re-run: every step caches.
set -euo pipefail
cd "$(dirname "$0")"
python -m pipeline.legiscan ${LEGISCAN_ZIP:+--zip "$LEGISCAN_ZIP"}   # LD list, sponsors, history, roll calls
python -m pipeline.crawl_bills                                       # status pages: committee history, reports, amendments
python -m pipeline.billtext                                          # official SUMMARY from each bill's printed text
python -m pipeline.rosters                                           # legislators + committee rosters
python -m pipeline.testimony index                                   # needs TESTIMONY_ENDPOINTS (see pipeline/testimony.py)
python -m pipeline.testimony extract                                 # PDFs -> text, positions
python -m pipeline.build                                             # -> site/data
