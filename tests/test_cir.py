"""Tests for reading and preparing Micro-Cap `.CIR` schematics.

Pure text handling — no Micro-Cap needed.
"""

from __future__ import annotations

import pytest

from microcap_mcp import cir

# A miniature .CIR with the parts that matter: analysis limits, plotted traces,
# and the numeric-output switches. Shaped like the real thing.
SAMPLE = """[Main]
FileType=CIR
Version=12.00

[Limits]
Analysis=Transient
TMax=8
TStart=0
NPts=51
Temp=27

[Limits]
Analysis=AC
FRange=100K,1
NPts=1000
Temp=27

[Limits]
Analysis=DC
I1Range=10,0,.5
I1=V1
NPts=51

[Transient]
Num Out Low="TMIN"
Num Out High="TMAX"

[AC]
Num Out Low="FMIN"
Num Out High="FMAX"

[WaveForm]
Analysis=Transient
XExp=T
YExp=V(OUT)
Options=LINEARX,LINEARY

[WaveForm]
Analysis=AC
XExp=F
YExp=Mag(v(OUT))
Options=LINEARY

[WaveForm]
Analysis=AC
XExp=F
YExp=
Options=LINEARY
"""


def test_analyses_declared():
    assert cir.analyses(SAMPLE) == ["Transient", "AC", "DC"]


def test_expressions_reports_traces_and_export_state():
    exprs = cir.expressions(SAMPLE)
    assert [e.y for e in exprs] == ["V(OUT)", "Mag(v(OUT))"], "a blank YExp is not a trace"
    assert all(not e.exported for e in exprs)


def test_analysis_spellings_are_the_ones_micro_cap_uses():
    """Not guessable: these were surveyed across all 475 shipped circuits.
    Guessing "Harmonic Distortion" and "Dynamic DC" made those analyses fail
    at exactly 0%.
    """
    assert cir.CIR_ANALYSIS["harmonic_distortion"] == "HmDistortion"
    assert cir.CIR_ANALYSIS["intermodulation_distortion"] == "ImDistortion"
    assert cir.CIR_ANALYSIS["dynamic_ac"] == "DynamicAC"
    assert cir.CIR_ANALYSIS["dynamic_dc"] == "DynamicDC"


def test_dynamic_analyses_have_no_traces():
    """They annotate the schematic in place, so there is nothing to export."""
    assert cir.NO_TRACE_ANALYSES == {"dynamic_ac", "dynamic_dc"}


# --------------------------------------------------------------------------
# enabling numeric output
# --------------------------------------------------------------------------


def test_enable_numeric_output_switches_on_the_right_analysis():
    patched, n = cir.enable_numeric_output(SAMPLE, "ac")
    assert n == 1
    after = {e.y: e.exported for e in cir.expressions(patched)}
    assert after["Mag(v(OUT))"] is True
    assert after["V(OUT)"] is False, "the transient trace must be left alone"


def test_enable_numeric_output_is_idempotent():
    """It must report traces that *end up* exported, not ones it changed.

    Counting only changes returns 0 for an already-enabled circuit, and the
    caller concludes there is nothing to export — which rejected the 36
    shipped circuits that already had the flag set.
    """
    once, n1 = cir.enable_numeric_output(SAMPLE, "ac")
    twice, n2 = cir.enable_numeric_output(once, "ac")
    assert n1 == n2 == 1
    assert once == twice


def test_enable_numeric_output_when_there_is_no_options_line():
    text = "[WaveForm]\nAnalysis=AC\nXExp=F\nYExp=V(OUT)\n"
    patched, n = cir.enable_numeric_output(text, "ac")
    assert n == 1
    assert cir.expressions(patched)[0].exported


def test_unknown_analysis_is_an_error():
    with pytest.raises(KeyError):
        cir.enable_numeric_output(SAMPLE, "telepathy")


# --------------------------------------------------------------------------
# points
# --------------------------------------------------------------------------


def test_set_points_targets_one_analysis():
    """`NPts=0` exports a single row and makes an oscillator look dead."""
    patched = cir.set_points(SAMPLE, "transient", 200)
    assert "NPts=200" in patched
    assert "NPts=1000" in patched, "the AC block must keep its own NPts"


def test_set_points_adds_npts_when_missing():
    text = "[Limits]\nAnalysis=AC\nFRange=100K,1\n"
    assert "NPts=300" in cir.set_points(text, "ac", 300)


# --------------------------------------------------------------------------
# numeric range
# --------------------------------------------------------------------------


def test_symbolic_range_is_replaced_with_concrete_bounds():
    """`TMIN`/`TMAX` resolve interactively but not in batch, where they give
    `Low Range Error: Unknown identifier 'TMIN'` and no table at all.
    """
    patched, changed = cir.resolve_numeric_range(SAMPLE, "transient")
    assert changed
    assert 'Num Out Low="0"' in patched
    assert 'Num Out High="8"' in patched
    assert "TMIN" not in patched


def test_ac_range_comes_from_frange_high_first():
    """`FRange=100K,1` is high,low — not low,high."""
    patched, changed = cir.resolve_numeric_range(SAMPLE, "ac")
    assert changed
    assert 'Num Out Low="1"' in patched
    assert 'Num Out High="100K"' in patched


def test_resolve_numeric_range_is_a_no_op_without_limits():
    text = "[Transient]\nNum Out Low=\"TMIN\"\n"
    patched, changed = cir.resolve_numeric_range(text, "transient")
    assert not changed and patched == text


# --------------------------------------------------------------------------
# DC source
# --------------------------------------------------------------------------


def test_dc_swept_source_found():
    assert cir.dc_swept_source(SAMPLE) == "V1"


def test_dc_without_a_source_is_a_circuit_that_was_never_set_up():
    """Micro-Cap writes a default [Limits] block for every analysis whether the
    author used it or not, so a circuit looks DC-capable while naming nothing
    to sweep. It then refuses with "Error Source not found" — that is the
    library's incompleteness, not a driver bug.
    """
    text = SAMPLE.replace("I1=V1\n", "")
    assert cir.dc_swept_source(text) is None


def test_dc_source_set_to_none_counts_as_absent():
    text = SAMPLE.replace("I1=V1", "I1=NONE")
    assert cir.dc_swept_source(text) is None
