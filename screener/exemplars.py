"""Exemplar snapshot / diff harness — the before-and-after any change is judged on.

CAKE and VITL are the only externally-validated exemplars (user direction:
never calibrate on names the screener itself surfaced). This module records
where they land on EVERY deliverable — composite score and rank, membership
and rank inside each cohort CSV, and the full set of tags fired — so a change
to scoring or to the cohort machinery can be read as a diff instead of a
re-derivation.

Recording the composite alone would be useless here: both exemplars miss
every composite-ranked short deliverable and are surfaced only by the tag
cohorts, so a cohort regression would show up as "no change".

Two consumers:
- scripts/replay.py   — point-in-time, honest, slow (the real test)
- scripts/rescore.py  — today's universe, fast (the tuning-loop check)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

# Externally validated only. UAMY and anything else the screener surfaced is
# deliberately absent — calibrating on those is circular.
DEFAULT_EXEMPLARS = ["CAKE", "VITL"]

# Columns worth printing per exemplar; missing ones are skipped.
DEFAULT_METRICS = [
    "short_composite", "long_composite", "false_moat_scored",
    "theme_inflated_growth", "theme_forensic", "si_bonus", "fm_peaked",
    "at_peak_score", "ig_runup", "runup_raw", "ig_pmv", "pmv_score_raw",
    "ig_richness", "ig_ev_norm", "ev_normalized", "ig_decel", "fo_sloan",
    "fo_beneish", "drawdown", "ret_12m", "revenue_context", "cov_short",
    "catalyst_bonus", "days_to_catalyst", "n_thesis_tags",
]

SNAPSHOT_NAME = "exemplar_snapshot.json"
BASELINE_NAME = "exemplar_baseline.json"
# Moves smaller than these are float-tie noise, not a change worth reading.
RANK_NOISE = 2
SCORE_EPS = 0.05


def _num(v):
    """JSON-safe scalar: bool stays bool, numbers round, NaN/inf -> None."""
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return None if not np.isfinite(f) else round(f, 4)


def tags_fired(row: pd.Series) -> list[str]:
    """Every boolean column True for this name. The cohort layer ranks on tag
    COUNT rather than score, so the tag set is a first-class output."""
    return sorted(str(c) for c, v in row.items()
                  if isinstance(v, (bool, np.bool_)) and bool(v))


def snapshot(scored: pd.DataFrame, outputs: dict, tickers: list[str],
             label: str, metrics: list[str] | None = None) -> dict:
    """Where each exemplar lands on every deliverable, as a diffable dict."""
    metrics = metrics or DEFAULT_METRICS
    n = len(scored)
    snap = {"asof": label, "n_scored": int(n),
            "deliverables": {k: int(len(v)) for k, v in outputs.items()
                             if v is not None and len(v)},
            "exemplars": {}}
    for t in tickers:
        hit = scored[scored["ticker"] == t] if "ticker" in scored.columns \
            else scored.iloc[0:0]
        if not len(hit):
            snap["exemplars"][t] = {"present": False}
            continue
        r = hit.iloc[0]
        e: dict = {"present": True}
        for side in ("short", "long"):
            col = f"{side}_composite"
            if col in scored.columns and pd.notna(r[col]):
                rank = int((scored[col] > r[col]).sum() + 1)
                e[col] = _num(r[col])
                e[f"{side}_rank"] = rank
                e[f"{side}_pctile"] = round(100.0 * (1 - rank / n), 1) if n else None
        member = {}
        for name, frame in outputs.items():
            if frame is None or not len(frame) or "ticker" not in frame.columns:
                continue
            tl = frame["ticker"].tolist()
            if t in tl:
                member[name] = {"rank": tl.index(t) + 1, "n": len(tl)}
        e["deliverables"] = dict(sorted(member.items()))
        e["tags"] = tags_fired(r)
        e["metrics"] = {c: _num(r[c]) for c in metrics if c in r.index}
        snap["exemplars"][t] = e
    return snap


def diff(base: dict, new: dict) -> list[str]:
    """Human-readable before/after. Empty list means nothing moved."""
    lines: list[str] = []
    if base.get("asof") != new.get("asof"):
        return [f"  !! baseline is for {base.get('asof')}, this run is "
                f"{new.get('asof')} — not comparable"]
    if base.get("n_scored") != new.get("n_scored"):
        lines.append(f"  universe: {base.get('n_scored')} -> "
                     f"{new.get('n_scored')} scored names")
    bd, nd = base.get("deliverables", {}), new.get("deliverables", {})
    for k in sorted(set(bd) | set(nd)):
        if bd.get(k) != nd.get(k):
            lines.append(f"  cohort {k}: {bd.get(k)} -> {nd.get(k)} rows")
    for t in sorted(set(base.get("exemplars", {})) | set(new.get("exemplars", {}))):
        b = base.get("exemplars", {}).get(t, {})
        v = new.get("exemplars", {}).get(t, {})
        sub: list[str] = []
        if b.get("present") != v.get("present"):
            sub.append(f"    PRESENT {b.get('present')} -> {v.get('present')}")
        for side in ("short", "long"):
            sc, rk = f"{side}_composite", f"{side}_rank"
            b_s, v_s, b_r, v_r = b.get(sc), v.get(sc), b.get(rk), v.get(rk)
            if b_s is not None and v_s is not None and abs(v_s - b_s) >= SCORE_EPS:
                sub.append(f"    {sc}: {b_s} -> {v_s}   rank {b_r} -> {v_r}")
            elif b_r is not None and v_r is not None and abs(v_r - b_r) > RANK_NOISE:
                sub.append(f"    {rk}: {b_r} -> {v_r}")
        b_d, v_d = b.get("deliverables", {}), v.get("deliverables", {})
        for k in sorted(set(b_d) | set(v_d)):
            if k not in v_d:
                sub.append(f"    DROPPED from {k} (was rank {b_d[k]['rank']})")
            elif k not in b_d:
                sub.append(f"    ADDED to {k} at rank {v_d[k]['rank']}")
            elif abs(v_d[k]["rank"] - b_d[k]["rank"]) > RANK_NOISE:
                sub.append(f"    {k}: rank {b_d[k]['rank']} -> {v_d[k]['rank']}")
        gone = sorted(set(b.get("tags", [])) - set(v.get("tags", [])))
        gained = sorted(set(v.get("tags", [])) - set(b.get("tags", [])))
        if gone:
            sub.append(f"    tags lost:   {', '.join(gone)}")
        if gained:
            sub.append(f"    tags gained: {', '.join(gained)}")
        if sub:
            lines.append(f"  {t}:")
            lines.extend(sub)
    return lines


def report(snap: dict, show_metrics: bool = True) -> None:
    for t, e in snap.get("exemplars", {}).items():
        if not e.get("present"):
            print(f"\n=== {t}: NO SCORED ROW at {snap['asof']} ===")
            continue
        print(f"\n=== {t} @ {snap['asof']}: "
              f"SHORT {e.get('short_composite')} rank {e.get('short_rank')}"
              f"/{snap['n_scored']} ({e.get('short_pctile')}th) | "
              f"LONG {e.get('long_composite')} rank {e.get('long_rank')} ===")
        d = e.get("deliverables", {})
        print("  deliverables: " + (", ".join(
            f"{k}#{v['rank']}/{v['n']}" for k, v in d.items())
            if d else "NONE — reaches no output file"))
        print(f"  tags ({len(e.get('tags', []))}): "
              f"{', '.join(e.get('tags', [])) or '—'}")
        if show_metrics:
            for c, v in e.get("metrics", {}).items():
                print(f"  {c}: {v}")


def check_invariants(snap: dict, tickers: list[str],
                     extra: list[str] | None = None) -> list[str]:
    """Structural checks only — deliberately NOT score targets. Freezing
    CAKE 54.4 / VITL 62.0 into an assertion would make every legitimate
    improvement fail the test; the diff is what carries that signal."""
    fails = []
    for t in tickers:
        if not snap.get("exemplars", {}).get(t, {}).get("present"):
            fails.append(f"{t}: no scored row at {snap['asof']} — the harness "
                         f"cannot measure anything about it")
    if not snap.get("deliverables"):
        fails.append("no deliverable CSVs were written")
    fails.extend(extra or [])
    return fails


def load(path: str | Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save(snap: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    return p


def emit(snap: dict, outdir: str | Path, set_baseline: bool = False,
         invariant_fails: list[str] | None = None) -> int:
    """Print the report, diff against the baseline, persist. Returns the
    number of invariant failures."""
    outdir = Path(outdir)
    report(snap)
    fails = invariant_fails or []
    print("\n--- invariants ---")
    for f in fails:
        print(f"  FAIL {f}")
    if not fails:
        print("  OK   all structural invariants hold")
    snap_path = save(snap, outdir / SNAPSHOT_NAME)
    base_path = outdir / BASELINE_NAME
    base = load(base_path)
    if base is not None:
        lines = diff(base, snap)
        print("\n--- vs baseline ---")
        print("\n".join(lines) if lines
              else "  no change on any exemplar or cohort")
    else:
        print("\n--- no baseline yet: pass --set-baseline to freeze this run "
              "as the reference ---")
    if set_baseline:
        save(snap, base_path)
        print(f"baseline written -> {base_path}")
    print(f"snapshot -> {snap_path}")
    return len(fails)
