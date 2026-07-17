<div align="center">

# microcap-mcp

**An MCP server for the Micro-Cap 12 circuit simulator**

Lets an LLM agent design and simulate analog circuits: write netlists, run
analyses headless, and read the numbers back.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows-0078D6.svg?logo=windows&logoColor=white)](#install)
[![MCP](https://img.shields.io/badge/MCP-server-8A2BE2.svg)](https://modelcontextprotocol.io/)
[![Corpus: 84%](https://img.shields.io/badge/Corpus-84%25%20answerable-success.svg)](#how-well-does-it-work)

[Русский](README.md) · **English**

</div>

---

Micro-Cap 12 is the SPICE simulator Spectrum Software released as freeware in
2019 when the company closed. It is abandoned: no support, no updates, no site.

Ask an agent for a filter's frequency response and it writes the netlist, runs
it headless, and reads the numbers back:

```
    freq        |V(OUT)|    theory 1/sqrt(1+(f/1k)^2)
   1000.0 Hz     0.70710    0.70711
```

---

## Why this is not a reverse-engineering project

The obvious plan — reverse the binary, inject an automation layer — turned out
to be unnecessary. Micro-Cap ships a **documented batch mode**:

```
MC12 @BATCH.BAT
```

with per-line syntax:

```
cname [/DEF "x val"] [/NOF "fn"] [analysis] [image commands]
```

That covers every analysis, parametric runs, numeric export and image export. A
three-point sweep runs headless in ~4 seconds and the process exits on its own.
No GUI automation, no injection, no patched binary.

## What is actually hard

Automation was solved by the vendor in 2019. **Competence was not.**

An agent that can call `simulate` but does not know that node 0 is ground, that
every node needs a DC path to it, or that a `.TRAN` span must follow the
circuit's own time constants will produce confident nonsense. Worse, some
failures are silent: run Micro-Cap's own 555 astable with its shipped settings
and you get *one* data point — the oscillator looks dead.

```
points=None  ->   1 row  | v(OUT) 0.482..0.482 V   ← "it doesn't oscillate"
points=200   -> 200 rows | v(OUT) 0.229..9.964 V   ← full rail-to-rail swing
```

So the server is built in three layers:

| Layer | Mechanism | Purpose |
| --- | --- | --- |
| Capability | MCP **tools** | run analyses, sweep, export |
| Knowledge | reference corpus + rules in the tool contracts | ~490 worked circuits across 44 domains |
| Judgment | validation + solver diagnostics | refuse malformed runs, surface solver distress |

## Install

Needs Python 3.11+, Windows, and your own Micro-Cap 12 installation.

```bash
git clone https://github.com/monoxide-xen/microcap-mcp
cd microcap-mcp
uv sync
```

The driver auto-detects common install paths; otherwise:

```bash
export MICROCAP_HOME="D:/Games/MC12"
```

Register it with your MCP client:

```json
{
  "mcpServers": {
    "microcap": {
      "command": "uv",
      "args": ["--directory", "/path/to/microcap-mcp", "run", "microcap-mcp"]
    }
  }
}
```

## Tools

| Tool | Does |
| --- | --- |
| `simulate` | run a SPICE netlist, return waveform data + solver stats |
| `sweep` | run one deck repeatedly over a `.DEFINE` parameter |
| `plot` | return Micro-Cap's rendered plot as a JPEG |
| `simulate_example` | run one of the ~490 circuits shipped with Micro-Cap |
| `describe_example` | what a reference circuit is set up to do, without running it |
| `list_domains` | the 44 reference domains and their circuit counts |
| `search_examples` | search the reference circuits |
| `get_example` | fetch a reference circuit's source |

## How well does it work

`eval/harness.py` runs every circuit Micro-Cap ships through the driver and
buckets each failure by cause — because "79% works" is useless without knowing
whether the rest is our bug, a missing feature, or a circuit that could never
answer the question.

```
RUNS 925   OK 728 (79% of all)   FAILED 197   978s
  59 runs were unanswerable by construction (digital-only / no setup)
  728/866 = 84% of runs the circuit could answer
```

Of the remaining failures, most are not the driver's: broken node references
inside subcircuits, circuits with no ground, plot traces that are the literal
constant `1`, Micro-Cap's own refusal to plot noise beside other expressions,
and 42 circuits whose DC block was never configured with a source to sweep.
Fixing every remaining bug of ours would land near **87%**. Getting to 99% would
mean counting other people's broken circuits as our successes.

```bash
uv run python eval/harness.py --all --window   # full sweep, progress window
uv run python eval/harness.py --domain Filters # one domain
```

## Things Micro-Cap does that the manual does not mention

Each of these cost real debugging, and none is in the documentation — one is
flatly contradicted by it.

<details open>
<summary><b>Driving it</b></summary>

<br>

* Batch paths must be flat names in Micro-Cap's own `DATA` folder. A
  subdirectory fails with `No such file or directory` and produces nothing.
* Micro-Cap writes a `.DOC` log beside the batch file with the real error text
  and per-run solver statistics — nodes, Newton-Raphson iterations, rejected
  solutions. Error lines are prefixed with the circuit file name, so matching
  `^Error` silently loses every one of them.
* **The documented extension list is wrong.** The manual says numeric output is
  "(.TNO, .ANO, or .DNO)". Distortion and stability analyses write `.HNO`,
  `.INO` and `.SNO`.

</details>

<details>
<summary><b>Getting numbers out</b></summary>

<br>

* The shipped circuits export nothing by default: numeric output is a per-trace
  `OUTPUT` flag in a `[WaveForm]` block, set on ~36 of 475 circuits.
* `NPts=0` in `[Limits]` exports a single row. 22 shipped circuits are like
  that — they were drawn to be looked at, not exported.
* `Num Out Low="TMIN"` resolves interactively but not in batch, where it fails
  with `Low Range Error: Unknown identifier 'TMIN'` and writes no table.
* Analysis names inside a `.CIR` are `HmDistortion`, `ImDistortion`,
  `DynamicAC`, `DynamicDC` — abbreviated, no spaces. `DynamicAC`/`DynamicDC`
  define no traces at all; they annotate the schematic in place.

</details>

<details>
<summary><b>Reading the output</b></summary>

<br>

* The units row is positional, not one-per-column — dimensionless columns have
  no unit, and a fully dimensionless table has a blank units row.
* Numbers come in three spellings: `70.000000`, `5.000E+00`, `37.383410m`.
* `NA` appears where a value is undefined (phase at the first AC point). Digital
  columns carry logic states `X`, `Z`, `R`, `F`. Treating a row as numbers-only
  discards the analog columns sitting beside them.
* Watch out: an AC table's first column is named `F`, which is also the logic
  state for "falling". Column names need a stricter test than cell values.

</details>

<details>
<summary><b>The window</b></summary>

<br>

Micro-Cap has no headless mode; in batch it still opens its window and plots as
it goes. `STARTUPINFO.wShowWindow = SW_HIDE` does not work — MC shows its
windows explicitly. A watcher thread hiding them cuts on-screen time from 97% to
10%, but Micro-Cap renders plots *through* the window, so a suppressed one
exports a **black JPEG**. Suppression and image export are mutually exclusive;
the driver therefore goes quiet only when no image was requested.

</details>

## Licence

[MIT](LICENSE), for the code here. Micro-Cap 12 is Spectrum Software's; it is not
included, redistributed or modified. This project drives its documented
command-line interface and nothing more.
