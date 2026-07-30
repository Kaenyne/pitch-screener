"""Fast rescore: reload the cached raw rows from the last full run and re-run
ONLY scoring -> shortlist -> outputs (+ diagnostics). Seconds, not the ~30-min
full build.

Use this after any change to scoring.py / shortlist.py / diagnostics.py, or a
config change that only affects SCORING (weights, thresholds, tags, cohort SIC
sets). Changes to the per-company BUILD (fundamentals/metrics/forensic/pipeline
raw-row construction — i.e. anything that changes a RAW input column) require a
full run: `python scripts/full_run.py`.

Tear sheets are NOT regenerated here (they need the per-name detail blobs from a
full run); the CSV outputs + diagnostics are.

Every rescore prints the exemplar snapshot (CAKE/VITL: composite, rank, cohort
membership, tags) and diffs it against output/exemplar_baseline.json, so a
scoring change is read as a before/after instead of re-derived. This is the
FAST check on today's universe; `scripts/replay.py` is the honest
point-in-time one. Freeze a reference with `--set-baseline`.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from screener import diagnostics, exemplars, pipeline, scoring, shortlist


def _load_raw(src_dir: Path | None = None) -> tuple[pd.DataFrame, str]:
    """Prefer the parquet raw-row cache (dtype-safe); fall back to the scored
    universe CSV (score_universe is idempotent w.r.t. its raw inputs).

    With `src_dir` (a replay dir), rescore that POINT-IN-TIME run instead of
    the live one — how a scoring change is validated against CAKE/VITL at
    their own dates without paying the ~50-min replay build again."""
    if src_dir is not None:
        d = Path(src_dir)
        if not d.is_dir():
            raise SystemExit(f"--from: not a directory: {d}")
        parquet, csv = d / "raw_rows.parquet", d / "universe_ranked.csv"
        if parquet.exists():
            return pd.read_parquet(parquet), str(parquet)
        if csv.exists():
            return (pd.read_csv(csv),
                    f"{csv} (CSV fallback — this replay predates raw-row "
                    f"persistence; re-run replay.py to get the parquet)")
        raise SystemExit(f"--from: no raw_rows.parquet or universe_ranked.csv "
                         f"in {d}")
    parquet = pipeline.CACHE / "raw_rows.parquet"
    if parquet.exists():
        return pd.read_parquet(parquet), str(parquet)
    csv = pipeline.OUT / "universe_ranked.csv"
    if csv.exists():
        return (pd.read_csv(csv),
                f"{csv} (CSV fallback — run full_run once to enable the "
                f"faster/dtype-safe parquet cache)")
    raise SystemExit("No raw_rows.parquet or universe_ranked.csv found — run "
                     "`python scripts/full_run.py` first.")


def main(tickers=None, set_baseline: bool = False, src_dir=None):
    t0 = time.time()
    raw, src = _load_raw(src_dir)
    print(f"rescore: loaded {len(raw)} rows from {src}")

    # A replay rescore writes back into its own dir and keeps its own
    # baseline, so the live outputs are never overwritten by a point-in-time
    # check (and the two baselines never compare across dates).
    outdir = Path(src_dir) if src_dir is not None else pipeline.OUT
    label = outdir.name.replace("replay_", "") if src_dir is not None else "live"

    scored = scoring.score_universe(raw)
    outputs = shortlist.rank_outputs(scored)
    paths = shortlist.write_csvs(outputs, str(outdir))
    diag = diagnostics.run_all(scored, str(outdir / "diagnostics"))

    print(f"\n=== RESCORE COMPLETE in {time.time() - t0:.1f}s ===")
    for k in ("long_shortlist", "short_shortlist", "long_financials",
              "short_financials", "consumer_shorts", "industrials_shorts",
              "commodity_disconnect_shorts"):
        if k in outputs and len(outputs[k]):
            print(f"  {k}: {len(outputs[k])} names")
    print(f"  wrote {len(paths)} CSVs + diagnostics")
    print("  NOTE: tear sheets NOT regenerated — run scripts/full_run.py for those.")

    # Exemplar before/after. The label is the replay date, or "live" for
    # today's universe, so a baseline can never be compared across dates.
    tick = list(tickers or exemplars.DEFAULT_EXEMPLARS)
    snap = exemplars.snapshot(scored, outputs, tick, label)
    fails = exemplars.check_invariants(snap, tick)
    exemplars.emit(snap, outdir, set_baseline=set_baseline,
                   invariant_fails=fails)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="",
                    help="comma-separated exemplars (default CAKE,VITL)")
    ap.add_argument("--set-baseline", action="store_true",
                    help="freeze this run's exemplar snapshot as the reference")
    ap.add_argument("--from", dest="src_dir", default=None,
                    help="rescore a replay dir (e.g. output/replay_2025-10-15) "
                         "instead of the live run — point-in-time validation "
                         "in ~1s")
    args = ap.parse_args()
    tick = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    main(tick or None, set_baseline=args.set_baseline, src_dir=args.src_dir)
