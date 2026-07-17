"""Domain knowledge for driving Micro-Cap competently.

The tools let an agent *run* a simulation; this is what keeps it from running
the wrong one or trusting a result it should not. It is deliberately specific
to Micro-Cap and to this server — an LLM already knows what a bandpass filter
is; what it does not know is that Micro-Cap will happily hand back a flat line
for a working oscillator, or that a 22%-rejection converter is healthy.
"""

from __future__ import annotations

# One line per shipped domain, so an agent can reach for a worked reference
# before inventing a topology. Keys must match corpus.domains() exactly; the
# purposes are hand-written. A few "domains" are analysis *techniques* rather
# than circuit types — marked so the agent does not mistake them for topologies.
DOMAIN_PURPOSE: dict[str, str] = {
    "(root)": "Uncategorised examples — the largest bucket; search by name.",
    "Audio Amplifiers": "Audio power and preamp stages; THD and frequency response.",
    "Chaos": "Chaotic oscillators and nonlinear-dynamics demos.",
    "CoPEC SMPS": "Switch-mode supplies from the CoPEC teaching set.",
    "Complex AC Power": "Real/reactive power, power factor, complex AC quantities.",
    "Constant Power": "Constant-power load and source behaviour.",
    "Curve Fit": "technique: fitting model curves to data, not a circuit type.",
    "DSP": "Sampled-data and digital-signal-processing building blocks.",
    "Data Communications": "Line drivers, receivers, links, signalling.",
    "Feedback Loops": "Control loops, loop gain, compensation.",
    "Ferrites": "Ferrite-core and nonlinear-magnetic component models.",
    "Filters": "Passive and active filters — low/high/band-pass, notch.",
    "Fourier": "technique: harmonic/spectral content of a waveform.",
    "Measure Commands": "technique: automated measurements on a run.",
    "MemElements": "Memristors, memcapacitors, meminductors.",
    "Misc": "Assorted examples; search by name.",
    "Mixers": "RF mixers and frequency conversion.",
    "Motors": "Electric-motor models and drives.",
    "Nonlinear Magnetics": "Saturating cores, hysteresis, transformers.",
    "OPAMPS": "Op-amp circuits — gain stages, integrators, comparators.",
    "Off-Line Converters": "Mains-input switch-mode power supplies.",
    "Optimizer": "technique: parameter optimisation, not a circuit type.",
    "Oscillators": "Sinusoidal and relaxation oscillators.",
    "PSS": "technique: periodic steady-state analysis of switching circuits.",
    "PWM Controller": "Pulse-width-modulation controller circuits.",
    "PWM Switch": "Averaged PWM-switch models for SMPS analysis.",
    "Phase Locked Loops": "PLLs — phase detectors, VCOs, loop filters.",
    "Power Conversion": "DC-DC and AC-DC conversion topologies.",
    "Power Lines": "Transmission-line and power-line models.",
    "RF Amplifiers": "Radio-frequency gain stages; often S-parameters.",
    "Region Enable": "technique: enabling/disabling regions of a schematic.",
    "Regulators": "Linear and switching voltage regulators.",
    "S Parameters": "Scattering-parameter analysis; complex/Smith output.",
    "Small Signal SMPS": "Small-signal (AC) models of switching supplies.",
    "Smoke": "technique: stress/operating-limit (Smoke) analysis.",
    "Smoothing": "technique: source smoothing for convergence.",
    "Spreadsheet": "Schematic spreadsheets driven by simulation variables.",
    "Stability Analysis": "technique: loop stability, gain/phase margin.",
    "Switched Models": "Ideal-switch and switched-capacitor models.",
    "Switching Regulators": "Buck/boost/flyback regulator circuits.",
    "Vacuum Tubes": "Valve amplifier stages and tube models.",
    "Voltage Controlled Oscillators": "VCOs and tuning behaviour.",
    "Worst Case Analysis": "technique: worst-case tolerance analysis.",
}


GUIDE = """\
# Driving Micro-Cap through this server

## Start from a worked circuit, not a blank page

Micro-Cap ships ~490 circuits across 43 domains, all by the tool's authors.
Before writing a netlist from memory, look for one:

1. `list_domains` — the domains and their sizes.
2. `search_examples("bandpass")` — find candidates by name or domain.
3. `describe_example(name)` — what analyses it supports and what it plots,
   *without* running it.
4. `simulate_example(name, analysis=...)` — run it and read the numbers.
5. `get_example(name)` — fetch the source to adapt.

A reference gives you a topology that already converges and a set of node
names that exist. Adapting one beats inventing from scratch.

## Pick the analysis that answers the question

| Question | Analysis |
| --- | --- |
| Gain vs frequency, bandwidth, roll-off | `ac` |
| Start-up, ringing, switching, time-domain shape | `transient` |
| Bias point, DC transfer curve | `dc` |
| Total harmonic distortion | `harmonic_distortion` |
| Intermodulation | `intermodulation_distortion` |
| Loop gain, phase/gain margin | `stability` |

Running the wrong one wastes a launch and, worse, returns a plausible-looking
answer to a question you did not ask.

## Do not be fooled by a flat or empty result

Micro-Cap can hand back a technically-correct result that means nothing:

- A `transient` span far longer or shorter than the circuit's time constants
  shows a flat line. Match the run time to the circuit — microsecond edges
  need a microsecond span.
- An op-amp with no supply rails, or a node with no DC path to ground, does
  nothing or fails to converge. Node `0` is ground and must exist.
- If `points` returns just one row, the circuit's own settings suppressed the
  output; ask for more points.

## Judge whether to trust the waveform

`simulate` returns a `solver` block. Read it:

- `rejected_fraction` is a *topology signature*, not an alarm. Switching
  converters sit at 18-23%, an astable at ~17%, linear circuits at 0-5% —
  the solver cuts the timestep at each switching edge, which is correct. A
  high fraction on a *linear* circuit, though, is worth investigating.
- `iterations_per_solution` around 2 is an easy solve; 4-5 is a hard switching
  one. Runaway values mean the solve is genuinely fighting.
- If a run does not converge, the usual moves are tightening `.RELTOL`, adding
  a `.NODESET`, or finding the floating node.

## Reading the data

- Columns come back with `units`. Dimensionless columns (ratios, gains) have
  an empty unit — that is normal, not missing data.
- `null` in a column is Micro-Cap's `NA`: an undefined value, e.g. phase at
  the first AC point.
- AC and S-parameter values are complex, returned as `{"re", "im"}`. Magnitude
  is `hypot(re, im)`; phase is `atan2(im, re)`.
- Digital columns carry logic states; their cells come back as `null` because
  a logic level is not a measurement.

## SPICE netlist essentials

A netlist passed to `simulate` must:

- name ground as node `0`, with every node reachable from it by DC;
- carry a `.PRINT` line naming the outputs you want (without it there is no
  numeric data);
- give active parts their supply connections;
- end with `.END`.
"""


def domain_map() -> str:
    """Markdown table of domains, their sizes, and what each is for.

    Built from the live corpus so the counts are always right; unknown domains
    (a differently-patched install) fall back to a neutral description.
    """
    from . import corpus

    rows = ["| Domain | Circuits | For |", "| --- | --- | --- |"]
    for name, count in sorted(corpus.domains().items()):
        purpose = DOMAIN_PURPOSE.get(name, "Examples in this domain; search by name.")
        rows.append(f"| {name} | {count} | {purpose} |")
    return "# Micro-Cap reference domains\n\n" + "\n".join(rows) + "\n"
