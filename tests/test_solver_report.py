"""Tests for how solver effort is reported.

The numbers in these cases are real, measured across the circuits Micro-Cap
ships. They exist because the first version of this reporting was wrong in a
way that looked right: it warned on any non-zero rejection count, which fires
on 27% of all runs and on 100% of the Off-Line Converters — where rejecting
timesteps is exactly what a correct solver does.
"""

from __future__ import annotations

from microcap_mcp.runner import RunStats
from microcap_mcp.server import _solver_report


def stats(**kw) -> RunStats:
    base = dict(circuit="X.CIR", analysis="/T", nodes=10, iterations=100,
                rejected=0, solutions=50, run_time=1.0)
    base.update(kw)
    return RunStats(**base)


def test_linear_circuit_is_quiet():
    """BPFILT, measured: 14290 accepted, 0 rejected."""
    r = _solver_report(stats(nodes=12, iterations=28580, solutions=14290, rejected=0))
    assert r["rejected_fraction"] == 0.0
    assert r["iterations_per_solution"] == 2.0
    assert "note" not in r, "a clean linear run must not be annotated"


def test_switching_converter_is_explained_not_condemned():
    """Test Buck Cycle-by-Cycle CM, measured: 443236 accepted, 129272 rejected.

    The raw count looks catastrophic and is not: it is 22.6% of a very long
    run, in line with every other converter.
    """
    r = _solver_report(stats(nodes=27, iterations=1663839, solutions=443236, rejected=129272))
    assert r["rejected_fraction"] == 0.226
    assert "note" in r
    note = r["note"].lower()
    assert "expected" in note, "must not read as an alarm"
    assert "switching" in note
    assert "not by itself a reason to distrust" in note


def test_raw_count_does_not_drive_the_verdict():
    """A long healthy run and a short one get the same verdict when their
    ratios match — ranking by raw count just ranks by run length.
    """
    long_run = _solver_report(stats(solutions=443236, rejected=129272))
    short_run = _solver_report(stats(solutions=4432, rejected=1292))
    assert long_run["rejected_fraction"] == short_run["rejected_fraction"]
    assert ("note" in long_run) == ("note" in short_run)


def test_a_multivibrator_looks_like_a_switcher():
    """555ASTAB, measured: 570 accepted, 116 rejected = 17%.

    The ratio tracks fast edges, not converter topology specifically — so the
    wording must not claim "this is a converter".
    """
    r = _solver_report(stats(nodes=27, iterations=2250, solutions=570, rejected=116))
    assert r["rejected_fraction"] == 0.169
    assert "note" in r
    assert "multivibrator" in r["note"].lower()


def test_a_few_rejections_are_not_worth_mentioning():
    """OPAMP1, measured: 1027 accepted, 2 rejected."""
    r = _solver_report(stats(iterations=2173, solutions=1027, rejected=2))
    assert r["rejected_fraction"] == 0.002
    assert "note" not in r


def test_iterations_per_solution_is_reported():
    """2 is an easy linear circuit; 5.4 was the hardest thing in the library."""
    r = _solver_report(stats(iterations=493512, solutions=91382, rejected=26036))
    assert r["iterations_per_solution"] == 5.4


def test_zero_solutions_does_not_divide_by_zero():
    r = _solver_report(stats(iterations=0, solutions=0, rejected=0))
    assert "iterations_per_solution" not in r
    assert "rejected_fraction" not in r
    assert "note" not in r


def test_missing_fields_are_tolerated():
    r = _solver_report(stats(nodes=None, iterations=None, solutions=None, rejected=None))
    assert r["accepted_solutions"] == 0 and r["rejected_solutions"] == 0
