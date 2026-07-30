"""Point-in-time calibration scorecard.

The question this answers, which nothing before it could: **at the date a pitch
was actually made, would the screener have surfaced that name?**

Every earlier check ran against today's universe, where the winners' setups have
already resolved — BURL's drawdown is 0.02 now versus whatever it was in Feb
2022. Measuring there tests the wrong thing. This reads the replay dirs, which
are built point-in-time (facts filed <= the date, filings bounded by it, prices
truncated, FRED from cache), and reports where each pitch landed on that date.

Selection here is on COMPETITION PLACEMENT, not realized return — those are
different objectives (see config.LONG_PITCHABILITY). Only 1st/2nd finishes at a
sponsored or flagship competition are treated as calibration targets.

    python scripts/calibration_scorecard.py
"""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from screener import pipeline

OUT = pipeline.ROOT / "reports" / "2026-07-25_pitch_sourcing" / "calibration_scorecard.md"

# (replay_date, ticker, direction, competition_date, placement, competition)
# Replay date is batched: a name pitched within ~2 weeks of the replay date is
# scored against it, since the filed data barely moves over that span. The
# offset is printed so the reader can discount it.
TARGETS = [
    ("2021-11-21", "FICO", "long", "2021-11-21", "1st", "UNC Alpha"),
    ("2021-11-21", "DPZ",  "long", "2021-11-19", "2nd", "UNC Alpha"),
    ("2022-02-23", "BURL", "long", "2022-02-23", "1st", "Point72 (1st of 200)"),
    ("2022-12-01", "ENTG", "long", "2022-12-01", "1st", "UNC Alpha"),
    ("2022-12-01", "POOL", "long", "2022-12-01", "1st", "UNC Alpha"),
    ("2022-12-01", "TYL",  "long", "2022-11-15", "2nd", "UNC Alpha"),
    ("2023-11-17", "JBI",  "long", "2023-11-17", "1st", "UNC Alpha"),
    ("2023-11-17", "ULTA", "long", "2023-11-14", "2nd", "UNC Alpha"),
    ("2023-11-17", "PATK", "long", "2023-11-19", "2nd", "UNC Alpha"),
    ("2023-11-17", "AAON", "long", "2023-11-17", "3rd", "UNC Alpha"),
    ("2024-09-19", "GAP",  "long", "2024-09-19", "2nd", "UNC Alpha"),
    ("2024-11-22", "WMS",  "long", "2024-11-22", "1st", "UNC Alpha"),
    ("2024-11-22", "LII",  "long", "2024-11-20", "2nd", "UNC Alpha"),
    ("2024-11-22", "BMI",  "long", "2024-11-24", "3rd", "UNC Alpha"),
    ("2024-11-22", "BBWI", "long", "2024-12-05", "1st", "UNC Alpha"),
    ("2025-11-21", "WEX",  "long", "2025-11-21", "3rd", "UNC Alpha"),
    ("2025-11-21", "CLH",  "long", "2025-11-13", "2nd", "UNC Alpha"),
    ("2025-11-21", "MIDD", "long", "2025-12-04", "1st", "UNC Alpha"),
]

LONG_DELIVERABLES = {
    "long_shortlist", "long_financials", "derated_compounder",
    "inflecting_thin_moat", "surviving_distressed_value", "pitchable_longs",
}


def cohorts_for(dirpath: str) -> dict[str, list[str]]:
    out = {}
    for f in glob.glob(os.path.join(dirpath, "*.csv")):
        name = os.path.basename(f)[:-4]
        if name == "universe_ranked":
            continue
        try:
            c = pd.read_csv(f)
        except Exception:
            continue
        if "ticker" in c.columns:
            out[name] = c["ticker"].tolist()
    return out


