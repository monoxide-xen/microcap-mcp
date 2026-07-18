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

**`/IA` (analysis plot) works in batch; `/IC` (schematic drawing) does not.**
`/IA Page="Main" Output="x.jpg"` renders the analysis plot reliably. The
documented companion `/IC Page="<page>" Output=...` for the schematic drawing
produced no file here across every page name (`Main`, `Page 1`) and format
(gif/bmp/png/wmf/jpg) tried — the batch completes, but nothing lands. So the
driver requests only the analysis plot; there is no schematic-image output.

## Generating a `.CIR` from scratch

Facts that make schematic generation work (the driver ships a bounded
generator — a source and a series chain of two-terminal passives):

**Shape and component definitions are built-in.** A `.CIR` that places parts by
name (`Resistor`, `Capacitor`, `Ground`, ...) without embedding any `[shapedef]`
or `[compdef]` still opens and simulates. So a generator needs only `[Main]`,
`[Comp]`/`[Attr]` placements, `[Wire]` segments, `[Grid Text]` node labels, and
`[Limits]`.

**Pin geometry lives in `Standard.cmp`**, in grid units (×8 for pixels):

```
[compdef]
Name=Resistor
Pin="Plus",6,0,-10,-4     ; Plus at grid (6,0) = 48 px
Pin="Minus",0,0,-14,-4    ; Minus at grid (0,0)
```

Every supported two-terminal part — R, C, L, Battery, Voltage Source — shares
this layout: Minus at (0,0), Plus at (6,0), horizontal at `Rot=0`. Knowing the
real pin positions is the difference between building the intended circuit and
whatever Micro-Cap extracts from misplaced wires (a guessed vertical source
left `V(OUT)=0`).

**A node is named by a `[Grid Text]` label at its wire coordinate**, e.g.
`[Grid Text]
Text="OUT"
Px=160,128`.

**A plot expression needs `Plt`/`AliasID`/`Enable`**, or Micro-Cap reports
"Must select an expression to plot".

**A `Voltage Source` (`Definition=VSpice`) takes a `VALUE` attribute** in
Micro-Cap syntax, e.g. `DC=0 AC=1` for an AC probe or a `PULSE ...` line for
transient — not the SPICE `AC 1` spelling.

Verified by generating an RC low-pass that reproduces `1/sqrt(2)` at the cutoff,
a resistive divider at exactly 0.5, an RL high-pass, and a charging transient.

### Parallel branches and active components

**Parallel branches work with the same passive geometry.** Elements sharing a
node just need their own wire down to their own ground. A series R feeding a
parallel L-C tank resonates at `1/(2*pi*sqrt(LC))` as it should.

**Active components need three extra things — none in the manual.** An op-amp is
a macro/subcircuit component; a `.CIR` that instantiates one (unlike a passive)
needs, found by bisecting a working circuit to its minimum:

* a `[Page]` section — minimally `[Page]
Name=Page 1`;
* the model in a `[Text Area]` **tagged with the page**:
  `[Text Area]
Section=0
Page=1
Text=.MODEL O1 OPA (LEVEL=1 A=1e6 ...)`;
* **section order** — Main, Circuit, drawing, Page, Text Area, Limits, WaveForm.
  Passives tolerate any order; the op-amp does not (wrong order gives
  "Bad format in loading file"; a missing page gives "Missing model statement").

Op-amp pins from `Standard.cmp` (grid units): Plus in (0,0), Minus in (0,6),
Output (9,3); VCC (4,-1)/VEE (4,7) float for the near-ideal LEVEL=1 model.
Transistors: NPN Collector (3,-3), Base (0,0), Emitter (3,3).

With these, the generator produces inverting and non-inverting op-amp
amplifiers, verified against `-Rf/Rin` and `1 + Rf/Rg`.

**Transistors: the real trap is the grid, not rotation.** The NPN is a
*primitive* (not a macro), referencing a model by name — `2N2222` from the
global library, or a local `.MODEL QN NPN (...)`. Its pins from `Standard.cmp`
are Collector (3,-3), Base (0,0), Emitter (3,3), at `Rot=0` — no rotation is
needed (the shipped COLPITTS.cir places its NPN at `Rot=0` and its collector
wire lands exactly on Base+(24,-24), confirming the geometry).

The failure that looked like a rotation problem was actually this: **a
`[Grid Text]` node label only binds if its coordinate is a multiple of the 8 px
grid.** A label placed off-grid is silently dropped, and the analysis aborts
with `Can't find label 'OUT' in V(...)` — the same error a missing macro gives,
which is what made it look like the transistor "did not instantiate". It did:
the batch log showed the four analog nodes built and only the *plot label*
unresolved. The NPN pins sit at Base ±(24,∓24); if the placement origin's `y`
is not itself a multiple of 8, every pin lands off-grid and no label on them
binds. Put the whole device on the 8 px grid and it works — no rotation-aware
geometry, no special case beyond the passives.

With that, a common-emitter stage (divider bias, unbypassed `Re`,
AC-coupled input) reproduces the small-signal gain `-Rc/(Re+re')` to ~1%.
One more sharp edge: a part and a node must not share a name — naming the
supply source `VCC` *and* labelling its net `VCC` earns a warning and muddies
the netlist; give the label and the part different names.

**Wires join only at endpoints, not at midpoints.** A wire whose endpoint lands
partway along another wire — a T-junction — does *not* connect: Micro-Cap ties
`[Wire]` segments together only where their endpoints coincide (and to a
component pin at that coordinate). A single device never hits this because every
branch runs pin-to-pin, but a fan-out node does: the joined emitters of a
differential pair, or a supply rail feeding several taps, must be built from
segments that meet end-to-end, splitting the run at each tap. Drop a tap onto
the middle of a wire and that branch is silently open — the stage biases as if
it were not there (measured: a long-tailed pair whose tail tapped the emitter
wire at its midpoint sat with both collectors at `Vcc`, i.e. cut off; splitting
the emitter run at the tap turned it on and the collectors came to mid-supply).

A near-ideal single-transistor bias trick used above: drive a base from a
`Voltage Source` whose VALUE carries both the operating point and the signal,
`DC=6 AC=1`. The AC analysis linearises around the DC, so one source both biases
the base and injects the small signal — no coupling capacitor, no separate bias
network. (Watch the source orientation: its pins are Minus-left, Plus-right, so
a source mirrored to sit on the *right* of its node feeds the node its Minus and
inverts the bias — a base meant for `+6 V` reads `-6 V`.)
