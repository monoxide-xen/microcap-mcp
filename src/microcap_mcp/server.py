"""MCP server exposing Micro-Cap 12 to an LLM agent.

The tool descriptions here are deliberately opinionated. An agent that can
call ``simulate`` but does not know that a floating node has no DC path to
ground, or that an op-amp needs its supply rails, will produce confident
nonsense. The rules live in the contract, not just in the code.
"""

from __future__ import annotations

import base64
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import cir, corpus
from .runner import MicroCap, MicroCapError

mcp = FastMCP("microcap")

_mc: MicroCap | None = None


def _driver() -> MicroCap:
    global _mc
    if _mc is None:
        _mc = MicroCap()
    return _mc


def _solver_report(s) -> dict[str, Any]:
    """How hard Micro-Cap had to work, phrased so it can be acted on.

    The raw rejection count is close to useless on its own: measured across the
    shipped library, the "worst" circuit rejects 129,272 solutions — but it also
    accepts 443,236, so it rejected 22.6% of its attempts, in line with every
    other switching converter. Ranking by the raw count just ranks by run
    length.

    The ratio is a signature of *topology*, not of trouble. Measured:
    switching converters land at 18-23%, an astable multivibrator at 17%, and
    linear circuits at 0-5%. Backing the timestep off at every switching edge
    is the solver working correctly, not struggling. So this reports context
    and leaves the judgement to the caller rather than crying wolf on 27% of
    all runs — including 100% of the Off-Line Converters, where it is normal.
    """
    accepted = s.solutions or 0
    rejected = s.rejected or 0
    attempts = accepted + rejected
    report: dict[str, Any] = {
        "nodes": s.nodes,
        "iterations": s.iterations,
        "accepted_solutions": accepted,
        "rejected_solutions": rejected,
        "run_time_s": s.run_time,
    }
    if attempts:
        report["rejected_fraction"] = round(rejected / attempts, 3)
    if accepted:
        # Newton-Raphson passes per accepted point. 2 is an easy linear
        # circuit; 4-5 is a hard switching one. Runaway values would mean the
        # solve is genuinely fighting, though nothing in the shipped library
        # goes above ~5.4.
        report["iterations_per_solution"] = round((s.iterations or 0) / accepted, 2)
    if attempts and rejected / attempts > 0.15:
        report["note"] = (
            f"{rejected / attempts:.0%} of timesteps were rejected and retried. "
            f"That is expected for circuits with fast switching edges — converters, "
            f"multivibrators — where the solver cuts the step at each transition. "
            f"It is not by itself a reason to distrust the waveform; on a purely "
            f"linear circuit, though, it would be worth investigating."
        )
    return report


def _json_safe(x: float) -> float | None:
    """NaN marks a value Micro-Cap reported as NA. JSON has no NaN, so it
    travels as null rather than as invalid JSON or a silent zero."""
    return None if x != x else x


