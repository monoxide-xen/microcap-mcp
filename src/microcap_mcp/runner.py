"""Headless driver for Micro-Cap 12.

MC12 has a documented batch mode (help topic "Command Lines and Batch Files"):

    MC12 @BATCH.BAT

where each line of the batch file is

    cname [/DEF "x val"] [/NOF "fn"] [analysis] [image commands]

No GUI automation is involved: the process runs the batch and exits.

Two behaviours were established by experiment against MC 12.2.0.3 and are
load-bearing here:

* Paths must be flat names resolved against MC's own DATA folder. A
  subdirectory makes MC fail with "No such file or directory" and produce
  nothing, so everything is written straight into DATA under an ``mcp_``
  prefix and cleaned up afterwards.
* MC writes a run log next to the batch file, named after it with a .DOC
  extension. It carries the real error text and per-run solver statistics,
  which is the only honest way to report *why* a run failed.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .parser import NumericOutput, parse_file

# analysis name -> (batch switch, extension MC appends to the /NOF name).
#
# The manual states the extension is "(.TNO, .ANO, or .DNO)" — that is wrong,
# or at least incomplete. Distortion and stability analyses write .HNO, .INO
# and .SNO. Every extension below was established by running a circuit and
# looking at what appeared, not by reading the documentation: trusting it here
# cost 63 runs failing at exactly 0%.
ANALYSES: dict[str, tuple[str, str]] = {
    "transient": ("/T", "TNO"),
    "ac": ("/A", "ANO"),
    "dc": ("/D", "DNO"),
    "harmonic_distortion": ("/HD", "HNO"),
    "intermodulation_distortion": ("/ID", "INO"),
    "dynamic_ac": ("/DYNAMIC_AC", "ANO"),
    "dynamic_dc": ("/DYNAMIC_DC", "DNO"),
    "stability": ("/STABILITY", "SNO"),
}

PREFIX = "mcp_"


class MicroCapError(RuntimeError):
    """A run failed. Carries MC's own diagnostics where available."""


# Micro-Cap's own ReadMe tells you not to install it under Program Files —
# it writes to its own folder and needs the directory writable — so in practice
# it ends up anywhere at all. These are guesses for convenience only; anyone
# whose install is elsewhere sets MICROCAP_HOME, which always wins.
_FOLDER_NAMES = ("MC12", "Micro-Cap 12", "MicroCap12", "Micro-Cap")

# 64-bit first, but a 32-bit-only install is perfectly normal.
EXE_NAMES = ("mc12_64.exe", "mc12.exe")


def _executable_in(folder: Path) -> Path | None:
    for name in EXE_NAMES:
        exe = folder / name
        if exe.is_file():
            return exe
    return None


def _likely_roots() -> "list[Path]":
    """Plausible parent directories, across whichever drives exist."""
    roots: list[Path] = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        drive = Path(f"{letter}:\\")
        if not drive.exists():
            continue
        roots.append(drive)
        for sub in ("Program Files", "Program Files (x86)", "Programs", "Apps"):
            p = drive / sub
            if p.is_dir():
                roots.append(p)
                spectrum = p / "Spectrum Software"
                if spectrum.is_dir():
                    roots.append(spectrum)
    return roots


def find_install(explicit: str | os.PathLike | None = None) -> Path:
    """Locate the Micro-Cap 12 installation directory.

    Order: the explicit argument, then ``MICROCAP_HOME``, then a scan of common
    folder names on every drive. Micro-Cap can live anywhere, so the scan is a
    convenience, not a contract — set ``MICROCAP_HOME`` and it is used verbatim.
    """
    for candidate in (explicit, os.environ.get("MICROCAP_HOME")):
        if not candidate:
            continue
        folder = Path(candidate)
        if _executable_in(folder):
            return folder
        raise MicroCapError(
            f"No Micro-Cap executable ({' or '.join(EXE_NAMES)}) in {folder}. "
            f"MICROCAP_HOME must point at the folder that contains it."
        )

    for root in _likely_roots():
        for name in _FOLDER_NAMES:
            folder = root / name
            if folder.is_dir() and _executable_in(folder):
                return folder

    raise MicroCapError(
        "Micro-Cap 12 not found. Set MICROCAP_HOME to the folder holding "
        f"{EXE_NAMES[0]} (or {EXE_NAMES[1]}), e.g. C:\\MC12."
    )


