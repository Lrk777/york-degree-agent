# Degree Navigator (working name): an unofficial degree planner for York University

**Live:** https://lrk777.github.io/york-degree-agent/ (published by GitHub Actions)

Pick your entry year and program, paste the course list from your unofficial transcript, and see:

- **where you stand**: every requirement marked done, in progress, still needed, or *ask an advisor*
- **which courses count**: for each open requirement, real courses from any faculty or subject code, why
  each one counts, whether you meet the prerequisites, and whether it also
  helps another requirement (e.g. a 3000-level elective that also fills upper-level credits)
- **does this course count?**: type any course and see every requirement it would or wouldn't fill
- **a summary for your advisor**: what looks open and which rules need a human answer
- **an optional AI assistant**: answers questions only through the tools above, using your own Anthropic key

> Not affiliated with York University. The Academic Calendar for the year you entered your program is
> the official source; this tool can be wrong. Confirm decisions with your Faculty's advising office.

## Why

Requirements are spread across the Academic Calendar (one version per year), program checklists,
faculty general-education pages and course pages. Elective and breadth rules mix subject codes,
faculties and levels. Students must follow *the calendar of the year they entered*. York's Degree
Progress Report covers single-major programs only.

## How it works

```
York calendar API (Kuali: one catalog per year, ~10k courses, ~155 programs)  ─┐
York Courses Website (2026–27 term offerings)                                  ─┼─► pipeline (GitHub Actions, weekly)
                                                                               ─┘      │  SQLite → data-quality checks → JSON
                                                                                       ▼
                     web page (GitHub Pages) ── Pyodide runs the same Python engine in the browser
                     transcript never leaves the tab   ── rules engine → course finder → optional AI agent (tools only)
```

| Part | What it does |
|---|---|
| `kuali.py`, `pipeline.py` | Public calendar API client: 1 request/s, retries with backoff, disk cache, re-fetches a course only when its version id changes; idempotent SQLite upserts; a missing course is logged, not fatal |
| `offerings.py` | Term offerings from the York Courses Website's stateless per-course URL; polite, cached, sharded |
| `codes.py` | One key for `SC/MATH2030 Cr=3.00 EN`, `SC/MATH 2030 3.00`, `SC MATH 2030`, `MATH 2030` |
| `reqparse.py`, `tree.py` | Program requirement HTML → rule tree (`all_of`, `credits_from`, free-text `bucket`, `upper_level`, `total`, `note`) |
| `freetext.py` | Interprets free-text rules ("additional credits at the 3000 or 4000 level in anthropology", "outside the major", "non-MATH credits", "free electives", "science disciplines outside the major") into course filters, using the section, the program's major subject and York's 229 subject names. Unknown wording or lists we don't have (approved general-education lists, language groups) → *ask an advisor*, never a guess |
| `rules/buckets.yaml` | Hand-checked rules with sources (Faculty of Science non-science requirement, science breadth) |
| `audit.py` | Deterministic audit: named courses → lists → free-text rules (electives last) → upper-level and total credits; streams; repeats (latest attempt counts); pass/fail; 2-area rule; major subject inferred and editable |
| `suggest.py` | Course finder + "does it count?" with prerequisite, exclusion, double-count and offering checks |
| `retrieval.py` | Pure-Python TF-IDF search scoped to the student's calendar year |
| `agent.py`, `webapi.py` | Tool-using assistant (4 tools) and the JSON API the page calls |
| `export.py` | Per-year JSON for the page, plus a **coverage report**: share of all program rules the engine can check |

## Privacy

The transcript is read inside the browser tab. Student numbers, names, emails and addresses are removed
first; nothing is uploaded or stored. With the optional assistant, questions and tool results go only to
Anthropic, using the student's own key. Tests use made-up students.

## Limits (honest list)

- General-education *approved lists* (e.g. Faculty of Health, LA&PS categories) aren't loaded yet, so those
  rules show *ask an advisor*.
- Minors and double majors: the second program isn't combined yet.
- Prerequisites with non-course conditions ("24 credits completed", "permission of the instructor") are
  shown for a human check.
- Transfer credits, petitions and residency rules aren't modelled.
- Term offerings (which courses run in Fall/Winter 2026-27) aren't shown. York's Courses Website sits behind
  bot protection, and the pipeline doesn't try to get around it, so the scraper is off by default and every
  course shows "Offering not checked". Check the timetable before enrolling.

## Run locally

```bash
pip install -r requirements.txt
pytest -q                                                    # offline tests
PYTHONPATH=src python -m ydn.cli sync --years FW25/26 --programs all
PYTHONPATH=src python -m ydn.cli export --out site/data
```
