# Stock Pitch Competition Screener

A turnaround-first long/short **idea generator** for stock pitch competitions.
It builds the full $1–20B US universe from SEC EDGAR's bulk XBRL data, scores
every company on fundamental inflection (longs) and forensic red flags
(shorts), and writes ranked shortlists, discovery cohorts, and per-name tear
sheets — a starting point for manual research, **not** a decision engine and
not a backtester.

This is an implementation of `SCREENER_SPEC.md` v2 (all 38 findings of the
2026-07 methodology audit in `METHODOLOGY_AUDIT.md` folded in). Those two
documents are the methodology; the code follows them line by line.

## What it does

- **Universe**: every US-listed name with $1–20B market cap, built from
  EDGAR's `companyfacts.zip` + `submissions.zip` bulk data with entity
  hygiene (dual-class dedup, shells, foreign filers).
- **Fundamentals**: raw XBRL facts are converted to clean discrete quarters
  (Q4 derivation, YTD differencing, restatement dedup) through per-period
  tag-fallback ladders, then to TTM/YoY series.
- **Long side**: an inflection engine (level/slope/trough shape detection,
  peak-recency, cost-cut haircuts) plus moat/ROIC-WACC, Piotroski F-Score,
  and survivability metrics.
- **Short side**: forensic signals — Beneish M-Score, Sloan accruals, revenue
  deceleration, serial dilution, buyback behavior, filing-event flags (NT
  filings, auditor changes, non-reliance 8-Ks), short-interest shape.
- **Market layer**: prices, betas, drawdowns and short interest via yfinance;
  risk-free rate and PPI/CPI via FRED.
- **Outputs**: composite-ranked long/short shortlists, plus ~15 archetype
  "discovery cohort" CSVs that surface names the composites structurally
  can't reward, and Tier 1/Tier 2 markdown tear sheets per name.

Everything is cache-first and reproducible: a full run persists every raw
feature row, so iterating on scoring re-ranks the whole universe in ~1 second,
and the point-in-time replay harness can re-run the screen as of any past date
(facts, filings, prices, and FRED all truncated to what existed then).

## Quickstart

```bash
git clone https://github.com/Kaenyne/pitch-screener.git
cd pitch-screener
pip install -r requirements.txt
```

Two one-time config items:

1. **EDGAR User-Agent** — the SEC requires a real name + email on bulk
   requests. Set `EDGAR_USER_AGENT` in `screener/config.py` (or the env var).
2. **FRED API key** — free, instant:
   <https://fred.stlouisfed.org/docs/api/api_key.html>. Set the
   `FRED_API_KEY` env var.

The optional Refinitiv/LSEG layer (`screener/refinitiv.py`, `scripts/lseg.ps1`)
adds IBES estimate data if you have a Workspace entitlement; the screener runs
fully without it.

## Layout

```
SCREENER_SPEC.md          the locked spec (authority for every decision)
METHODOLOGY_AUDIT.md      audit rationale behind the spec
screener/
  config.py               every tunable, weight, window, kernel shape, tag ladder
  edgar.py                EDGAR client + bulk-zip readers + submissions parsing
  universe.py             universe build + entity hygiene (audit 1.4/1.5)
  quarterlyize.py         facts -> discrete quarters (Q4 derivation, YTD
                          differencing, restatement dedup) + TTM/YoY series
  ladders.py              per-period tag-fallback ladder resolution (audit 1.2)
  fundamentals.py         per-company assembly -> CompanyData contract
  inflection.py           the inflection engine (level/slope/trough,
                          peak-recency primitive, cost-cut haircut, guards)
  metrics.py              ROIC/WACC/moat, F-Score, survivability, financials
                          substitutes (bank ROE-Ke, REIT FFO)
  forensic.py             short side: Beneish, Sloan, decel, dilution,
                          buybacks, event flags, SI hump
  market.py               yfinance layer (chunked prices, betas, SI, FRED rf)
  scoring.py              normalization, missing-data policy, themes, composites
  shortlist.py            threshold + caps + contested/on-both-lists flags
  exemplars.py            CAKE/VITL snapshot + diff harness (the before/after
                          every scoring change is judged on)
  tearsheet.py            Tier 1/Tier 2 tear sheets (incl. audit-4.2 cross-check)
  diagnostics.py          first-run diagnostics 1-6
  pipeline.py             orchestration
scripts/
  integration_test.py     end-to-end run on ~13 archetype names
  full_run.py             the full $1-20B universe run (builds every company)
  rescore.py              fast re-score of the last full run's cached rows (~1s)
  replay.py               point-in-time re-run as of a past date (calibration)
  clean.py                reclaim regenerable churn (__pycache__, old logs, ...)
tests/                    behavioral unit suite (synthetic shapes)
```

