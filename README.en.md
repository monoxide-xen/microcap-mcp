<div align="center">

# microcap-mcp

**An MCP server for the Micro-Cap 12 SPICE simulator**

Lets an LLM agent simulate analog circuits: run analyses, sweep parameters,
get waveform data and plots.

[![tests](https://github.com/monoxide-xen/microcap-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/monoxide-xen/microcap-mcp/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Windows](https://img.shields.io/badge/platform-Windows-0078D6.svg?logo=windows&logoColor=white)](#install)
[![MCP](https://img.shields.io/badge/MCP-server-8A2BE2.svg)](https://modelcontextprotocol.io/)

[Русский](README.md) · **English**

</div>

---

Works through Micro-Cap's own batch mode (`MC12 @batch.bat`) — no GUI automation, no
modification of the program. Runs are headless and the process exits by itself.

## Requirements

- Windows
- Python 3.11+
- An installed copy of [Micro-Cap 12](https://spectrum-soft.com/) (freeware)

## Install

```bash
git clone https://github.com/monoxide-xen/microcap-mcp
cd microcap-mcp
uv sync
```

Register it with your MCP client:

```json
{
  "mcpServers": {
    "microcap": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/microcap-mcp", "run", "microcap-mcp"]
    }
  }
}
```

### Where Micro-Cap lives

The driver scans the usual places (`MC12`, `Micro-Cap 12` at drive roots and under
`Program Files`) and accepts either `mc12_64.exe` or `mc12.exe`. But Micro-Cap is not
installed under `Program Files` — it writes to its own folder and needs it writable — so
in practice it ends up anywhere.

If the scan misses it, point `MICROCAP_HOME` at the folder containing the executable; it
is used verbatim:

```jsonc
{
  "mcpServers": {
    "microcap": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/microcap-mcp", "run", "microcap-mcp"],
      "env": { "MICROCAP_HOME": "E:/Tools/MC12" }   // ← your path
    }
  }
}
```

Or as an environment variable — PowerShell:

```powershell
$env:MICROCAP_HOME = "E:\Tools\MC12"
```

## Tools

| Tool | Does |
|---|---|
| `simulate` | run a SPICE netlist, return waveform data and solver stats |
| `sweep` | run a circuit across values of a `.DEFINE` parameter |
| `plot` | return Micro-Cap's rendered plot as a JPEG |
| `simulate_schematic` | run an arbitrary `.CIR` schematic — edit a reference and run the copy |
| `generate_schematic` | draw a `.CIR` from scratch: a source + R/C/L in series and parallel (pin geometry from MC's library) |
| `generate_amplifier` | draw a `.CIR` op-amp amplifier (inverting / non-inverting) sized to a target gain |
| `generate_transistor_amplifier` | draw a `.CIR` common-emitter BJT stage; the bias divider is auto-sized to a mid-supply collector |
| `generate_emitter_follower` | draw a `.CIR` emitter follower (buffer: gain ≈ 1, low output impedance) |
| `generate_mosfet_amplifier` | draw a `.CIR` common-source MOSFET stage; gate bias derived from the model's VTO/KP to hold saturation |
| `generate_differential_pair` | draw a `.CIR` BJT long-tailed pair (antiphase OUTP/OUTN outputs, auto-biased to mid-supply) |
| `generate_current_mirror` | draw a `.CIR` BJT current mirror (Iout ≈ Iref = (Vcc−Vbe)/Rref) |
| `simulate_example` | run one of the ~490 circuits shipped with Micro-Cap |
| `describe_example` | which analyses a circuit supports and what it plots, without running it |
| `list_domains` | the 43 reference domains and their sizes |
| `search_examples` | search the reference circuits |
| `get_example` | fetch a reference circuit's source |

Complex output (AC, S-parameters, Smith charts) is returned as `{"re", "im"}`.

Supported analyses: `transient`, `ac`, `dc`, `harmonic_distortion`,
`intermodulation_distortion`, `stability`.

Beyond the tools the server exposes **resources** — `microcap://guide` (how not
to let Micro-Cap silently mislead you: choosing an analysis, trusting the
solver, reading complex data, SPICE rules) and `microcap://domains` (a map of
the 43 reference-circuit domains) — and an `analyse_circuit` **prompt** for the
common workflow.

## Example

The agent writes a netlist and gets numbers back:

```
RC Lowpass
V1 IN 0 AC 1
R1 IN OUT 1K
C1 OUT 0 159.155N
.AC DEC 21 10 100K
.PRINT AC V(OUT)
.END
```

```jsonc
// simulate(netlist, analysis="ac")
{
  "columns": ["F", "V(OUT)"],
  "units":   ["Hz", "V"],
  "points":  85,
  "data": {
    "F":      [10.0, 100.0, 1000.0, 10000.0],
    "V(OUT)": [1.0,  0.995, 0.70710, 0.09950]
  },
  "solver": { "nodes": 2, "iterations": 88, "rejected_solutions": 0,
              "rejected_fraction": 0.0, "iterations_per_solution": 2.0 }
}
```

The RC cutoff `1/(2πRC)` is 1000 Hz, where the gain should be `1/√2 ≈ 0.70711`.

`solver.rejected_fraction` is the share of timesteps the solver retried. It is a signature
of topology, not an alarm: switching converters sit at 18–23%, linear circuits at 0–5% —
the solver cuts the step at each switching edge, which is normal. Past 15%, a note is
added to the response.

## Worked example: characterise a filter

The loop `microcap://guide` prescribes — find a reference, don't invent one:

```python
search_examples("bandpass")          # → BPFILT (Filters domain); the search is
                                     #   semantic even though the name is cryptic
describe_example("BPFILT")           # → supports AC; plots Mag(v(S3)/v(In))
r = simulate_example("BPFILT", analysis="ac")
```

From the data: peak gain **37.5 at 627 Hz**, −3 dB band **453…1115 Hz** (Q ≈ 0.95).
Then `get_example` + `simulate_schematic` to edit a value and run the copy.

## Tests

```bash
uv run pytest
```

123 unit tests with no Micro-Cap (parser, `.CIR` handling, log reader — pure text) plus 25
integration tests that drive the whole stack against physics with a known answer and need
Micro-Cap installed:

```bash
MICROCAP_HOME=C:/MC12 uv run pytest tests/test_integration.py
```

Without Micro-Cap the integration tests skip, so CI stays green.

## Corpus evaluation

`eval/harness.py` runs every circuit Micro-Cap ships and buckets the failures by cause:

```bash
uv run python eval/harness.py --all --window   # full sweep with a progress window
uv run python eval/harness.py --domain Filters # a single domain
```

Current result: 762 of 866 runs the circuit is able to answer (88%). Most remaining
failures are not on the driver's side — circuits with no ground, broken node references,
unconfigured DC blocks.

`--compare <previous.jsonl>` diffs the result per circuit: the headline pass rate hides
trades where one change fixes some circuits and breaks others.

## Documentation

- [Micro-Cap notes](docs/micro-cap-notes.en.md) — behaviour that is not in the manual,
  and in places contradicts it. Useful to anyone automating MC12.

## Licence

[MIT](LICENSE), for the code in this repository.

Micro-Cap 12 belongs to Spectrum Software. It is not included, redistributed or
modified: this project uses its documented command-line interface.
