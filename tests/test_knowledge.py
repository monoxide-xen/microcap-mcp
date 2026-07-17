"""Tests for the knowledge layer — the domain map and the guide.

These are content-integrity checks: the value of a knowledge resource is that
it stays true, so the tests guard against it drifting out of sync or losing the
specific facts that make it worth more than generic prose.
"""

from __future__ import annotations

from microcap_mcp import knowledge


def test_every_purpose_key_names_a_plausible_domain():
    """No stray keys. (Completeness against the live corpus is checked by the
    integration test that has Micro-Cap; here we just guard the static table.)
    """
    assert knowledge.DOMAIN_PURPOSE, "the map must not be empty"
    for name, purpose in knowledge.DOMAIN_PURPOSE.items():
        assert name and purpose, f"empty entry for {name!r}"


def test_techniques_are_marked_as_such():
    """Some 'domains' are analysis techniques, not circuit types. An agent that
    treats 'Optimizer' as a topology to copy is misled, so they are flagged.
    """
    for tech in ("Optimizer", "Worst Case Analysis", "Curve Fit", "Smoke"):
        assert knowledge.DOMAIN_PURPOSE[tech].startswith("technique:")


def test_guide_carries_the_hard_won_specifics():
    """The guide earns its place by stating what an LLM does not already know.
    If these facts fall out, it has decayed into generic filler.
    """
    g = knowledge.GUIDE
    assert "rejected_fraction" in g, "the trust signal must be explained"
    assert "flat line" in g, "the silent-failure trap must be named"
    assert '{"re", "im"}' in g, "complex output format must be documented"
    assert "0" in g and "ground" in g, "node-0-is-ground must be stated"
    assert ".PRINT" in g, "the mandatory .PRINT rule must be there"


def test_guide_points_at_the_tools_by_name():
    """It is a guide to *this server*, so it must reference the actual tools."""
    g = knowledge.GUIDE
    for tool in ("search_examples", "describe_example", "simulate_example", "get_example"):
        assert tool in g


def test_analysis_selection_table_covers_every_analysis():
    g = knowledge.GUIDE
    for analysis in ("ac", "transient", "dc", "harmonic_distortion",
                     "intermodulation_distortion", "stability"):
        assert analysis in g, f"{analysis} missing from the selection guidance"