## Running

```bash
# one-time data pull (~3 GB, User-Agent header configured in config.py)
curl -H "User-Agent: <you@email>" -o data/edgar/companyfacts.zip \
  https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip
curl -H "User-Agent: <you@email>" -o data/edgar/submissions.zip \
  https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip
curl -H "User-Agent: <you@email>" -o data/edgar/company_tickers_exchange.json \
  https://www.sec.gov/files/company_tickers_exchange.json

python -m pytest tests/          # behavioral suite (158 tests)
python scripts/integration_test.py
python scripts/full_run.py       # full universe; ~25-30 min build once the
                                 # EDGAR zips + price caches are warm (the
                                 # first-ever run also pays the ~3 GB pull)
```

### The tuning loop (fast path)

A full run caches every company's raw feature row to
`data/cache/raw_rows.parquet`. Iterating on **scoring** (weights, thresholds,
tags, cohort SIC sets — anything in `config.py`/`scoring.py`/`shortlist.py`
that does not change a raw input column) does **not** need another full run:

```bash
python scripts/rescore.py                  # reload cached rows, re-score +
                                           # re-rank + rewrite CSVs +
                                           # diagnostics — ~1 second
python scripts/rescore.py --set-baseline   # ...and freeze the exemplar
                                           # snapshot as the reference
```

Each rescore prints where CAKE and VITL land — composite, rank, which cohort
CSVs they reach and at what rank, every tag fired — and diffs that against
`output/exemplar_baseline.json`. Recording the composite alone would be
useless: both exemplars miss every composite-ranked short deliverable and are
surfaced only by the tag cohorts, so a cohort regression would otherwise read
as "no change".

Only changes to the per-company **build** (fundamentals / metrics / forensic /
pipeline raw-row construction) require `full_run.py`. Tear sheets are
regenerated only by the full run.

### Calibration replays

To ask "would the screen have caught X on date D?", replay the universe as of
that date. Calibrate **only** against the known-good exemplars — CAKE
(`2026-04-10`) and VITL (`2025-10-15`); tuning against screener-surfaced names
would be self-confirming.

```bash
python scripts/replay.py 2025-10-15 --tickers VITL --offline   # -> output/replay_2025-10-15/
python scripts/replay.py 2026-04-10 --tickers CAKE --offline   # -> output/replay_2026-04-10/
```

**What "point-in-time" covers** (all four enforced; the filing and FRED legs
were added 2026-07-25):

| leg | guarantee |
|---|---|
| facts | only observations FILED <= the date (`filter_facts_asof`) |
| filings | submissions parsed with `asof`: form history, 8-K items, NT counts and latest-accession pointers stop at the date, and the NT (3y) / 8-K (2y) lookback windows are anchored on it rather than on wall-clock today |
| prices | closes/SPX truncated at the date; betas, drawdowns and run-ups recomputed on the truncated history |
| FRED | DGS10 + PPI/CPI served from `data/cache/fred_series.parquet`; `--offline` forbids the network so a rerun is reproducible |

Before the filing guard, a replay of 2025-10-15 saw **144,674** filings that
did not exist yet (67.7 per name), flipping `nt_filer` on 52 names,
`auditor_change` on 92 and `non_reliance` on 30 of ~39 — which is why event
base rates from earlier replays cannot be used for tuning.

Every replay writes `exemplar_snapshot.json` (composite, rank, membership +
rank in every cohort CSV, every tag fired) and diffs it against
`exemplar_baseline.json` in the same dir. Freeze a reference with
`--set-baseline`; after that, each run prints only what moved.

