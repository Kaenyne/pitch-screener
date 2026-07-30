"""Exemplar-replay harness (user direction 2026-07-08).

Reruns the screener AS OF a past date: only EDGAR facts FILED by that date
are visible (point-in-time via each fact's `filed` stamp), prices are
truncated at the date, betas/drawdowns/run-ups recomputed on the truncated
history, and market caps rebuilt from as-of shares x as-of close.

Purpose: "would the screen have caught X on date D?" — calibration against
known good calls (VITL fall 2025, CAKE spring 2026), NOT a backtest.

Honest limitations (documented, accepted):
- universe membership is TODAY's list (survivorship-accepting, consistent
  with the spec's snapshot philosophy); exemplars outside today's band are
  force-included via --tickers
- short interest / quote-summary fields have no history in yfinance ->
  SI rows are absent for every name uniformly (renormalized; SI bonus = 0)
- enterpriseValue falls back to as-of mktcap + EDGAR debt - cash for all

Point-in-time guarantees (all enforced, 2026-07-25):
- facts: only observations FILED <= the date (`filter_facts_asof`)
- filings: submissions parsed with asof, so form history, 8-K items, NT
  counts and latest-accession pointers stop at the date, and the NT/8-K
  lookback windows are anchored on it rather than on wall-clock today
- prices/PPI: truncated at the date; FRED series served from the disk cache
  so a rerun is reproducible (`--offline` forbids the network outright)

Usage:
    python scripts/replay.py 2025-10-15 --tickers VITL
    python scripts/replay.py 2026-05-15 --tickers CAKE,VITL
    python scripts/replay.py 2025-10-15 --tickers VITL --offline --set-baseline

Every run writes output/replay_<date>/exemplar_snapshot.json: composite
scores, ranks, membership+rank in every deliverable CSV, and every tag fired.
With a baseline present the run prints a before/after diff — that is the
before/after any scoring change is judged on.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from screener import (config, exemplars, market, pipeline, scoring,
                      shortlist)
from screener.edgar import BulkZip, parse_submissions
from screener.universe import classify_sector, load_ticker_table

REPORT_COLS = exemplars.DEFAULT_METRICS


def _force_include(uni: pd.DataFrame, tickers: list[str],
                   asof_iso: str = "") -> pd.DataFrame:
    """Add exemplar tickers that fell out of today's cap band."""
    missing = [t for t in tickers if t not in set(uni["ticker"])]
    if not missing:
        return uni
    table = load_ticker_table(pipeline.TICKERS_JSON)
    subs_zip = BulkZip(pipeline.SUBS_ZIP)
    rows = []
    for t in missing:
        hit = table[table["ticker"] == t]
        if not len(hit):
            print(f"  !! {t}: not in the EDGAR ticker table — skipped")
            continue
        cik = int(hit.iloc[0]["cik"])
        subs = subs_zip.get(cik)
        info = parse_submissions(subs, asof=asof_iso) if subs else None
        sic = info.sic if info else None
        row = {
            "cik": cik, "ticker": t, "name": info.name if info else t,
            "exchange": hit.iloc[0].get("exchange", ""), "multi_class": False,
            "sic": sic, "sic_desc": info.sic_description if info else "",
            "fin_sector": classify_sector(sic),
            "is_financial": classify_sector(sic) != "nonfinancial",
            "is_commodity": config.is_commodity_sic(sic),
            "sector2": (sic // 100) if sic else -1,
            "annual_only": (info.filer_type == "annual_only") if info else False,
            "filer_type": info.filer_type if info else "domestic_quarterly",
            "is_despac_name": info.is_despac_name if info else False,
            "nt_recent": info.nt_filings_recent if info else 0,
            "mktcap_build": np.nan, "shares": np.nan, "last_close": np.nan,
            "has_facts": True, "bdc_or_trust": False,
        }
        rows.append(row)
        print(f"  force-included {t} (cik {cik}, sic {sic})")
    if rows:
        uni = pd.concat([uni, pd.DataFrame(rows)], ignore_index=True)
    return uni


def replay(date_str: str, extra_tickers: list[str], workers: int = 10,
           offline: bool = False, set_baseline: bool = False) -> pd.DataFrame:
    asof = pd.Timestamp(date_str)
    cutoff = asof.date().isoformat()
    print(f"=== REPLAY as of {cutoff}{' [offline]' if offline else ''} ===")
    if offline:
        market.set_fred_offline(True)

    uni = pd.read_parquet(pipeline.CACHE / "universe.parquet")
    uni = _force_include(uni, extra_tickers, asof_iso=cutoff)
    tickers = uni["ticker"].tolist()

    # --- point-in-time market data: 5y closes truncated at the date ---
    closes5 = market.chunked_download(
        tickers, period="5y",
        cache_path=str(pipeline.CACHE / "closes_5y.parquet"))
    closes = closes5[closes5.index <= asof]
    spx = market.fetch_spx("5y")
    spx = spx[spx.index <= asof]
    dd = market.drawdown_inputs(closes)   # 3y window inside truncated frame
    betas = market.beta_weekly(closes, spx)
    rf_hist = market.rf_history_annual()
    rf = float(rf_hist.get(asof.year, np.nan))
    if not np.isfinite(rf):
        rf = market.rf_current()
    qs = pd.DataFrame()  # no historical quote summaries: SI/EV absent
    ppi_ids = sorted(set(config.SIC_PPI_CROSSWALK.values())
                     | set(config.PPI_TICKER_OVERRIDE.values()))
    ppi = market.fetch_ppi_series(ppi_ids)  # external_price_signal filters <= asof
    mkt = {"closes": closes, "betas": betas, "dd": dd, "qs": qs,
           "rf": rf, "rf_hist": rf_hist, "ppi": ppi}

    raw, details = pipeline.stage_companies(uni, mkt, workers=workers,
                                            asof=asof, filed_cutoff=cutoff)
    if "error" in raw.columns:
        raw = raw[raw["error"].isna()].drop(columns=["error"])
    scored = scoring.score_universe(raw)
    outputs = shortlist.rank_outputs(scored)
    outdir = pipeline.OUT / f"replay_{cutoff}"
    shortlist.write_csvs(outputs, str(outdir))
    print(f"scored {len(scored)} names -> {outdir}")

    # Persist the point-in-time raw rows so a SCORING change can be validated
    # against the exemplars with `rescore.py --from <dir>` in ~1s instead of
    # paying this ~50-min build again.
    try:
        raw.to_parquet(outdir / "raw_rows.parquet", index=False)
        print(f"raw rows -> {outdir / 'raw_rows.parquet'} "
              f"(rescore with: python scripts/rescore.py --from {outdir})")
    except Exception as e:   # object-dtype columns etc. — never fail the run
        print(f"  !! raw_rows.parquet not written ({e}); "
              f"rescore will fall back to universe_ranked.csv")

    sup = scored.get("n_filings_suppressed")
    leak_guard = {
        "suppressed": int(sup.fillna(0).sum()) if sup is not None else 0,
        "names": int((sup.fillna(0) > 0).sum()) if sup is not None else 0,
        "fred": "disk cache (offline)" if offline else "disk cache + network",
    }
    extra_fails = []
    if leak_guard["suppressed"] <= 0:
        extra_fails.append(
            "as-of filing guard suppressed 0 filings — the replay is seeing "
            "today's filing history (lookahead leak)")

    snap = exemplars.snapshot(scored, outputs, extra_tickers, cutoff)
    snap["leak_guard"] = leak_guard
    print(f"\n  as-of filing guard: {leak_guard['suppressed']} filings hidden "
          f"across {leak_guard['names']} names")
    print(f"  FRED source: {leak_guard['fred']}")
    fails = exemplars.check_invariants(snap, extra_tickers, extra_fails)
    n_fail = exemplars.emit(snap, outdir, set_baseline=set_baseline,
                            invariant_fails=fails)
    if n_fail:
        print(f"\n{n_fail} invariant failure(s)")
    return scored


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--tickers", default="", help="comma-separated exemplars")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--offline", action="store_true",
                    help="forbid FRED network access — cache only, so the "
                         "run is reproducible")
    ap.add_argument("--set-baseline", action="store_true",
                    help="freeze this run's snapshot as the comparison "
                         "baseline for later changes")
    args = ap.parse_args()
    tick = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    replay(args.date, tick, args.workers, offline=args.offline,
           set_baseline=args.set_baseline)