@dataclass
class RunStats:
    """One row of MC's batch log: solver effort for a single run."""

    circuit: str
    analysis: str
    nodes: int | None = None
    iterations: int | None = None
    rejected: int | None = None
    solutions: int | None = None
    run_time: float | None = None


@dataclass
class BatchLog:
    """Parsed .DOC log that MC writes beside the batch file."""

    errors: list[str] = field(default_factory=list)
    stats: list[RunStats] = field(default_factory=list)
    circuits: int = 0
    simulations: int = 0
    runs_with_errors: int = 0
    raw: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors and self.simulations > 0 and self.runs_with_errors == 0

    def why(self, stem: str = "") -> str:
        """Micro-Cap's own explanation for one circuit's failure.

        A batch log holds every circuit's errors, each tagged with the file it
        came from. Returning all of them attributes one circuit's complaint to
        all 24 of its neighbours — which sends you hunting a cause that circuit
        never had. So filter to this stem first, then strip it from the text:
        the caller never chose the scratch name and it means nothing to them.
        """
        if not stem:
            errors = list(self.errors)
        else:
            errors = [e for e in self.errors if stem.upper() in e.upper()]
        if not errors:
            return "Micro-Cap reported no error text for this circuit"
        out = []
        for e in errors:
            if stem:
                e = re.sub(rf"{re.escape(stem)}\S*\s*", "", e, flags=re.IGNORECASE)
            out.append(e.strip())
        return "; ".join(out)


_STAT_ROW = re.compile(
    r"^(?P<circuit>\S+)\s+(?P<analysis>/\w+)\s+(?P<nodes>\d+)\s+(?P<iter>\d+)\s+"
    r"(?P<rej>\d+)\s+(?P<sol>\d+)\s+(?P<run>[\d.]+)"
)


def bisect(items: list) -> tuple[list, list]:
    """Split a timed-out batch for retry.

    A circuit that hangs Micro-Cap on a modal dialog takes the whole batch down
    with it, so its neighbours must be retried apart from it. A single leftover
    is not assumed guilty — it may simply not have been reached before a
    neighbour wedged the run — so the caller re-runs it alone to get its own
    diagnosis. This just decides the split; empty and singleton inputs are
    returned unchanged for the caller to handle.
    """
    if len(items) <= 1:
        return items, []
    mid = len(items) // 2
    return items[:mid], items[mid:]


def parse_log(text: str) -> BatchLog:
    """Parse MC's batch .DOC log.

    Error lines are not anchored: MC prefixes them with the circuit file name,
    e.g. ``FOO.CIR Error Can't plot noise with other expressions.`` Matching
    only at line start silently loses every one of them.
    """
    log = BatchLog(raw=text)
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("Total"):
            # Summary counters — checked before the error scan, because
            # "Total runs with Error/Warnings 0" contains the word Error.
            if s.startswith("Total Circuits"):
                log.circuits = int(s.split()[-1])
            elif s.startswith("Total Simulations"):
                log.simulations = int(s.split()[-1])
            elif s.startswith("Total runs with Error/Warnings"):
                log.runs_with_errors = int(s.split()[-1])
            continue
        if re.search(r"\bError\b", s):
            log.errors.append(s)
        elif m := _STAT_ROW.match(s):
            log.stats.append(
                RunStats(
                    circuit=m["circuit"],
                    analysis=m["analysis"],
                    nodes=int(m["nodes"]),
                    iterations=int(m["iter"]),
                    rejected=int(m["rej"]),
                    solutions=int(m["sol"]),
                    run_time=float(m["run"]),
                )
            )
    return log


@dataclass
class Result:
    """Outcome of one simulation.

    Images are carried as bytes, not paths: every artefact is deleted from
    Micro-Cap's DATA folder once it has been read, so a run leaves nothing
    behind in the user's install.
    """

    name: str
    analysis: str
    numeric: NumericOutput
    log: BatchLog
    images: dict[str, bytes] = field(default_factory=dict)


@dataclass
class Job:
    """One circuit to run, for :meth:`MicroCap.run_many`."""

    key: str
    """Caller's identifier; results come back keyed by it."""
    source: str
    """SPICE netlist text, or .CIR schematic text."""
    kind: str = "ckt"
    """``ckt`` for a SPICE netlist, ``cir`` for a Micro-Cap schematic."""
    analysis: str = "transient"
    defines: dict[str, str] | None = None
    points: int | None = None
    """Override the circuit's own NPts. Needed for circuits drawn to be looked
    at rather than exported — NPts=0 yields a single useless row."""


