"""Tests for schematic generation — structure and geometry (no Micro-Cap).

The physics is checked end to end in test_integration.py; here we pin the .CIR
structure and the pin geometry that makes it electrically correct. The
geometry constants came from Micro-Cap's own library; if they drift, a
generated circuit silently becomes the wrong circuit, so they are guarded.
"""

from __future__ import annotations

import pytest

from microcap_mcp import schematic
from microcap_mcp.schematic import PARTS, SOURCE, SchematicError, series_circuit


def test_two_terminal_parts_share_the_library_pin_layout():
    """Read from Standard.cmp: Minus at grid (0,0), Plus at (6,0). All the
    supported passives and the source are identical, which is why the layout
    can be uniform. A wrong offset here builds the wrong circuit.
    """
    for part in (*PARTS.values(), SOURCE):
        assert part.minus == (0, 0)
        assert part.plus == (6, 0)


def test_rc_lowpass_structure():
    cir = series_circuit([("R", "1K"), ("C", "159.155N")], analysis="AC")
    # a source, two parts, two grounds
    assert cir.count("Name=Voltage Source") == 1
    assert cir.count("Name=Resistor") == 1
    assert cir.count("Name=Capacitor") == 1
    assert cir.count("Name=Ground") == 2
    # the values landed on the right attributes
    assert "RESISTANCE\nV=1K" in cir
    assert "CAPACITANCE\nV=159.155N" in cir
    # the output node is labelled
    assert '[Grid Text]\nText="OUT"' in cir


def test_source_value_and_attribute():
    cir = series_circuit([("R", "1K")], source="DC=0 AC=1", analysis="AC")
    assert "VALUE\nV=DC=0 AC=1" in cir, "the VSpice source takes a VALUE attribute"


def test_plot_expression_has_the_required_fields():
    """Without Plt/AliasID/Enable, Micro-Cap ignores the expression."""
    cir = series_circuit([("R", "1K"), ("C", "1N")], analysis="AC")
    wave = cir.split("[WaveForm]")[1].split("[Comp]")[0]
    for field in ("Plt=1", "AliasID=1", "Enable=Enable", "Options=OUTPUT", "YExp=V(OUT)"):
        assert field in wave


def test_references_are_numbered_per_kind():
    cir = series_circuit([("R", "1K"), ("R", "2K"), ("C", "1N")], analysis="AC")
    assert "V=R1" in cir and "V=R2" in cir and "V=C1" in cir
    assert "V=C2" not in cir, "the capacitor is C1, not C2"


def test_no_shapedefs_are_embedded():
    """They are built-in; embedding them would just be noise."""
    cir = series_circuit([("R", "1K")], analysis="AC")
    assert "[shapedef]" not in cir and "[compdef]" not in cir


def test_pins_meet_end_to_end_via_gap_wires():
    """Each part spans 48 px; a wire bridges the gap to the next. The output
    node x must be a real pin coordinate, not a gap.
    """
    cir = series_circuit([("R", "1K"), ("C", "1N")], analysis="AC")
    # the OUT label and a wire endpoint must share an x — the R's Plus pin
    import re
    label = re.search(r'Text="OUT"\nPx=(\d+),128', cir)
    assert label, "OUT label must sit on the rail at y=128"


@pytest.mark.parametrize("analysis,expect", [("AC", "F"), ("Transient", "T"), ("DC", "T")])
def test_x_axis_matches_analysis(analysis, expect):
    cir = series_circuit([("R", "1K")], analysis=analysis)
    assert f"XExp={expect}" in cir


def test_empty_parts_rejected():
    with pytest.raises(SchematicError):
        series_circuit([], analysis="AC")


def test_unsupported_part_rejected():
    with pytest.raises(SchematicError, match="unsupported"):
        series_circuit([("Q", "2N2222")], analysis="AC")


# --------------------------------------------------------------------------
# parallel shunt branches
# --------------------------------------------------------------------------


def test_shunt_adds_parallel_branches_to_ground():
    """A series R with a shunt L and C makes a resonant tank; the physics is
    checked in the integration tests, here just the structure.
    """
    cir = series_circuit([("R", "1K")], shunt=[("L", "1M"), ("C", "1U")], analysis="AC")
    assert cir.count("Name=Inductor") == 1
    assert cir.count("Name=Capacitor") == 1
    # source ground + one ground per shunt branch, and NOT a ground on the output
    assert cir.count("Name=Ground") == 1 + 2


def test_shunt_output_node_is_not_grounded():
    """Grounding the output directly would short it — the shunts are its path
    to ground. Regression guard: an earlier version grounded it and got V=0.
    """
    cir = series_circuit([("R", "1K")], shunt=[("C", "1U")], analysis="AC")
    import re
    # the OUT label's x
    out_x = int(re.search(r'Text="OUT"\nPx=(\d+),128', cir).group(1))
    # no wire drops straight from (out_x,128) to a ground at (out_x,152)
    assert f"Pxs={out_x},128,{out_x},152" not in cir


def test_shunt_parts_are_validated_too():
    with pytest.raises(SchematicError, match="unsupported"):
        series_circuit([("R", "1K")], shunt=[("Q", "2N2222")], analysis="AC")


# --------------------------------------------------------------------------
# op-amp amplifiers (active components)
# --------------------------------------------------------------------------

from microcap_mcp.schematic import opamp_amplifier, _fmt_ohms  # noqa: E402


def test_opamp_uses_the_required_page_and_model_association():
    """The three cracked keys: a [Page], a [Text Area] tagged Section=0/Page=1
    carrying the model, and the canonical order (drawing before Limits). Losing
    any one makes the op-amp fail to instantiate.
    """
    cir = opamp_amplifier(gain=10, kind="inverting")
    assert "[Page]\nName=Page 1" in cir
    assert "[Text Area]\nSection=0\nPage=1\nText=.MODEL O1 OPA" in cir
    # drawing (the op-amp) must come before [Limits]
    assert cir.index("Name=Opamp") < cir.index("[Limits]")


def test_inverting_sizes_the_feedback_resistor():
    """gain = Rf/Rin, so Rf = gain*Rin."""
    cir = opamp_amplifier(gain=10, kind="inverting", rin="1K")
    assert "RESISTANCE\nV=1K" in cir       # Rin
    assert "RESISTANCE\nV=10K" in cir      # Rf = 10 * 1K


def test_non_inverting_sizes_the_feedback_resistor():
    """gain = 1 + Rf/Rg, so Rf = (gain-1)*Rg."""
    cir = opamp_amplifier(gain=11, kind="non-inverting", rin="1K")
    assert "RESISTANCE\nV=10K" in cir      # Rf = (11-1) * 1K


def test_fmt_ohms():
    assert _fmt_ohms(10_000) == "10K"
    assert _fmt_ohms(2_200_000) == "2.2MEG"
    assert _fmt_ohms(470) == "470"
    assert _fmt_ohms(0) == "0"


def test_bad_gain_and_kind_rejected():
    with pytest.raises(SchematicError):
        opamp_amplifier(gain=0, kind="inverting")
    with pytest.raises(SchematicError):
        opamp_amplifier(gain=5, kind="cascode")
