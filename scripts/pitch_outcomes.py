"""Did the pitch actually WORK?

Competition placement and being right about the stock are different things, and
the deck corpus only evidences the first. A pitch that placed 1st and then went
the wrong way is worse than useless as a calibration exemplar — it would teach
the screener to surface exactly the wrong shape.

Reads ticker/date/direction/placement out of the deck filenames, then measures
realized forward return from the pitch date, direction-signed, against SPX over
the identical window.

    python scripts/pitch_outcomes.py            # -> reports/.../pitch_outcomes.csv

Honest limits: this measures the CALL, not the pitch. A 3-month window is noise
for a structural thesis; a name can be right and early. Verdicts are reported at
several horizons for that reason, and names with < 6 months elapsed are marked
TOO_EARLY rather than scored.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from screener import pipeline

DECKS = pipeline.ROOT / "reports" / "2026-07-24_pitch_audit" / "pitch_decks"
OUT = pipeline.ROOT / "reports" / "2026-07-25_pitch_sourcing" / "pitch_outcomes.csv"

# filename convention: SOURCE_YYYY-MM-DD_TICKER_DIRECTION_placement...
FNAME = re.compile(
    r"^(?P<src>[A-Z0-9]+)_(?P<date>\d{4}-\d{2}-\d{2})_(?P<tick>[A-Z][A-Z0-9.\-]*)"
    r"_(?P<dir>LONG|SHORT)_(?P<place>[^.]*)", re.IGNORECASE)

# Pitches with no deck in the corpus, added from verified reporting.
EXTRA = [
    # src, date, ticker, direction, placement
    ("SOHN", "2025-10-15", "VITL", "SHORT", "winner-validated-exemplar"),
    ("PITCH", "2026-04-10", "CAKE", "SHORT", "validated-exemplar"),
    ("FORDHAM", "2026-03-14", "HAS", "SHORT", "1st-Roger-F-Murray"),
]

HORIZONS = {"3m": 63, "6m": 126, "12m": 252}


def collect() -> pd.DataFrame:
    rows = []
    for p in DECKS.rglob("*.pdf"):
        m = FNAME.match(p.name)
        if not m:
            continue
        rows.append({
            "source": m.group("src").upper(),
            "date": m.group("date"),
            "ticker": m.group("tick").upper(),
            "direction": m.group("dir").lower(),
            "placement": m.group("place").strip("_"),
            "file": p.name,
        })
    for src, date, tick, dr, place in EXTRA:
        rows.append({"source": src, "date": date, "ticker": tick,
                     "direction": dr.lower(), "placement": place, "file": ""})
    df = pd.DataFrame(rows).drop_duplicates(subset=["ticker", "date", "direction"])
    return df.sort_values(["date", "ticker"]).reset_index(drop=True)


def _prices(tickers: list[str]) -> pd.DataFrame:
    import yfinance as yf
    raw = yf.download(tickers, period="max", interval="1d", auto_adjust=True,
                      progress=False, multi_level_index=False)
    close = raw["Close"] if "Close" in raw else raw
    if isinstance(close, pd.Series):
        close = close.to_frame(tickers[0])
    return close


def main() -> None:
    df = collect()
    print(f"pitches with a parseable ticker/date/direction: {len(df)}")
    ticks = sorted(set(df["ticker"]) | {"^GSPC"})
    print(f"downloading {len(ticks)} price histories...")
    px = _prices(ticks)
    spx = px["^GSPC"] if "^GSPC" in px else pd.Series(dtype=float)

    recs = []
    for _, r in df.iterrows():
        t, d0 = r["ticker"], pd.Timestamp(r["date"])
        sign = 1.0 if r["direction"] == "long" else -1.0
        rec = dict(r)
        s = px[t].dropna() if t in px.columns else pd.Series(dtype=float)
        if s.empty:
            rec["status"] = "NO_PRICE_DATA"
            recs.append(rec)
            continue
        s = s[s.index >= d0]
        if len(s) < 5:
            rec["status"] = "NO_PRICE_AFTER_DATE"
            recs.append(rec)
            continue
        p0 = float(s.iloc[0])
        b = spx[spx.index >= d0].dropna() if len(spx) else pd.Series(dtype=float)
        b0 = float(b.iloc[0]) if len(b) else np.nan
        for name, nd in HORIZONS.items():
            if len(s) > nd:
                rec[f"ret_{name}"] = round(sign * (float(s.iloc[nd]) / p0 - 1), 4)
                if len(b) > nd:
                    rec[f"alpha_{name}"] = round(
                        rec[f"ret_{name}"] - sign * (float(b.iloc[nd]) / b0 - 1), 4)
        rec["ret_todate"] = round(sign * (float(s.iloc[-1]) / p0 - 1), 4)
        if len(b):
            rec["alpha_todate"] = round(
                rec["ret_todate"] - sign * (float(b.iloc[-1]) / b0 - 1), 4)
        rec["days_elapsed"] = int((s.index[-1] - d0).days)
        # A structural thesis needs room to work; do not score the impatient ones.
        if rec["days_elapsed"] < 180:
            rec["status"] = "TOO_EARLY"
        else:
            key = rec.get("alpha_12m", rec.get("alpha_6m", rec.get("alpha_todate")))
            rec["status"] = ("WORKED" if key is not None and key > 0.05 else
                             "FAILED" if key is not None and key < -0.05 else "FLAT")
        recs.append(rec)

    out = pd.DataFrame(recs)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)

    print(f"\nwrote {OUT}\n")
    scored = out[out["status"].isin(["WORKED", "FAILED", "FLAT"])]
    print("=== outcome by direction (direction-signed alpha vs SPX) ===")
    if len(scored):
        print(pd.crosstab(scored["direction"], scored["status"]).to_string())
        print("\n=== median signed alpha ===")
        for h in ["alpha_6m", "alpha_12m", "alpha_todate"]:
            if h in scored:
                g = scored.groupby("direction")[h].median().round(3)
                print(f"  {h:14s} " + "  ".join(f"{k}={v:+.3f}" for k, v in g.items()))
    unscored = out[~out["status"].isin(["WORKED", "FAILED", "FLAT"])]
    if len(unscored):
        print(f"\nnot scored ({len(unscored)}): "
              + ", ".join(f"{r.ticker}({r.status})" for r in unscored.itertuples()))


if __name__ == "__main__":
    main()
