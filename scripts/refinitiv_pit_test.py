"""THE decisive experiment: is `SDate` an as-of vintage, or a period selector?

Everything about using Refinitiv consensus for replay calibration hangs on this
one question, and it cannot be answered by reading documentation.

On a FUNDAMENTAL field (TR.Revenue), `SDate` selects which fiscal PERIOD you
want, and returns today's — possibly restated — figure for it. If `SDate` behaves
the same way on an ESTIMATE field, then asking for "2024-03-15" hands back
TODAY's consensus for the fiscal period containing that date. That is a lookahead
leak in the most dangerous possible form: it looks like a point-in-time query, it
returns entirely plausible numbers, and every signal built on it would be
contaminated in a way that reads as alpha.

THE TEST, and why it needs no outside knowledge
-----------------------------------------------
Ask for the SAME fiscal period's consensus twice, at two `SDate` values many
months apart. Consensus for a given fiscal year moves over such a span for
essentially every liquid name — analysts revise constantly.

  - values DIFFER  -> `SDate` is a real as-of vintage. Point-in-time consensus
                      works, and the single biggest gap in this screener closes.
  - values IDENTICAL -> `SDate` is a period selector. The TR route is a LEAK,
                      not a solution, and consensus can only ever be used for
                      live screening, never for replay.

No assumption about which company revised when. Just: did the answer change?

    python scripts/refinitiv_pit_test.py            # default names
    python scripts/refinitiv_pit_test.py AAPL.O     # your own

Run it in a NEW shell with Workspace running and logged in.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Liquid, heavily covered names — consensus for a given FY certainly moved
# across the test window for all of these.
DEFAULT_UNIVERSE = ["AAPL.O", "MSFT.O", "WMT.N"]

# Two vintages straddling most of a fiscal year, both asking about the SAME
# fiscal period. Wide enough that "no change" cannot be explained by a quiet
# stretch with no revisions.
EARLY_SDATE = "2024-02-15"
LATE_SDATE = "2024-11-15"
PERIOD = "FY2024"

# Candidate identifiers — all UNVERIFIED. The script reports which ones return
# anything at all, which is itself useful output.
CANDIDATE_FIELDS = [
    "TR.EPSMean",
    "TR.EPSMeanEstimate",
    "TR.RevenueMean",
    "TR.EpsSmartEst",
]


def main() -> int:
    universe = sys.argv[1:] or DEFAULT_UNIVERSE
    if not os.environ.get("REFINITIV_APP_KEY"):
        print("REFINITIV_APP_KEY not visible to this process — open a NEW shell.")
        return 1
    try:
        import lseg.data as ld
    except ImportError:
        try:
            import refinitiv.data as ld
        except ImportError:
            print("no LSEG library importable — pip install lseg-data")
            return 1

    try:
        ld.open_session()
    except Exception as e:
        print(f"session failed: {type(e).__name__}: {e}")
        print("A desktop session needs Workspace RUNNING and logged in.")
        return 1
    print(f"session open | universe={universe} | period={PERIOD}")
    print(f"comparing SDate={EARLY_SDATE}  vs  SDate={LATE_SDATE}\n")

    working: list[str] = []
    for f in CANDIDATE_FIELDS:
        try:
            probe = ld.get_data(universe=universe[:1], fields=[f])
            if probe is not None and probe.shape[0]:
                working.append(f)
                print(f"  field OK       {f}")
            else:
                print(f"  field EMPTY    {f}")
        except Exception as e:
            print(f"  field FAILED   {f}  ({type(e).__name__})")
    if not working:
        print("\nNo candidate estimate field returned data. Either none of these "
              "identifiers is right, or I/B/E/S is not entitled on this key. "
              "Check the Data Item Browser (type DIB in Workspace) for the exact "
              "consensus field names and re-run.")
        return 1

    print(f"\nusing {len(working)} working field(s): {working}\n")
    verdict_rows = []
    for f in working:
        try:
            early = ld.get_data(universe=universe, fields=[f],
                                parameters={"SDate": EARLY_SDATE, "Period": PERIOD})
            late = ld.get_data(universe=universe, fields=[f],
                               parameters={"SDate": LATE_SDATE, "Period": PERIOD})
        except Exception as e:
            print(f"{f}: parameterised call FAILED — {type(e).__name__}: {e}")
            print("   (if this rejects 'Period', retry with SDate only)")
            continue

        col = [c for c in early.columns if c.lower() != "instrument"]
        if not col:
            print(f"{f}: no value column returned")
            continue
        c = col[0]
        print(f"--- {f} (column {c!r}) ---")
        for i, ric in enumerate(universe):
            try:
                e_val = early[c].iloc[i]
                l_val = late[c].iloc[i]
            except Exception:
                continue
            same = str(e_val) == str(l_val)
            print(f"   {ric:10s} early={e_val!s:>14s}  late={l_val!s:>14s}  "
                  f"{'IDENTICAL' if same else 'DIFFERENT'}")
            verdict_rows.append(same)

    print("\n" + "=" * 68)
    if not verdict_rows:
        print("INCONCLUSIVE — no comparable values came back. Confirm the field")
        print("names in the Data Item Browser before drawing any conclusion.")
        return 1
    n_same = sum(verdict_rows)
    if n_same == len(verdict_rows):
        print("VERDICT: every value IDENTICAL across an 9-month SDate gap.")
        print("  -> SDate is a PERIOD SELECTOR, not an as-of vintage.")
        print("  -> The TR route is a LEAK. Do NOT use it for replay dates.")
        print("  -> Consensus is usable for LIVE screening only; point-in-time")
        print("     must come from forward-accumulated snapshots (start now) or")
        print("     from the I/B/E/S history product via the account team.")
    elif n_same == 0:
        print("VERDICT: every value DIFFERS across the SDate gap.")
        print("  -> SDate looks like a REAL as-of vintage parameter.")
        print("  -> Point-in-time consensus is achievable. Next: establish how")
        print("     far back it goes, and sanity-check one value against a known")
        print("     revision before trusting it.")
    else:
        print(f"VERDICT: MIXED — {n_same}/{len(verdict_rows)} identical.")
        print("  -> Do not generalise. Some names may simply not have been")
        print("     revised, or coverage differs. Re-run with more names and a")
        print("     wider date gap before concluding anything.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