def main() -> None:
    lines: list[str] = []
    rows = []
    for date in sorted({t[0] for t in TARGETS}):
        d = pipeline.OUT / f"replay_{date}"
        uni_f = d / "universe_ranked.csv"
        if not uni_f.exists():
            lines.append(f"- **{date}** — replay dir missing, skipped")
            continue
        u = pd.read_csv(uni_f)
        u["long_rank"] = u["long_composite"].rank(ascending=False, method="min")
        u["short_rank"] = u["short_composite"].rank(ascending=False, method="min")
        coh = cohorts_for(str(d))
        n = len(u)
        for rdate, tick, direction, cdate, place, comp in TARGETS:
            if rdate != date:
                continue
            r = u[u["ticker"] == tick]
            rec = {"replay": date, "ticker": tick, "pitch_date": cdate,
                   "offset_d": (pd.Timestamp(cdate) - pd.Timestamp(date)).days,
                   "placement": place, "competition": comp, "n_universe": n}
            if not len(r):
                rec["status"] = "ABSENT_FROM_UNIVERSE"
                rows.append(rec)
                continue
            r = r.iloc[0]
            where = [f"{k}#{v.index(tick) + 1}/{len(v)}"
                     for k, v in coh.items() if tick in v]
            longs = [w for w in where if w.split("#")[0] in LONG_DELIVERABLES]
            shorts = [w for w in where
                      if w.split("#")[0] not in LONG_DELIVERABLES]
            rec.update({
                "long_composite": round(float(r["long_composite"]), 1),
                "long_rank": int(r["long_rank"]),
                "long_pctile": round(100 * (1 - r["long_rank"] / n), 1),
                "short_composite": round(float(r["short_composite"]), 1),
                "drawdown": round(float(r.get("drawdown", float("nan"))), 2),
                "t_inflection": round(float(r.get("t_inflection", float("nan"))), 1),
                "long_cohorts": ", ".join(longs) or "NONE",
                "short_cohorts": ", ".join(shorts) or "none",
                "status": "SURFACED_LONG" if longs else "MISSED",
            })
            rows.append(rec)

    df = pd.DataFrame(rows)
    csv = OUT.with_suffix(".csv")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv, index=False)

    scored = df[df["status"].isin(["SURFACED_LONG", "MISSED"])]
    tier12 = scored[scored["placement"].isin(["1st", "2nd"])]
    lines.append("# Point-in-time calibration scorecard\n")
    lines.append("Would the screener have surfaced these pitches **on the date "
                 "they were pitched**? Replays are point-in-time; selection is "
                 "on competition placement, not realized return.\n")
    if len(scored):
        hit = (scored["status"] == "SURFACED_LONG").sum()
        lines.append(f"**All placings: {hit}/{len(scored)} surfaced in a long "
                     f"deliverable.**\n")
    if len(tier12):
        h2 = (tier12["status"] == "SURFACED_LONG").sum()
        lines.append(f"**1st/2nd only: {h2}/{len(tier12)} surfaced.**\n")
    cols = ["ticker", "pitch_date", "placement", "long_composite", "long_rank",
            "long_pctile", "drawdown", "t_inflection", "long_cohorts",
            "short_cohorts", "status"]
    have = [c for c in cols if c in scored.columns]
    if len(scored):
        # hand-rolled: pandas' to_markdown needs `tabulate`, which is not
        # installed here, and this report is not worth a dependency
        w = {c: max(len(c), *(len(str(v)) for v in scored[c])) for c in have}
        lines.append("")
        lines.append("| " + " | ".join(c.ljust(w[c]) for c in have) + " |")
        lines.append("|" + "|".join("-" * (w[c] + 2) for c in have) + "|")
        for _, r in scored.iterrows():
            lines.append("| " + " | ".join(str(r[c]).ljust(w[c])
                                           for c in have) + " |")
        lines.append("")
    missing = df[~df["status"].isin(["SURFACED_LONG", "MISSED"])]
    if len(missing):
        lines.append("\n## Not scored\n")
        for _, r in missing.iterrows():
            lines.append(f"- {r['ticker']} @ {r['pitch_date']}: {r['status']}")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nwrote {OUT}\nwrote {csv}")


if __name__ == "__main__":
    main()
