"""Tests for the batch log and install detection.

Both are pure logic — no Micro-Cap needed. Simulation itself is exercised by
`eval/harness.py` against a real install.
"""

from __future__ import annotations

import pytest

from microcap_mcp.runner import ANALYSES, BatchLog, MicroCapError, find_install, parse_log

# A real batch log: three runs of one circuit, plus the summary block.
LOG = """D:\\MC12\\Batch.bat 17.07.2026 1:03:21
Circuit        Analog       Total   Rejected       Total       Run     Setup
                Nodes  Iterations  Solutions   Solutions      Time      Time
BATCH.CIR   /A      2        1004          0        1001     1.969     1.938
BATCH.CIR   /A      2        1004          0        1001     0.500     0.469
BATCH.CIR   /A      2        1004         37        1001     0.109     0.078
Total elapsed time             3 sec
Total Circuits\t\t\t          3
Total Simulations\t\t          3
Total runs with Error/Warnings 0
Total runs with Difa Errors    0
"""


def test_solver_statistics_are_parsed():
    log = parse_log(LOG)
    assert len(log.stats) == 3
    assert log.stats[0].nodes == 2
    assert log.stats[0].iterations == 1004
    assert log.stats[0].run_time == pytest.approx(1.969)
    assert log.stats[2].rejected == 37, "rejected solutions are the distress signal"


def test_summary_counters():
    log = parse_log(LOG)
    assert log.circuits == 3
    assert log.simulations == 3
    assert log.runs_with_errors == 0
    assert log.ok


def test_summary_line_mentioning_errors_is_not_an_error():
    """"Total runs with Error/Warnings 0" contains the word Error. Scanning
    for errors before handling the summary counts it as one.
    """
    assert parse_log(LOG).errors == []


def test_error_lines_are_prefixed_with_the_circuit_file():
    """Micro-Cap does not anchor its errors — matching `^Error` finds none."""
    log = parse_log(LOG + "MCP_1_3.CIR Error Can't plot noise with other expressions.\n")
    assert len(log.errors) == 1
    assert "noise" in log.errors[0]


def test_one_circuits_error_does_not_infect_its_neighbours():
    """A batch log holds every circuit's errors. Returning all of them to each
    circuit that produced no output invents a cause it never had — which made
    one broken circuit look like a hundred identical failures, and sent two
    rounds of debugging after a problem that did not exist.
    """
    log = BatchLog(
        errors=[
            "MCP_1_3.CIR Error Source not found.",
            "MCP_1_7.CIR Error Can't plot noise with other expressions.",
        ]
    )
    assert "Source not found" in log.why("MCP_1_3")
    assert "noise" not in log.why("MCP_1_3"), "cross-contamination"
    assert "noise" in log.why("MCP_1_7")
    assert "Source not found" not in log.why("MCP_1_9"), "innocent circuit inherited a fault"


def test_why_strips_our_scratch_name():
    """The caller never chose the temp file name; it means nothing to them."""
    log = BatchLog(errors=["MCP_1_3.CIR Error Source not found."])
    assert "MCP_1_3" not in log.why("MCP_1_3")


def test_why_is_honest_when_micro_cap_said_nothing():
    assert "no error text" in BatchLog().why("MCP_1_3")


def test_empty_log():
    log = parse_log("")
    assert log.stats == [] and log.errors == [] and not log.ok


# --------------------------------------------------------------------------
# analyses
# --------------------------------------------------------------------------


def test_distortion_and_stability_extensions_are_not_the_documented_ones():
    """The manual says numeric output is "(.TNO, .ANO, or .DNO)". It is wrong:
    trusting it made three analyses fail at exactly 0%.
    """
    assert ANALYSES["harmonic_distortion"] == ("/HD", "HNO")
    assert ANALYSES["intermodulation_distortion"] == ("/ID", "INO")
    assert ANALYSES["stability"] == ("/STABILITY", "SNO")


def test_the_documented_three_are_still_right():
    assert ANALYSES["transient"] == ("/T", "TNO")
    assert ANALYSES["ac"] == ("/A", "ANO")
    assert ANALYSES["dc"] == ("/D", "DNO")


# --------------------------------------------------------------------------
# install detection
# --------------------------------------------------------------------------


def test_explicit_path_without_an_executable_is_refused(tmp_path):
    """It must not fall through to the scan: a caller who named a folder wants
    that folder, and silently using another install would be worse than an error.
    """
    with pytest.raises(MicroCapError, match="mc12"):
        find_install(tmp_path)


def test_microcap_home_is_used_verbatim(tmp_path, monkeypatch):
    (tmp_path / "mc12_64.exe").write_bytes(b"")
    monkeypatch.setenv("MICROCAP_HOME", str(tmp_path))
    assert find_install() == tmp_path


def test_a_32_bit_only_install_is_accepted(tmp_path, monkeypatch):
    (tmp_path / "mc12.exe").write_bytes(b"")
    monkeypatch.setenv("MICROCAP_HOME", str(tmp_path))
    assert find_install() == tmp_path


def test_bad_microcap_home_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROCAP_HOME", str(tmp_path / "nope"))
    with pytest.raises(MicroCapError, match="MICROCAP_HOME"):
        find_install()


# --------------------------------------------------------------------------
# batch bisection
# --------------------------------------------------------------------------

from microcap_mcp.runner import bisect  # noqa: E402


def test_bisect_halves_a_batch():
    a, b = bisect(list(range(10)))
    assert a == list(range(5)) and b == list(range(5, 10))


def test_bisect_leaves_a_singleton_for_solo_rerun():
    """A lone leftover is not split further — the caller re-runs it on its own
    so its .DOC survives. 19 of 28 'silent' failures were healthy circuits that
    a bisecting batch had discarded without keeping their real diagnosis.
    """
    assert bisect([42]) == ([42], [])


def test_bisect_handles_empty():
    assert bisect([]) == ([], [])


def test_bisect_of_two_splits_one_and_one():
    assert bisect(["a", "b"]) == (["a"], ["b"])