def _summarise(result, max_points: int) -> dict[str, Any]:
    table = result.numeric.table
    rows = table.rows
    step = max(1, len(rows) // max_points) if max_points else 1
    sampled = rows[::step]

    out: dict[str, Any] = {
        "analysis": result.numeric.analysis,
        "circuit": result.numeric.circuit,
        "columns": table.columns,
        "units": table.units,
        "points": len(rows),
        "returned_points": len(sampled),
        "data": {
            name: [_json_safe(row[i]) for row in sampled]
            for i, name in enumerate(table.columns)
        },
        "limits": result.numeric.limits,
    }
    if result.log.stats:
        out["solver"] = _solver_report(result.log.stats[0])
    if result.log.errors:
        out["messages"] = result.log.errors
    return out


@mcp.tool()
def simulate(
    netlist: str,
    analysis: str = "transient",
    defines: dict[str, str] | None = None,
    max_points: int = 200,
) -> dict[str, Any]:
    """Run a SPICE netlist through Micro-Cap and return the waveform data.

    The netlist is plain SPICE. Rules that will bite you if ignored:

    * Node ``0`` is ground and must exist. Every node needs a DC path to it,
      or the run fails to converge.
    * A ``.PRINT`` line is mandatory — it names the outputs you want. Without
      it Micro-Cap emits only an operating-point dump and no waveforms.
    * Match the analysis to the question: ``ac`` for frequency response and
      gain/phase, ``transient`` for time-domain behaviour and start-up,
      ``dc`` for bias points and transfer curves.
    * Set the time span from the circuit's own constants. A ``.TRAN`` running
      for 10 s on a circuit with microsecond edges returns a flat line.
    * Active parts need their supplies wired; an op-amp without rails does
      nothing.

    Args:
        netlist: full SPICE deck, first line is the title, ending in ``.END``.
        analysis: transient | ac | dc | harmonic_distortion |
            intermodulation_distortion | dynamic_ac | dynamic_dc | stability.
        defines: values for ``.DEFINE`` symbols in the deck, e.g. ``{"R": "1K"}``.
        max_points: cap on returned samples; the run itself is unaffected.

    Returns:
        Columns, units, sampled data, solver statistics, and any warnings.
    """
    try:
        result = _driver().simulate(netlist, analysis=analysis, defines=defines, plot_image=False)
        return _summarise(result, max_points)
    except (MicroCapError, ValueError) as e:
        # _summarise touches .table, which raises when the run produced no
        # usable data — so it must sit inside the guard, not after it.
        return {"error": str(e)}


@mcp.tool()
def sweep(
    netlist: str,
    parameter: str,
    values: list[str],
    analysis: str = "ac",
    max_points: int = 60,
) -> dict[str, Any]:
    """Run one netlist repeatedly, varying a ``.DEFINE`` parameter.

    The deck must declare the symbol, e.g. ``.DEFINE RLOAD 1K``, and use it in
    a component value. Each value is a separate Micro-Cap run.

    Args:
        netlist: SPICE deck containing a ``.DEFINE`` for ``parameter``.
        parameter: the symbol to vary.
        values: values to substitute, e.g. ``["1K", "10K", "100K"]``.
        analysis: analysis to run for every value.
        max_points: cap on returned samples per run.
    """
    if f".define {parameter.lower()}" not in netlist.lower():
        return {
            "error": f"netlist has no '.DEFINE {parameter}' line, so there is "
            f"nothing to sweep. Declare it and use it as a component value."
        }
    runs = []
    for v in values:
        try:
            r = _driver().simulate(
                netlist, analysis=analysis, defines={parameter: v}, plot_image=False
            )
            runs.append({"value": v, **_summarise(r, max_points)})
        except (MicroCapError, ValueError) as e:
            runs.append({"value": v, "error": str(e)})
    return {"parameter": parameter, "runs": runs}


@mcp.tool()
def plot(netlist: str, analysis: str = "transient", defines: dict[str, str] | None = None) -> Any:
    """Run a netlist and return Micro-Cap's own rendered plot as a JPEG image.

    Use this to *look* at a waveform. For numbers to reason about, use
    ``simulate`` — reading values off a picture is guesswork.
    """
    try:
        result = _driver().simulate(netlist, analysis=analysis, defines=defines, plot_image=True)
    except (MicroCapError, ValueError) as e:
        return {"error": str(e)}
    if "plot" not in result.images:
        return {"error": "Micro-Cap produced no plot image for this run."}
    return {
        "mime_type": "image/jpeg",
        "data": base64.b64encode(result.images["plot"]).decode("ascii"),
    }


@mcp.tool()
def simulate_example(
    name: str, analysis: str = "ac", max_points: int = 200
) -> dict[str, Any]:
    """Run one of Micro-Cap's own reference circuits and return its data.

    These are worked designs by the tool's authors, so this is the cheapest way
    to get a trustworthy baseline before modifying anything. Use
    ``describe_example`` first to see which analyses a circuit supports and
    what it plots — asking for an analysis it was not built for returns nothing
    useful.

    Numeric export is enabled automatically; the shipped circuits have it off.
    """
    try:
        e = corpus.find(name)
    except KeyError as err:
        return {"error": str(err)}
    text = e.path.read_text(encoding="cp1252", errors="replace")
    try:
        if e.is_netlist:
            result = _driver().simulate(text, analysis=analysis, plot_image=False)
        else:
            result = _driver().simulate_cir(text, analysis=analysis, plot_image=False)
        return {"circuit": e.name, "domain": e.domain, **_summarise(result, max_points)}
    except (MicroCapError, ValueError) as err:
        return {"error": str(err), "circuit": e.name, "domain": e.domain}


@mcp.tool()
def describe_example(name: str) -> dict[str, Any]:
    """Report what a reference circuit is set up to do, without running it.

    Returns the analyses it defines and the expressions it plots. Read this
    before ``simulate_example`` so you ask for an analysis that exists.
    """
    try:
        e = corpus.find(name)
    except KeyError as err:
        return {"error": str(err)}
    text = e.path.read_text(encoding="cp1252", errors="replace")
    if e.is_netlist:
        return {
            "circuit": e.name,
            "domain": e.domain,
            "format": "spice_netlist",
            "note": "plain SPICE; its .PRINT/.PLOT lines define the outputs",
        }
    return {
        "circuit": e.name,
        "domain": e.domain,
        "format": "microcap_schematic",
        "analyses": cir.analyses(text),
        "plots": [
            {"analysis": x.analysis, "x": x.x, "y": x.y} for x in cir.expressions(text)
        ],
    }


@mcp.tool()
def list_domains() -> dict[str, int]:
    """List the circuit-design domains Micro-Cap ships reference circuits for.

    Returns domain name -> circuit count. Start here before designing from
    scratch: a working reference beats an invented topology.
    """
    return corpus.domains()


@mcp.tool()
def search_examples(query: str, limit: int = 25) -> list[dict[str, str]]:
    """Search the ~470 reference circuits by name or domain."""
    return [
        {"name": e.name, "domain": e.domain, "netlist": str(e.is_netlist)}
        for e in corpus.search(query, limit=limit)
    ]


@mcp.tool()
def get_example(name: str) -> dict[str, Any]:
    """Fetch one reference circuit's source text.

    ``.CKT`` files are plain SPICE and can be fed straight to ``simulate``.
    ``.CIR`` files are Micro-Cap schematics: text, but carrying component
    coordinates and analysis settings as well as the netlist.
    """
    try:
        e = corpus.find(name)
    except KeyError as err:
        return {"error": str(err)}
    return {
        "name": e.name,
        "domain": e.domain,
        "format": "spice_netlist" if e.is_netlist else "microcap_schematic",
        "content": e.path.read_text(encoding="cp1252", errors="replace"),
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
