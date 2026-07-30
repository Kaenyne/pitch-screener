"""Refinitiv / LSEG data adapter — point-in-time safe by construction.

Adds what SEC companyfacts + yfinance structurally cannot provide, ranked by how
many competition-winning pitches each unblocks (measured over 80 pitches):

  1. I/B/E/S consensus estimates WITH HISTORY  — 9 of 80 pitches turned on a
     "Street models X, we model Y" variant view. The screener has no forward
     estimate of any kind today, so it cannot express one at all.
  2. Segment revenue / EBIT                    — ~11% of pitches (SOTP). Verified
     absent from companyfacts: observations carry only
     {accn,end,filed,form,fp,frame,fy,start,val} and the sole segment-named tags
     are NumberOfReportableSegments / NumberOfOperatingSegments, which are counts.
  3. Real peer sets                            — 31% of pitches turn on the comp
     set, and 3-digit SIC provably fails as a proxy (winning longs sit at the
     100th percentile of their SIC peers on valuation).
  4. Historical short interest                 — the replay harness has none, so
     its SI leg is zeroed in every historical replay.

THE CENTRAL DESIGN CONSTRAINT — read before touching the cache
--------------------------------------------------------------
Everything here is fetched to support REPLAYS of past dates. Unlike a price or a
filed fact, a consensus estimate is a *moving* quantity: what the Street expected
for BBWI on 2024-12-05 is not what it expects now. So every cached value is keyed
by the as-of date it describes, and `get_asof` will NEVER serve a row whose asof
differs from the one requested.

This is not defensive over-engineering. This screener already shipped a lookahead
leak — `edgar.py` anchored its filing windows on wall-clock today, so a replay of
2025-10-15 saw 144,768 filings that did not exist yet and flipped `non_reliance`
on 30 of ~39 firing names. A ticker-keyed estimate cache would be the same bug in
a worse place, because a leaked forward estimate does not look like a bug — it
looks like signal.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import config

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / config.CACHE_DIR / "refinitiv"
_OFFLINE = False
_SESSION = None
_SESSION_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# FIELD IDENTIFIERS — the single place any identifier appears.
#
# STATUS: PENDING VERIFICATION. These are placeholders in the documented TR.*
# shape; a parallel verification pass is confirming the exact identifiers,
# the point-in-time parameter names, and the real rate limits against the LSEG
# developer docs. Correct THIS BLOCK when that lands — nothing else should need
# to change, which is the point of keeping them here.
#
# Do not let a plausible-looking identifier through unverified. A wrong field
# name mostly returns an empty frame, and an empty frame read as "no data for
# this name" is exactly how a silent coverage hole gets into a composite.
# ---------------------------------------------------------------------------
FIELDS: dict[str, dict[str, str]] = {
    "consensus": {
        "eps_mean": "TR.EPSMean",
        "eps_num_estimates": "TR.EPSNumberOfEstimates",
        "eps_std_dev": "TR.EPSStdDev",
        "rev_mean": "TR.RevenueMean",
        "rev_num_estimates": "TR.RevenueNumberOfEstimates",
        "target_price": "TR.PriceTargetMean",
    },
    "segment": {
        "seg_name": "TR.SegmentName",
        "seg_revenue": "TR.SegmentRevenue",
        "seg_operating_income": "TR.SegmentOperatingIncome",
    },
    "peers": {
        "peer_ric": "TR.PeerRIC",
    },
    "short_interest": {
        "shares_short": "TR.ShortInterest",
        "pct_float_short": "TR.PctOfSharesOutShort",
    },
}

# VERIFIED desktop-session limits (Eikon Data API Usage and Limits Guideline,
# still the current cited figures). All AGGREGATED across every client app on the
# same Workspace instance — not per-app — so another tool of yours eats the same
# budget:
#     5 requests/sec | 10,000 requests/day | 50 MB/min | 5 GB/day
# Platform/RDP limits are entitlement-dependent and NOT published; do not assume
# these carry over to a cloud contract.
REQUESTS_PER_SEC = 5
MIN_REQUEST_INTERVAL_S = 1.0 / (REQUESTS_PER_SEC - 1)   # 4/s, one under the cap
DAILY_REQUEST_CAP = 10_000
MAX_RICS_PER_CALL = 50
MAX_RETRIES = 3

# Throttling returns HTTP 429 with "Too many requests" — loud, catchable.
# DATAPOINT limits are the dangerous ones. Verbatim from LSEG: "responses are
# simply truncated and only the available cells/headlines/results are returned".
# No exception, no 429, no flag. A truncated frame is byte-for-byte identical to
# a legitimately short one, so every call MUST be checked for completeness
# against what was requested — see _assert_complete.
SILENT_TRUNCATION = True


class RefinitivUnavailable(RuntimeError):
    """Raised instead of returning empty data.

    A missing key, an un-launched desktop terminal and a lapsed entitlement all
    produce empty frames from the underlying library. Empty frames flow into the
    screener as 'this name has no estimates', which is indistinguishable from
    genuine no-coverage and would quietly bias every cohort that reads the field.
    Fail loudly instead.
    """


class _RateLimiter:
    """Minimum-interval limiter, mirroring edgar.RateLimiter."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


