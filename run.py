"""
Run the whole pipeline in order (works on Windows, macOS, Linux).
    python run.py            # all steps
    python run.py build      # one step by name
Every step caches what it already fetched, so re-running resumes.
"""
import os, subprocess, sys
from pathlib import Path

STEPS = [
    ("legiscan",   [sys.executable, "-m", "pipeline.legiscan"]),          # LD list, sponsors, history, roll calls
    ("crawl",      [sys.executable, "-m", "pipeline.crawl_bills"]),       # status pages: committee history, reports, amendments
    ("billtext",   [sys.executable, "-m", "pipeline.billtext"]),          # official SUMMARY from each printed bill
    ("rosters",    [sys.executable, "-m", "pipeline.rosters"]),           # legislators + committee rosters
    ("tindex",     [sys.executable, "-m", "pipeline.testimony", "index"]),   # needs TESTIMONY_ENDPOINTS (see pipeline/testimony.py)
    ("textract",   [sys.executable, "-m", "pipeline.testimony", "extract"]),  # PDFs -> text, positions
    ("build",      [sys.executable, "-m", "pipeline.build"]),             # -> site/data
    # pipeline.build clears site/data, so everything below re-creates its files from saved inputs.
    # Each skips quietly if its inputs don't exist yet; none of them calls a paid API.
    ("pages",      [sys.executable, "-m", "pipeline.legislator_pages"]),  # per-legislator votes/signatures, per-committee hearings
    ("profiles",   [sys.executable, "-m", "pipeline.profiles"]),          # organizations, agencies, public officials
    ("lobbying",   [sys.executable, "-m", "pipeline.lobbying", "build"]), # needs raw/lobbying/reports.json (pipeline.lobbying index)
    ("finance",    [sys.executable, "-m", "pipeline.finance", "2024"]),   # needs raw/finance/*.csv
    ("districts",  [sys.executable, "-m", "pipeline.districts"]),         # district pages + slim testimony/index.json
    ("synthesis",  [sys.executable, "-m", "pipeline.synthesis", "build"]),# rebuilds from saved results; new runs: pipeline.synthesis plan / run --yes
]
DEFAULT_STEPS = ["legiscan", "crawl", "billtext", "rosters", "tindex", "textract", "build", "pages", "profiles", "lobbying", "finance", "districts", "synthesis"]

def main():
    only = sys.argv[1:] or DEFAULT_STEPS
    root = Path(__file__).resolve().parent
    for name, cmd in STEPS:
        if name not in only:
            continue
        print(f"\n=== {name}: {' '.join(cmd[1:])} ===", flush=True)
        # UTF-8 mode: Windows Pythons before 3.15 otherwise read/write files as cp1252
        r = subprocess.run(cmd, cwd=root, env={**os.environ, "PYTHONUTF8": "1"})
        if r.returncode != 0:
            sys.exit(f"step '{name}' failed (exit {r.returncode}); fix it and re-run: python run.py {name}")
    print("\nDone. Deploy the site/ folder.")

if __name__ == "__main__":
    main()
