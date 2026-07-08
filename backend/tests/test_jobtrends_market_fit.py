"""Unit tests for the market-fit engine's pure verdict/trajectory logic."""

from app.jobtrends.market_fit import SkillSignal, _comp_verdict, _signal, _trajectory


def test_comp_verdict_buckets() -> None:
    # band: p25=150k, median=180k, p75=210k
    assert _comp_verdict(None, 150000, 180000, 210000) == ("unknown", None)
    assert _comp_verdict(200000, 0, 0, 0) == ("unknown", None)  # no market data
    v, d = _comp_verdict(120000, 150000, 180000, 210000)
    assert v == "under" and d < 0  # below p25
    v, d = _comp_verdict(180000, 150000, 180000, 210000)
    assert v == "fit" and d == 0.0  # exactly median
    v, d = _comp_verdict(300000, 150000, 180000, 210000)
    assert v == "over" and d > 0  # above p75


def test_comp_verdict_delta_is_signed_pct_of_median() -> None:
    _, d = _comp_verdict(90000, 150000, 180000, 210000)
    assert d == round(100.0 * (90000 - 180000) / 180000, 1)  # -50.0


def test_trajectory_thresholds() -> None:
    assert _trajectory(None) == "flat"
    assert _trajectory(0.2) == "flat"
    assert _trajectory(0.5) == "rising"
    assert _trajectory(3.1) == "rising"
    assert _trajectory(-0.6) == "falling"


class _KT:
    def __init__(self, shares: list, mom: float | None) -> None:
        self.shares = shares
        self.mom_delta_pts = mom


def test_signal_uses_latest_non_null_share() -> None:
    s = _signal("agents", "ai", _KT([2.0, None, 13.0, None], 6.0))
    assert isinstance(s, SkillSignal)
    assert s.skill == "agents"
    assert s.demand_share == 13.0  # latest non-null
    assert s.mom_delta_pts == 6.0
    assert s.trajectory == "rising"


def test_signal_handles_missing_trend() -> None:
    s = _signal("cobol", "language", None)
    assert s.demand_share == 0.0
    assert s.mom_delta_pts is None
    assert s.trajectory == "flat"
