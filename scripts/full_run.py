"""Full universe run: EDGAR $1-20B US listings -> ranked CSVs + shortlists
+ tear sheets + first-run diagnostics."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener import pipeline


def main():
    t0 = time.time()
    res = pipeline.run(refresh_universe=True, workers=10)
    dt = (time.time() - t0) / 60
    print(f"\n=== FULL RUN COMPLETE in {dt:.1f} min ===", flush=True)
    print(f"universe: {res['universe_n']} | scored: {res['scored_n']} | "
          f"errors: {res['errors_n']} | tear sheets: {res['n_tearsheets']}")
    print("outputs:")
    for p in res["paths"]:
        print(" -", p)
    print("diagnostics:", res["diagnostics"])
    outs = res["outputs"]
    for k in ("long_shortlist", "short_shortlist", "long_financials",
              "short_financials"):
        n = len(outs.get(k, []))
        print(f"{k}: {n} names")
    ranked = outs["universe_ranked"]  # carries the overlap flags
    cols = [c for c in ("ticker", "name", "long_composite", "short_composite",
                        "cov_long", "false_moat_collision", "contested",
                        "on_both_lists") if c in ranked.columns]
    print("\nTop 15 LONG:")
    print(ranked.nlargest(15, "long_composite")[cols].to_string(index=False))
    print("\nTop 15 SHORT:")
    print(ranked.nlargest(15, "short_composite")[cols].to_string(index=False))


if __name__ == "__main__":
    main()