_LIMITER = _RateLimiter(MIN_REQUEST_INTERVAL_S)


def set_offline(flag: bool = True) -> None:
    """Forbid network access — cache or nothing.

    The replay harness runs with this on so a rerun of a past date is
    reproducible, exactly as `market.set_fred_offline` does for FRED.
    """
    global _OFFLINE
    _OFFLINE = bool(flag)


def app_key() -> str:
    """The app key, read at call time rather than import time.

    `setx` only affects processes started afterwards, so a long-lived shell can
    hold a stale environment while the key is perfectly well set.
    """
    return os.environ.get("REFINITIV_APP_KEY", "") or config.REFINITIV_APP_KEY


def session_kind() -> str:
    return (os.environ.get("REFINITIV_SESSION", "")
            or config.REFINITIV_SESSION or "desktop").lower()


def _open_session():
    """Lazy session open. Desktop proxies through a running Workspace/Eikon on
    localhost; platform (EDP/RDP) is machine-to-machine and is the only one that
    survives an unattended overnight batch."""
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is not None:
            return _SESSION
        key = app_key()
        if not key:
            raise RefinitivUnavailable(
                "REFINITIV_APP_KEY is empty in this process. It is set at user "
                "level, but `setx` only reaches processes started after it ran — "
                "open a new shell, or launch via a fresh cmd/PowerShell.")
        try:
            import lseg.data as ld            # current package
        except ImportError:
            try:
                import refinitiv.data as ld   # previous name
            except ImportError as e:
                raise RefinitivUnavailable(
                    "No LSEG/Refinitiv library importable. Install the one the "
                    "verification pass confirms (lseg-data, or refinitiv-data on "
                    "older installs). The legacy `eikon` package is deprecated."
                ) from e
        kind = session_kind()
        try:
            if kind == "desktop":
                _SESSION = ld.open_session(name="desktop.workspace")
            else:
                _SESSION = ld.open_session(name="platform.rdp")
        except Exception as e:
            raise RefinitivUnavailable(
                f"could not open a {kind} session: {e}. For a desktop session "
                f"Workspace/Eikon must be RUNNING and logged in on this machine."
            ) from e
        logger.info("Refinitiv %s session opened", kind)
        return _SESSION


# ---------------------------------------------------------------------------
# As-of keyed disk cache
# ---------------------------------------------------------------------------

def _cache_path(dataset: str) -> Path:
    return CACHE_DIR / f"{dataset}.parquet"


def _cache_read(dataset: str) -> pd.DataFrame:
    p = _cache_path(dataset)
    cols = ["ric", "field", "asof", "period", "value", "fetched_at"]
    if not p.exists():
        return pd.DataFrame(columns=cols)
    try:
        return pd.read_parquet(p)
    except Exception as e:
        logger.debug("refinitiv cache read failed (%s): %s", p, e)
        return pd.DataFrame(columns=cols)


def _cache_write(dataset: str, rows: pd.DataFrame) -> None:
    if not len(rows):
        return
    p = _cache_path(dataset)
    p.parent.mkdir(parents=True, exist_ok=True)
    old = _cache_read(dataset)
    both = pd.concat([old, rows], ignore_index=True)
    # newest fetch wins for an identical (ric, field, asof, period) — a revision
    # to a HISTORICAL consensus is a real thing and should overwrite; a different
    # asof is a different observation and must never collide.
    both = (both.sort_values("fetched_at")
                .drop_duplicates(subset=["ric", "field", "asof", "period"],
                                 keep="last"))
    try:
        both.to_parquet(p, index=False)
    except Exception as e:
        logger.warning("refinitiv cache write FAILED (%s): %s", p, e)
        # Mirrors the pipeline raw-row guard: a stale cache that silently serves
        # the wrong as-of is worse than no cache at all.
        try:
            if p.exists():
                p.unlink()
                logger.warning("removed the now-stale %s", p)
        except OSError:
            pass


def cached_asof(dataset: str, rics: list[str], fields: list[str],
                asof: str) -> pd.DataFrame:
    """Cached rows for EXACTLY this as-of date. Never falls back to another date.

    The no-fallback rule is the leak guard: serving 2026 consensus for a 2024
    replay would manufacture a signal out of information that did not exist.
    """
    c = _cache_read(dataset)
    if not len(c):
        return c
    return c[c["ric"].isin(rics) & c["field"].isin(fields)
             & (c["asof"] == str(asof)[:10])].copy()


