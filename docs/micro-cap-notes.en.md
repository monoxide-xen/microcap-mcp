# Micro-Cap 12 notes

Behaviour found by experiment while writing the driver. None of it is in the manual, and
one item the manual flatly contradicts. Micro-Cap has been abandoned since 2019 — no
support, no site, nobody to ask — so it is written down here.

Verified against Micro-Cap 12.2.0.3 (64-bit).

## Running it

**Paths must be flat names in Micro-Cap's own `DATA` folder.** A subdirectory gives
`Error No such file or directory` and produces nothing:

```
_mcp/circuit.CKT /A /NOF "_mcp/out"    → nothing is created
circuit.CKT /A /NOF "out"              → works
```

**Micro-Cap writes a `.DOC` log beside the batch file.** It holds the real error text and
solver statistics — nodes, Newton-Raphson iterations, rejected solutions, timings:

```
Circuit     Analog  Total       Rejected   Run    Setup
BATCH.CIR /A    2   1004        0          1.969  1.938
Total runs with Error/Warnings 0
```

Error lines are **prefixed with the circuit's file name**, so matching `^Error` loses
every one of them:

```
FOO.CIR Error Can't plot noise with other expressions.
```

**Log errors belong to a specific circuit.** Joining all of a batch's errors and showing
them to every circuit that produced no output lets one broken circuit infect its
neighbours with a diagnosis they never had.

**The numeric output extensions are not only the documented ones.** The manual states
Micro-Cap appends "(.TNO, .ANO, or .DNO)". That is wrong:

| Analysis | Switch | Extension |
|---|---|---|
| Transient | `/T` | `.TNO` |
| AC | `/A` | `.ANO` |
| DC | `/D` | `.DNO` |
| Harmonic Distortion | `/HD` | **`.HNO`** |
| Intermodulation Distortion | `/ID` | **`.INO`** |
| Stability | `/STABILITY` | **`.SNO`** |

**Launching is expensive.** Every start reloads the 11 MB component library ("Loading
Component Files"), and that dominates the wall clock: a run the solver finishes in 0.17 s
costs 0.8 s end to end. One batch file accepts many circuits, which amortises the start.

## Getting numbers out

**The shipped circuits export nothing by default.** Numeric output is an `OUTPUT` flag in
the `Options=` of each `[WaveForm]` block, set on roughly 36 of 475 circuits — the rest
were drawn to be looked at, not exported.

```ini
[WaveForm]
Analysis=AC
XExp=F
YExp=Mag(v(OUT))
Options=OUTPUT,LINEARY    ; ← without OUTPUT there is no table
```

**`NPts=0` in `[Limits]` exports a single row.** 22 shipped circuits are like this, and
the effect is treacherous: Micro-Cap's own 555 astable looks dead at its own settings.

```
NPts=0    →   1 row  | v(OUT) 0.482..0.482 V
NPts=200  → 200 rows | v(OUT) 0.229..9.964 V
```

**`Num Out Low="TMIN"` does not resolve in batch.** The export bounds are symbolic, in the
analysis section (`[Transient]`, `[AC]`, …). Interactively that works; in batch it is
`Low Range Error: Unknown identifier 'TMIN'` and an empty table. Concrete values from
`[Limits]` are needed.

**A DC sweep needs a source.** Micro-Cap creates a `[Limits]` block for every analysis
type whether the author configured it or not, so a circuit can look DC-capable while
naming nothing to sweep:

```ini
[Limits]
Analysis=DC
I1Range=10,0,.5     ; default boilerplate
I1=V1               ; ← without this line: Error Source not found
```

**Analysis names inside a `.CIR`** are abbreviated, no spaces: `HmDistortion`,
`ImDistortion`, `DynamicAC`, `DynamicDC`. `DynamicAC` and `DynamicDC` **define no traces
at all** — they annotate values on the schematic, so there is nothing to export.

## Reading the output

**The units row is positional, not one-per-column.** Dimensionless columns have no unit,
so the row cannot be split and zipped — columns are right-aligned and units match them by
end offset:

```
            F Mag(v(S3)/v(In)) mag(v(S3)/v(S2))
         (Hz)
    70.000000       37.383410m      673.569953m
```

A fully dimensionless table has a blank units row.

**Numbers come in three spellings:**

```
70.000000      plain
5.000E+00      scientific
37.383410m     SI suffix (f p n u m k MEG G T)
994.975MEG
```

**`NA`** means the value is undefined — phase at the first AC point, for instance. A
numbers-only rule stops the table there and loses the other 200 rows.

**Digital columns carry logic states** `X`, `Z`, `R`, `F`. In a mixed analog/digital
circuit they sit in the same table as ordinary analog columns:

```
         T    V(In)   V(Out) D(Convert) D(B0)
      0.00     7.00     8.00          1     X
```

Discard the row because of `X` and you lose `V(In)` and `V(Out)` with it.

**Beware `F`.** An AC table's first column is named `F` (frequency), which is also the
logic state for "falling". Column names need a stricter test than cell values, or the
parser stops recognising AC table heads.

**The file holds other tables too.** Operating point and model parameters are structurally
identical to waveforms and come **before** them. Tell them apart by the section title:

```
Interpolated Waveform Values     ← waveforms
DC Operating Point Voltages      ← not waveforms
Model parameters for devices ... ← not waveforms
```

**Micro-Cap writes errors into the file itself**, where the table would go:

```
Interpolated Waveform Values
============================
Low Range Error: Unknown identifier 'TMIN'.
```

## The window

**There is no headless mode.** In batch Micro-Cap still opens its window and plots as it
goes — the manual says so outright.

**`STARTUPINFO.wShowWindow = SW_HIDE` does not work.** It is only a hint for the first
`ShowWindow` call, and Micro-Cap shows its windows explicitly. Measured: the window was
visible in 9 samples out of 10.

**Suppression and image export are mutually exclusive.** Micro-Cap renders the plot
*through* the window:

| Mode | Window on screen | Images |
|---|---|---|
| no suppression | 97% of the time | fine |
| `ShowWindow(SW_HIDE)` | 10% | **black JPEG** |
| moved off-screen | 47% | **none at all** |

A black JPEG is a valid file — a format check passes it. Only the size gives it away:
53 KB against 315 KB for a real plot.

So: only suppress the window when no image was requested.
