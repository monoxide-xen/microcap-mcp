"""End-to-end tests against a real Micro-Cap install.

The unit tests pin the text handling; these pin the whole stack — driver,
batch mode, parser, server tools — against physics with a known answer. They
are the manual checks made throughout development, made permanent.

Skipped automatically where Micro-Cap is not installed (so CI stays green), so
run them locally with MICROCAP_HOME set:

    MICROCAP_HOME=D:/Games/MC12 uv run pytest tests/test_integration.py
"""

from __future__ import annotations

import math

import pytest

from microcap_mcp.runner import MicroCapError, find_install

try:
    find_install()
    HAVE_MC = True
except MicroCapError:
    HAVE_MC = False

pytestmark = pytest.mark.skipif(not HAVE_MC, reason="Micro-Cap not installed")


# Import the server tools the way an MCP client calls them.
from microcap_mcp.server import (  # noqa: E402
    describe_example,
    search_examples,
    simulate,
    simulate_example,
    sweep,
)

RC_LOWPASS = """RC Lowpass
V1 IN 0 AC 1
R1 IN OUT 1K
C1 OUT 0 159.155N
.AC DEC 21 10 100K
.PRINT AC V(OUT)
.END
"""


def test_rc_lowpass_matches_theory():
    """1/(2*pi*R*C) = 1000 Hz, where |H| must be 1/sqrt(2). The anchor check
    that everything downstream — batch mode, parsing, sampling — is faithful.
    """
    r = simulate(RC_LOWPASS, analysis="ac", max_points=200)
    assert "error" not in r, r
    f, mag = r["data"]["F"], r["data"]["V(OUT)"]
    i = min(range(len(f)), key=lambda k: abs(f[k] - 1000))
    assert mag[i] == pytest.approx(0.70711, abs=5e-3)
    assert r["solver"]["nodes"] == 2


def test_a_clean_run_reports_no_solver_distress():
    r = simulate(RC_LOWPASS, analysis="ac")
    assert r["solver"]["rejected_fraction"] == 0.0
    assert "note" not in r["solver"], "a linear circuit must not be flagged"


def test_sweep_moves_the_cutoff():
    """Raising R lowers the RC cutoff, so the gain at a fixed frequency drops.
    Exercises the parametric path end to end.
    """
    # A single-point AC run yields no table, so span a few points around the
    # 1 kHz cutoff and compare the middle sample.
    deck = """RC sweep
.DEFINE RL 1K
V1 IN 0 AC 1
R1 IN OUT {RL}
C1 OUT 0 159.155N
.AC LIN 3 900 1100
.PRINT AC V(OUT)
.END
"""
    s = sweep(deck, parameter="RL", values=["1K", "10K"], analysis="ac", max_points=3)
    g1 = s["runs"][0]["data"]["V(OUT)"][1]
    g10 = s["runs"][1]["data"]["V(OUT)"][1]
    assert g10 < g1, "a 10x larger R must attenuate more at the old cutoff"


def test_reference_first_workflow():
    """The workflow the guide prescribes: search -> describe -> simulate."""
    hits = search_examples("filter")
    assert hits, "the corpus should contain filters"
    name = next(h["name"] for h in hits if h["domain"] == "Filters")

    described = describe_example(name)
    assert "analyses" in described or described.get("format") == "spice_netlist"

    r = simulate_example(name, analysis="ac", max_points=50)
    assert "error" not in r, r
    assert r["points"] > 1


def test_oscillator_actually_oscillates():
    """555 astable: with the points override it must swing, not sit flat — the
    silent-failure trap the guide warns about, verified not to bite.
    """
    r = simulate_example("555ASTAB", analysis="transient", max_points=200)
    assert "error" not in r, r
    v = [x for x in r["data"]["v(OUT)"] if x is not None]
    assert max(v) - min(v) > 3.0, "the oscillator must swing, not sit flat"


def test_complex_s_parameter_output():
    """S-parameter analysis returns complex values as {re, im}."""
    r = simulate_example("smith", analysis="ac", max_points=20)
    assert "error" not in r, r
    s = next(c for c in r["columns"] if c != "F")
    cell = r["data"][s][0]
    assert isinstance(cell, dict) and "re" in cell and "im" in cell
    assert abs(complex(cell["re"], cell["im"])) > 0


def test_bad_netlist_returns_error_not_crash():
    r = simulate("Junk\nXX nonsense\n.PRINT TRAN V(1)\n.END", analysis="transient")
    assert "error" in r and isinstance(r["error"], str)


def test_missing_print_is_refused_with_guidance():
    r = simulate("X\nV1 1 0 AC 1\nR1 1 0 1K\n.AC DEC 10 1 1K\n.END", analysis="ac")
    assert "error" in r and ".PRINT" in r["error"]