A replay also persists its point-in-time raw rows, so a **scoring** change is
validated against the exemplars at their own dates in ~1s — no rebuild:

```bash
python scripts/rescore.py --from output/replay_2025-10-15 --tickers VITL
python scripts/rescore.py --from output/replay_2026-04-10 --tickers CAKE
```

This writes back into the replay dir and keeps that dir's own baseline, so a
point-in-time check never overwrites the live outputs.

## Outputs (`output/`)

- `universe_ranked.csv` — every scored name, both composites, all sub-scores,
  coverage columns, all tags (long/short CSVs are not informationally separate)
- `long_shortlist.csv` / `short_shortlist.csv` — threshold + ~100 cap +
  soft sector cap, core (non-financial) names
- `long_financials.csv` / `short_financials.csv` — financials/REITs ranked in
  their own section (substitute metrics)
- `segment_zero_revenue.csv` — zero/low-revenue cohort, segmented
- `pitchable_shorts.csv` — the curated short deliverable: conviction-ranked,
  hard-to-pitch sectors (semis, pharma/biotech) removed, capped at ~40 with a
  `high_confidence` top tier (names firing >= 3 distinct on-thesis mechanisms)
- `consumer_shorts.csv` / `industrials_shorts.csv` /
  `commodity_disconnect_shorts.csv` — sector/attribute cohorts ranked by
  on-thesis tag count (so a simple, easy-to-explain short surfaces even when
  the forensic-heavy composite buries it)
- **long-side discovery cohorts** — the long mirror of the short cohort layer,
  each ranked on its own evidence with **no composite pre-cut**, so an
  archetype the composite is not built to reward still gets an output file:
  - `derated_compounder.csv` — demonstrated moat in the top 30%, economics
    still intact (positive current spread **or** ≥60% of the demonstrated
    spread retained), and de-rated on price or valuation
  - `inflecting_thin_moat.csv` — ranked by `inflection_score` with **no moat
    gate**, since this is precisely the shape the moat-first composite cannot
    reward (`inflection_score` caps ~0.58 against 0–100 percentile rows). A
    ranked top-N view, not a filter — see `n_qualified` / `capped_at`
  - `surviving_distressed_value.csv` — 35–80% drawdown ∩ cheap ∩ **green
    traffic light ∩ F ≥ 7**. The survivability legs are the point; without
    them this is a falling-knife screen
  Before these, the long side had exactly one route (`long_composite ≥`
  threshold): 14 of the top 20 names by `inflection_score` reached **no** long
  output file. Coverage is now 53 → 153 names.
- discovery-tag cohorts — each archetype as its own list regardless of
  composite rank: `priced_for_impossible_growth.csv`, `peer_multiple_disconnect.csv`,
  `losing_share_priced_rich.csv`, `ran_on_pricing_success.csv`,
  `archetype_ran_on_temp_success.csv`, `capacity_decay.csv`, `ppi_windfall.csv`
- `details/<TICKER>.json` — per-company raw feature/score blobs (full run only)
- `tearsheets/<TICKER>_<side>.md` — Tier 1 + Tier 2 tear sheets; every LONG
  sheet carries the short-side false-moat cross-check with the
  demonstrated-window dates (audit 4.2 guardrail)
- `diagnostics/first_run_diagnostics.md` + per-diagnostic CSVs — REQUIRED
  reading before tuning/locking windows and weights (audit 6.4)

## Tuning

Everything tunable lives in `screener/config.py` with the spec reference next
to it. The loop: full run once -> read diagnostics 1-6 -> adjust windows /
weights / threshold / tags -> `rescore.py` (~1s) to see the effect -> validate
against the CAKE/VITL replays -> repeat. Reserve `full_run.py` for changes that
alter a raw input column. See "The tuning loop (fast path)" above.

## Housekeeping

`output/` (CSVs, tear sheets, `details/`) and `__pycache__` are regenerable and
accumulate across runs. `python scripts/clean.py --all --dry-run` reports what
is safe to reclaim; drop `--dry-run` to remove it. `data/` (EDGAR zips + parquet
caches) and the CAKE/VITL replay exemplars are load-bearing — the script never
touches them.