def get_asof(dataset: str, rics: list[str], field_keys: list[str],
             asof: str, offline: bool | None = None) -> pd.DataFrame:
    """Point-in-time accessor: values as they stood on `asof`.

    Returns long format (ric, field, asof, period, value). Cache-first for the
    exact as-of date, network for the remainder, and a hard refusal to substitute
    a different date. With `offline` the network is forbidden entirely and
    whatever is cached is returned as-is — reproducibility beats freshness when
    the whole point is rerunning a past date.
    """
    offline = _OFFLINE if offline is None else offline
    asof = str(asof)[:10]
    spec = FIELDS.get(dataset)
    if not spec:
        raise KeyError(f"unknown dataset {dataset!r}; have {sorted(FIELDS)}")
    unknown = [k for k in field_keys if k not in spec]
    if unknown:
        raise KeyError(f"unknown field keys for {dataset}: {unknown}")
    fields = [spec[k] for k in field_keys]

    have = cached_asof(dataset, rics, fields, asof)
    missing = sorted(set(rics) - set(have["ric"])) if len(have) else list(rics)
    if not missing or offline:
        if missing and offline:
            logger.info("refinitiv offline: %d/%d RICs uncached at %s",
                        len(missing), len(rics), asof)
        return have

    fetched = _fetch_asof(dataset, missing, fields, asof)
    if len(fetched):
        _cache_write(dataset, fetched)
    return pd.concat([have, fetched], ignore_index=True) if len(fetched) else have


def _fetch_asof(dataset: str, rics: list[str], fields: list[str],
                asof: str) -> pd.DataFrame:
    """Paged, rate-limited fetch. Returns long format, empty on total failure.

    PENDING VERIFICATION: the point-in-time parameter names are the load-bearing
    unknown here. The verification pass is confirming how to request "consensus
    as it stood on date D" (SDate/EDate/Period/Frq or otherwise). Until that is
    confirmed this raises rather than guessing, because a call that silently
    returns TODAY's consensus while we believe it returned the as-of value is the
    single worst failure mode available to this module.
    """
    raise RefinitivUnavailable(
        "point-in-time request syntax not yet verified — refusing to fetch. "
        "A call that quietly returns today's consensus instead of the as-of "
        "value would inject a lookahead leak that reads as signal. Fill in "
        "_fetch_asof once the verification pass confirms the parameter names.")


def _assert_complete(got: pd.DataFrame, requested_rics: list[str],
                     requested_fields: list[str], context: str) -> None:
    """Guard against SILENT TRUNCATION — the most dangerous property of this API.

    LSEG, verbatim: "responses are simply truncated and only the available
    cells/headlines/results are returned". No exception, no 429, no flag. A
    truncated frame is byte-for-byte identical to a legitimately short one.

    So completeness has to be checked against what was ASKED FOR, not inferred
    from the response. Missing RICs are reported explicitly rather than being
    left to look like genuine no-coverage — the same distinction that made the
    stale raw-row cache and the empty-frame-as-no-data failures so expensive.
    """
    if not len(got):
        raise RefinitivUnavailable(
            f"{context}: empty response for {len(requested_rics)} RICs. Empty is "
            f"not 'no coverage' — it is also what a wrong field name, an "
            f"un-launched terminal and a lapsed entitlement return.")
    returned = set(got["ric"]) if "ric" in got.columns else set()
    missing = [r for r in requested_rics if r not in returned]
    if missing:
        frac = len(missing) / len(requested_rics)
        msg = (f"{context}: {len(missing)}/{len(requested_rics)} RICs absent "
               f"({frac:.0%}). Could be genuine no-coverage OR silent "
               f"truncation — these are indistinguishable in the response.")
        if frac > 0.5:
            raise RefinitivUnavailable(
                msg + " Over half missing: treating as truncation, not coverage.")
        # pre-format: `msg` contains a literal '%' from the percentage, which the
        # logger's lazy %-interpolation would try to parse as a format spec
        logger.warning("%s First few: %s", msg, missing[:8])


def coverage_report(dataset: str) -> pd.DataFrame:
    """What is actually cached, by as-of date — so a replay can state its own
    coverage instead of quietly running on thin data."""
    c = _cache_read(dataset)
    if not len(c):
        return pd.DataFrame(columns=["asof", "n_rics", "n_fields", "n_values"])
    g = c.groupby("asof").agg(n_rics=("ric", "nunique"),
                              n_fields=("field", "nunique"),
                              n_values=("value", "size")).reset_index()
    return g.sort_values("asof")
