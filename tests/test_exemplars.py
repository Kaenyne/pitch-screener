"""Exemplar snapshot/diff harness tests.

The harness is the instrument every later scoring change is judged on, so its
failure mode matters: a diff that silently misses a cohort drop, or an
invariant that hard-codes a score, would make the whole tuning loop lie.
"""

import pandas as pd

from screener import exemplars


def _scored():
    return pd.DataFrame({
        "ticker": ["AAA", "CAKE", "VITL", "BBB"],
        "short_composite": [80.0, 54.4, 62.0, 40.0],
        "long_composite": [30.0, 45.0, 20.0, 70.0],
        "ppi_windfall": [False, False, True, False],
        "restated": [True, True, False, False],
        "n_thesis_tags": [3, 2, 2, 0],
    })


def _outputs(df):
    return {
        "universe_ranked": df.sort_values("short_composite", ascending=False),
        "consumer_shorts": df[df["ticker"].isin(["VITL", "CAKE"])],
        "pitchable_shorts": df[df["ticker"] == "AAA"],
        "empty_cohort": df.iloc[0:0],
    }


class TestSnapshot:
    def test_ranks_and_percentiles(self):
        df = _scored()
        s = exemplars.snapshot(df, _outputs(df), ["CAKE", "VITL"], "live")
        assert s["n_scored"] == 4
        assert s["exemplars"]["VITL"]["short_composite"] == 62.0
        assert s["exemplars"]["VITL"]["short_rank"] == 2
        assert s["exemplars"]["CAKE"]["short_rank"] == 3

    def test_records_cohort_membership_not_just_composite(self):
        """Both exemplars miss the composite-ranked outputs and are surfaced
        only by cohorts — a composite-only snapshot would hide that."""
        df = _scored()
        s = exemplars.snapshot(df, _outputs(df), ["CAKE", "VITL"], "live")
        d = s["exemplars"]["CAKE"]["deliverables"]
        assert d["consumer_shorts"]["rank"] == 1
        assert "pitchable_shorts" not in d

    def test_empty_cohorts_excluded(self):
        df = _scored()
        s = exemplars.snapshot(df, _outputs(df), ["CAKE"], "live")
        assert "empty_cohort" not in s["deliverables"]

    def test_tags_are_true_booleans_only(self):
        df = _scored()
        s = exemplars.snapshot(df, _outputs(df), ["CAKE", "VITL"], "live")
        assert s["exemplars"]["VITL"]["tags"] == ["ppi_windfall"]
        assert s["exemplars"]["CAKE"]["tags"] == ["restated"]

    def test_missing_ticker_marked_absent(self):
        df = _scored()
        s = exemplars.snapshot(df, _outputs(df), ["NOPE"], "live")
        assert s["exemplars"]["NOPE"] == {"present": False}


class TestDiff:
    def _pair(self, mutate):
        df = _scored()
        base = exemplars.snapshot(df, _outputs(df), ["CAKE", "VITL"], "live")
        df2 = mutate(df.copy())
        new = exemplars.snapshot(df2, _outputs(df2), ["CAKE", "VITL"], "live")
        return exemplars.diff(base, new)

    def test_identical_runs_report_no_change(self):
        assert self._pair(lambda d: d) == []

    def test_score_move_reported(self):
        lines = self._pair(lambda d: d.assign(
            short_composite=[80.0, 75.0, 62.0, 40.0]))
        assert any("CAKE" in ln for ln in lines)
        assert any("54.4 -> 75.0" in ln for ln in lines)

    def test_sub_epsilon_move_is_noise(self):
        lines = self._pair(lambda d: d.assign(
            short_composite=[80.0, 54.41, 62.0, 40.0]))
        assert lines == []

    def test_cohort_drop_reported(self):
        df = _scored()
        base = exemplars.snapshot(df, _outputs(df), ["CAKE"], "live")
        out2 = _outputs(df)
        out2["consumer_shorts"] = df[df["ticker"] == "VITL"]
        new = exemplars.snapshot(df, out2, ["CAKE"], "live")
        lines = exemplars.diff(base, new)
        assert any("DROPPED from consumer_shorts" in ln for ln in lines)

    def test_tag_changes_reported(self):
        lines = self._pair(lambda d: d.assign(
            ppi_windfall=[False, False, False, False]))
        assert any("tags lost:   ppi_windfall" in ln for ln in lines)

    def test_mismatched_label_refuses_to_compare(self):
        df = _scored()
        a = exemplars.snapshot(df, _outputs(df), ["CAKE"], "2025-10-15")
        b = exemplars.snapshot(df, _outputs(df), ["CAKE"], "2026-04-10")
        assert "not comparable" in exemplars.diff(a, b)[0]


class TestInvariants:
    def test_absent_exemplar_fails(self):
        df = _scored()
        s = exemplars.snapshot(df, _outputs(df), ["NOPE"], "live")
        assert exemplars.check_invariants(s, ["NOPE"])

    def test_present_exemplars_pass(self):
        df = _scored()
        s = exemplars.snapshot(df, _outputs(df), ["CAKE", "VITL"], "live")
        assert exemplars.check_invariants(s, ["CAKE", "VITL"]) == []

    def test_no_score_targets_baked_in(self):
        """A legitimate improvement must not fail the invariants — only the
        diff should speak. Moving CAKE from 54.4 to 90 is still valid."""
        df = _scored().assign(short_composite=[80.0, 90.0, 62.0, 40.0])
        s = exemplars.snapshot(df, _outputs(df), ["CAKE", "VITL"], "live")
        assert exemplars.check_invariants(s, ["CAKE", "VITL"]) == []


class TestRoundTrip:
    def test_json_safe(self, tmp_path):
        df = _scored()
        s = exemplars.snapshot(df, _outputs(df), ["CAKE", "VITL"], "live")
        p = exemplars.save(s, tmp_path / "snap.json")
        assert exemplars.load(p) == s

    def test_load_missing_returns_none(self, tmp_path):
        assert exemplars.load(tmp_path / "nope.json") is None
