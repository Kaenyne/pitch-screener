"""Refinitiv connectivity smoke test — run this yourself, in a NEW shell.

Two reasons it has to be you and not me: `setx` only reaches processes started
after it ran, so a long-lived agent shell holds a stale environment; and a desktop
session proxies through Workspace/Eikon, which has to be running and logged in on
your machine.

    python scripts/refinitiv_smoke.py

It escalates deliberately — key, import, session, then one trivial call — so a
failure tells you WHICH layer broke instead of just "no data". Nothing here writes
to the cache.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    print("=" * 66)
    print("Refinitiv / LSEG smoke test")
    print("=" * 66)

    # 1 — key visible to THIS process
    key = os.environ.get("REFINITIV_APP_KEY", "")
    if not key:
        print("\n[1/4] FAIL  REFINITIV_APP_KEY not visible to this process.")
        print("            It may still be set correctly: `setx` only reaches")
        print("            processes started AFTER it ran. Open a new terminal")
        print("            and rerun before doing anything else.")
        return 1
    print(f"\n[1/4] ok    key visible: {len(key)} chars, "
          f"{key[:2]}…{key[-2:]}  (value not printed)")

    # 2 — library importable, and which one
    lib = None
    for mod, label in (("lseg.data", "lseg-data (current)"),
                       ("refinitiv.data", "refinitiv-data (previous name)")):
        try:
            __import__(mod)
            lib = (mod, label)
            break
        except ImportError:
            continue
    if not lib:
        print("[2/4] FAIL  no LSEG/Refinitiv library importable.")
        print("            pip install lseg-data")
        print("            (the legacy `eikon` package is deprecated)")
        return 1
    print(f"[2/4] ok    {lib[1]}  ->  import {lib[0]}")

    # 3 — session opens
    import importlib
    ld = importlib.import_module(lib[0])
    from screener import refinitiv as rf
    kind = rf.session_kind()
    print(f"[3/4] ..    opening a '{kind}' session")
    try:
        if kind == "desktop":
            ld.open_session(name="desktop.workspace")
        else:
            ld.open_session(name="platform.rdp")
    except Exception as e:
        print(f"[3/4] FAIL  {type(e).__name__}: {e}")
        if kind == "desktop":
            print("            A desktop session needs Workspace/Eikon RUNNING")
            print("            and logged in on this machine. If it is running,")
            print("            check the App Key Generator lists this key as an")
            print("            'Eikon Data API' key specifically.")
        return 1
    print(f"[3/4] ok    {kind} session open")

    # 4 — one trivial call: does data actually come back?
    print("[4/4] ..    requesting one field for one instrument")
    try:
        df = ld.get_data(universe=["IBM.N"], fields=["TR.CommonName"])
        print(f"[4/4] ok    got {df.shape[0]} row(s), {df.shape[1]} col(s)")
        print(df.to_string(index=False))
    except Exception as e:
        print(f"[4/4] FAIL  {type(e).__name__}: {e}")
        print("            Session opened but the data call failed — usually an")
        print("            ENTITLEMENT issue rather than a connectivity one.")
        return 1

    print("\n" + "=" * 66)
    print("All four layers pass. Paste the output back and I will fill in the")
    print("verified field identifiers and the point-in-time request syntax.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