class MicroCap:
    """Runs circuits through MC12 in batch mode.

    Every artefact is written flat into MC's DATA folder under an ``mcp_``
    prefix, because MC cannot resolve subdirectories in batch lines.

    Launching MC is expensive: each start reloads the 11 MB component library
    ("Loading Component Files"), which dominates the wall clock — a run whose
    solver takes 0.17 s costs 0.8 s end to end. Micro-Cap's own example batch
    shows setup falling 1.938 s -> 0.469 s -> 0.078 s across three circuits in
    one invocation. So prefer :meth:`run_many` over a loop of :meth:`simulate`.
    """

    def __init__(
        self,
        install: str | os.PathLike | None = None,
        keep_files: bool = False,
        window_mode: str = "hide",
    ):
        self.install = find_install(install)
        exe = _executable_in(self.install)
        if exe is None:  # find_install guarantees this, but be explicit
            raise MicroCapError(f"no Micro-Cap executable in {self.install}")
        self.exe = exe
        self.data = self.install / "DATA"
        self.keep_files = keep_files
        self.window_mode = window_mode
        """How to keep MC's windows out of the way when no image is wanted.

        Micro-Cap renders plots *through* its window, so suppression and image
        export are mutually exclusive — measured: ``hide`` cuts on-screen time
        from 97% to 10% but exports a black JPEG; ``offscreen`` only halves it
        and exports nothing at all. There is no setting that gives both.

        The driver therefore suppresses only when the caller asked for no
        images, which is the overwhelmingly common case: an agent wants
        numbers, not pictures."""

    def _stem(self, salt: int = 0) -> str:
        return f"{PREFIX}{os.getpid()}_{int(time.time() * 1000)}_{salt}"

    def _launch(self, batch: Path, timeout: float, hide: bool) -> None:
        """Start MC on a batch file and wait for it to finish.

        Popen rather than run(), because suppressing MC's windows needs its pid
        the instant it exists — every millisecond of delay is a frame of window
        flashing in the user's face.
        """
        proc = subprocess.Popen(
            [str(self.exe), f"@{batch}"],
            cwd=self.install,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            if hide and os.name == "nt":
                from .quiet import hidden

                with hidden(proc.pid, mode=self.window_mode):
                    proc.communicate(timeout=timeout)
            else:
                proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise

    def run_batch(
        self, batch_text: str, stem: str, timeout: float = 300, hide: bool = True
    ) -> BatchLog:
        """Run a batch file to completion and return its parsed log."""
        batch = self.data / f"{stem}.bat"
        batch.write_text(batch_text, encoding="ascii", errors="replace")
        try:
            self._launch(batch, timeout, hide)
        except subprocess.TimeoutExpired as e:
            raise MicroCapError(
                f"MC12 did not finish within {timeout}s. A modal error dialog "
                f"(bad netlist, non-convergence) hangs batch mode."
            ) from e
        finally:
            if not self.keep_files:
                batch.unlink(missing_ok=True)

        doc = self.data / f"{stem}.DOC"
        log = parse_log(doc.read_text(encoding="cp1252", errors="replace")) if doc.is_file() else BatchLog()
        if not self.keep_files:
            doc.unlink(missing_ok=True)
        return log

    def simulate(
        self,
        netlist: str,
        analysis: str = "transient",
        defines: dict[str, str] | None = None,
        plot_image: bool = True,
        timeout: float = 300,
    ) -> Result:
        """Run a SPICE netlist; return parsed numbers, solver stats and images.

        The netlist must carry a ``.PRINT`` line: without one MC emits an
        operating-point dump and no waveform data at all.
        """
        if analysis not in ANALYSES:
            raise ValueError(f"unknown analysis {analysis!r}; pick from {sorted(ANALYSES)}")
        switch, ext = ANALYSES[analysis]

        if ".print" not in netlist.lower():
            raise ValueError(
                "netlist has no .PRINT line, so MC12 emits no numeric data. "
                "Add e.g. '.PRINT TRAN V(OUT)' naming the outputs you want."
            )

        stem = self._stem()
        ckt = self.data / f"{stem}.CKT"
        ckt.write_text(netlist, encoding="ascii", errors="replace")

        parts = [ckt.name]
        for k, v in (defines or {}).items():
            parts.append(f'/DEF "{k} {v}"')
        parts += [f'/NOF "{stem}"', switch]

        images: dict[str, str] = {}
        if plot_image:
            images["plot"] = f"{stem}_plot.jpg"
            parts.append(f'/IA Page="Main" Output="{images["plot"]}"')

        # Suppressing the window and exporting a plot are mutually exclusive:
        # Micro-Cap renders the image through the window, so a suppressed one
        # yields a black JPEG. Only go quiet when no picture was asked for.
        log = self.run_batch(
            "BEGIN_COMMAND\n" + "\n".join(parts) + "\nEND_COMMAND\n",
            stem,
            timeout=timeout,
            hide=not plot_image,
        )

        try:
            numeric_path = self._find(f"{stem}.{ext}")
            if numeric_path is None:
                raise MicroCapError(
                    f"no {ext} produced — the netlist failed to parse or the "
                    f"analysis did not converge. Micro-Cap said: {log.why(stem)}"
                )
            result = Result(
                name=stem,
                analysis=analysis,
                numeric=parse_file(numeric_path),
                log=log,
            )
            for kind, fname in images.items():
                if p := self._find(fname):
                    result.images[kind] = p.read_bytes()
            return result
        finally:
            # MC also drops index files (<stem>_CKT.inx) beside its outputs;
            # purge the whole family, not just what we asked for.
            if not self.keep_files:
                self.purge(stem)

    def simulate_cir(
        self,
        cir_text: str,
        analysis: str = "transient",
        defines: dict[str, str] | None = None,
        plot_image: bool = False,
        timeout: float = 300,
        points: int | None = None,
    ) -> Result:
        """Run a Micro-Cap ``.CIR`` schematic and return its numbers.

        Numeric export is switched on for the requested analysis first: stock
        Micro-Cap circuits have it off, so they would otherwise run happily
        and produce no data.
        """
        from . import cir as cir_mod

        if analysis not in ANALYSES:
            raise ValueError(f"unknown analysis {analysis!r}; pick from {sorted(ANALYSES)}")
        switch, ext = ANALYSES[analysis]

        if analysis in cir_mod.NO_TRACE_ANALYSES:
            raise MicroCapError(
                f"{analysis} annotates values directly on the schematic and "
                f"defines no plotted traces, so it has no numeric output to "
                f"export. Use the plot image, or pick an analysis that plots."
            )

        available = cir_mod.analyses(cir_text)
        want = cir_mod.CIR_ANALYSIS[analysis]
        if available and want not in available:
            raise MicroCapError(
                f"this circuit has no {want} setup — it defines {available}. "
                f"Running an analysis the circuit was not built for yields nothing."
            )

        if points:
            cir_text = cir_mod.set_points(cir_text, analysis, points)
        cir_text, _ = cir_mod.resolve_numeric_range(cir_text, analysis)
        patched_text, n = cir_mod.enable_numeric_output(cir_text, analysis)
        if n == 0:
            raise MicroCapError(
                f"no {want} traces found to export. The circuit plots "
                f"{sorted({e.y for e in cir_mod.expressions(cir_text)})}."
            )

        stem = self._stem()
        cir = self.data / f"{stem}.CIR"
        cir.write_text(patched_text, encoding="cp1252", errors="replace")

        parts = [cir.name]
        for k, v in (defines or {}).items():
            parts.append(f'/DEF "{k} {v}"')
        parts += [f'/NOF "{stem}"', switch]

        images: dict[str, str] = {}
        if plot_image:
            images["plot"] = f"{stem}_plot.jpg"
            parts.append(f'/IA Page="Main" Output="{images["plot"]}"')
            images["schematic"] = f"{stem}_sch.jpg"
            parts.append(f'/IC Page="Main" Output="{images["schematic"]}"')

        # See simulate(): the window must stay up for an image to render.
        log = self.run_batch(
            "BEGIN_COMMAND\n" + "\n".join(parts) + "\nEND_COMMAND\n",
            stem,
            timeout=timeout,
            hide=not plot_image,
        )
        try:
            numeric_path = self._find(f"{stem}.{ext}")
            if numeric_path is None:
                raise MicroCapError(
                    f"no {ext} produced. Micro-Cap said: {log.why(stem)}"
                )
            result = Result(
                name=stem, analysis=analysis, numeric=parse_file(numeric_path), log=log
            )
            for kind, fname in images.items():
                if p := self._find(fname):
                    result.images[kind] = p.read_bytes()
            return result
        finally:
            if not self.keep_files:
                self.purge(stem)

    def run_many(
        self,
        jobs: list[Job],
        timeout: float | None = None,
        hide: bool = True,
        on_done: "callable | None" = None,
        _retry_on_timeout: bool = True,
        _retry_silent: bool = True,
    ) -> dict[str, Result | Exception]:
        """Run many circuits in a *single* Micro-Cap invocation.

        One process start, one component-library load, one splash — instead of
        one per circuit. Results come back keyed by ``Job.key``; a circuit that
        failed maps to the exception rather than aborting its neighbours.

        A circuit that hangs Micro-Cap on a modal dialog takes the whole
        invocation down with it. Rather than fail the batch, the run is
        bisected on timeout until the offender is alone: everyone else still
        gets their result, and only the real culprit is reported as hung.

        ``on_done`` is called with each key as its result is collected, for
        progress reporting.
        """
        from . import cir as cir_mod

        # Measured throughput is ~0.55 s/circuit. A 20 s/circuit budget means
        # waiting 500 s to notice a 25-batch is wedged; 6 s is still ~10x
        # headroom and cuts the detection cost by a factor of three.
        if timeout is None:
            timeout = max(60.0, 6.0 * len(jobs))

        prepared: list[tuple[Job, str, str]] = []  # job, stem, extension
        lines: list[str] = []
        errors: dict[str, Exception] = {}

        for n, job in enumerate(jobs):
            try:
                if job.analysis not in ANALYSES:
                    raise ValueError(f"unknown analysis {job.analysis!r}")
                switch, ext = ANALYSES[job.analysis]
                text = job.source

                if job.kind == "cir":
                    if job.analysis in cir_mod.NO_TRACE_ANALYSES:
                        raise MicroCapError(
                            f"{job.analysis} annotates the schematic and plots "
                            f"no traces, so it has no numeric output"
                        )
                    if job.points:
                        text = cir_mod.set_points(text, job.analysis, job.points)
                    text, _ = cir_mod.resolve_numeric_range(text, job.analysis)
                    text, k = cir_mod.enable_numeric_output(text, job.analysis)
                    if k == 0:
                        raise MicroCapError("circuit exports no traces for this analysis")
                elif ".print" not in text.lower():
                    raise ValueError("netlist has no .PRINT line, so MC emits no data")

                stem = self._stem(n)
                suffix = "CIR" if job.kind == "cir" else "CKT"
                (self.data / f"{stem}.{suffix}").write_text(
                    text, encoding="cp1252", errors="replace"
                )
                parts = [f"{stem}.{suffix}"]
                for key, val in (job.defines or {}).items():
                    parts.append(f'/DEF "{key} {val}"')
                parts += [f'/NOF "{stem}"', switch]
                lines.append("BEGIN_COMMAND\n" + "\n".join(parts) + "\nEND_COMMAND")
                prepared.append((job, stem, ext))
            except Exception as e:  # noqa: BLE001 — one bad job must not sink the batch
                errors[job.key] = e

        results: dict[str, Result | Exception] = dict(errors)
        if not prepared:
            return results

        batch_stem = self._stem(9999)
        try:
            log = self.run_batch("\n".join(lines) + "\n", batch_stem, timeout=timeout, hide=hide)
        except MicroCapError as e:
            self.purge(batch_stem)
            if len(prepared) == 1 or not _retry_on_timeout:
                for job, stem, _ in prepared:
                    results[job.key] = e
                    self.purge(stem)
                return results

            # A circuit that hangs Micro-Cap on a modal dialog takes the whole
            # invocation down with it, so its 24 innocent neighbours would fail
            # too. Micro-Cap writes each output as it finishes, though, so
            # harvest whatever already landed before re-running anything:
            # re-simulating circuits that already succeeded is what made the
            # retry cost minutes instead of seconds.
            unfinished = []
            for job, stem, ext in prepared:
                path = self._find(f"{stem}.{ext}")
                if path is None:
                    unfinished.append(job)
                    self.purge(stem)
                    continue
                try:
                    results[job.key] = Result(
                        name=stem, analysis=job.analysis, numeric=parse_file(path), log=BatchLog()
                    )
                except Exception as err:  # noqa: BLE001
                    results[job.key] = err
                finally:
                    self.purge(stem)
                    if on_done:
                        on_done(job.key)

            if not unfinished:
                return results
            if len(unfinished) == 1:
                # One circuit left, and its file never appeared. It is not
                # necessarily the one that hung — it may simply not have been
                # reached before a *neighbour* wedged the batch. Give it a run
                # of its own so its own .DOC survives and its real diagnosis is
                # kept, instead of blaming it for the timeout. Only if that lone
                # run also times out is it truly the culprit.
                job = unfinished[0]
                solo = self.run_many(
                    [job], timeout=max(30.0, 6.0), hide=hide, _retry_on_timeout=False
                )
                results.update(solo)
                if on_done:
                    on_done(job.key)
                return results

            for half in bisect(unfinished):
                if half:
                    results.update(
                        self.run_many(half, hide=hide, on_done=on_done, _retry_on_timeout=True)
                    )
            return results

        # MC's log names each run by its file, so stats map back by stem.
        stats_by_circuit = {s.circuit.upper(): s for s in log.stats}

        # A circuit can fail inside a batch while running fine on its own: no
        # file at all, or a file with no waveform table, and the batch log names
        # no fault for it. Measured, both happen and both are batch interactions
        # rather than properties of the circuit — ~19 of 28 "silent" cases and
        # every one of the sampled "no table" cases pass in isolation.
        #
        # The honest signal is whether Micro-Cap blamed *this* circuit: if the
        # log has an error line mentioning its file, the failure is real; if it
        # is quiet about the circuit, the batch just did not give us an answer.
        # The quiet ones are re-run solo below to get a true verdict; rooting
        # out the cause inside a closed, abandoned program is not worth it.
        retry_solo: list[Job] = []

        for job, stem, ext in prepared:
            stem_blamed = any(stem.upper() in e.upper() for e in log.errors)
            path = self._find(f"{stem}.{ext}")

            outcome: Result | Exception
            if path is None:
                outcome = MicroCapError(f"no {ext} produced. Micro-Cap said: {log.why(stem)}")
            else:
                try:
                    one = BatchLog(errors=[e for e in log.errors if stem.upper() in e.upper()])
                    if s := stats_by_circuit.get(f"{stem}.{ext[0]}CT".upper()):
                        one.stats = [s]
                    else:
                        for key, s in stats_by_circuit.items():
                            if key.startswith(stem.upper()):
                                one.stats = [s]
                                break
                    outcome = Result(
                        name=stem, analysis=job.analysis, numeric=parse_file(path), log=one
                    )
                except Exception as e:  # noqa: BLE001
                    outcome = e

            if not self.keep_files:
                self.purge(stem)

            # Retry only the no-*file* case. Empty-table results were checked
            # too — but every sampled one (Smith/S-parameter complex output,
            # operating-point-only circuits) fails identically in isolation, so
            # they are the circuit's own output, not a batch interaction.
            # Retrying them would add launches for no gain; measured +0.
            if _retry_silent and isinstance(outcome, Exception) and not stem_blamed:
                retry_solo.append(job)
                continue

            results[job.key] = outcome
            if on_done:
                on_done(job.key)

        if not self.keep_files:
            self.purge(batch_stem)

        for job in retry_solo:
            # One circuit per launch, and _retry_silent off so a still-failing
            # result is recorded as-is rather than looping.
            solo = self.run_many([job], hide=hide, _retry_silent=False)
            results[job.key] = solo[job.key]
            if on_done:
                on_done(job.key)

        return results

    def _find(self, name: str) -> Path | None:
        """MC uppercases some output names; accept either spelling."""
        for cand in (self.data / name, self.data / name.upper()):
            if cand.is_file():
                return cand
        return None

    def purge(self, stem: str) -> None:
        """Delete every file MC produced for one run."""
        for variant in (stem, stem.upper()):
            for p in self.data.glob(f"{variant}*"):
                p.unlink(missing_ok=True)

    def cleanup(self) -> None:
        """Remove any artefact this driver ever left in MC's DATA folder."""
        self.purge(PREFIX)
