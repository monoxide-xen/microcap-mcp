"""Baseline harness: how much of Micro-Cap's shipped library can we drive?

This is the measurement the project is judged on. Before any claim that an
agent "can simulate circuits", we need the floor: given ~475 reference designs
by the tool's own authors, what fraction can be run headless and yield parsed
numeric data — and where exactly does it break?

Every failure is bucketed into a class rather than counted as a lump, because
"73% works" is useless without knowing whether the other 27% is our bug, a
missing feature, or a circuit that legitimately has nothing to export.

Circuits are run in batches through one Micro-Cap invocation each. Launching MC
costs ~0.6 s of component-library loading regardless of how long the solve
takes, so batching cuts wall clock by roughly a third. The batch size is also
the blast radius: if one circuit hangs MC on a modal dialog, that batch is lost
and the rest continue.

Usage:
    uv run python eval/harness.py                  # sample
    uv run python eval/harness.py --all            # every circuit
    uv run python eval/harness.py --all --window   # with a progress window
    uv run python eval/harness.py --domain Filters
"""

from __future__ import annotations

import argparse
import json
import queue
import random
import re
import pathlib
import sys
import threading
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

sys.path.insert(0, "src")

from microcap_mcp import cir, corpus  # noqa: E402
from microcap_mcp.runner import Job, MicroCap, MicroCapError  # noqa: E402

# Failure buckets. The point is to separate our problems from the circuit's.
NO_TRACES = "circuit exports nothing"
DIGITAL = "digital traces only"
WRONG_ANALYSIS = "analysis not defined"
MC_FAILED = "micro-cap produced no data"
TIMEOUT = "timeout / hung dialog"
NO_TABLE = "output file has no data table"
CRASH = "our code crashed"
NOT_CONFIGURED = "circuit not set up for this analysis"

# d(...), dec(...), hex(...) plot logic states, not numbers. Such a circuit can
# never yield a numeric table — that is the circuit's nature, not our failure.
_DIGITAL_EXPR = re.compile(r"^\s*(d|dec|hex|bin|oct)\s*\(", re.IGNORECASE)


@dataclass
class Outcome:
    circuit: str
    domain: str
    analysis: str
    ok: bool
    bucket: str = ""
    points: int = 0
    rejected: int | None = None
    detail: str = ""


def classify(err: Exception, example=None, analysis: str = "") -> tuple[str, str]:
    msg = str(err)
    low = msg.lower()
    # A DC sweep with no source named is a circuit that was never configured
    # for DC: Micro-Cap writes a default [Limits] block for every analysis
    # whether the author used it or not, so the circuit looks DC-capable while
    # naming nothing to sweep. Counting that against the driver would measure
    # the library's completeness, not ours.
    #
    # Judged after the fact rather than pre-blocked: two source-less circuits
    # do run, and a rule that refused them would trade a real result for a
    # tidier one.
    if (
        "source not found" in low
        and analysis == "dc"
        and example is not None
        and not example.is_netlist
    ):
        text = example.path.read_text(encoding="cp1252", errors="replace")
        if cir.dc_swept_source(text) is None:
            return NOT_CONFIGURED, "DC sweep names no source; circuit has no DC setup"
    if isinstance(err, MicroCapError):
        if "annotates" in low or "exports no traces" in low:
            return NO_TRACES, msg
        if "has no" in low and "setup" in low:
            return WRONG_ANALYSIS, msg
        if "did not finish" in low:
            return TIMEOUT, msg
        return MC_FAILED, msg
    if isinstance(err, ValueError):
        return NO_TABLE, msg
    return CRASH, f"{type(err).__name__}: {msg}"


def targets(example) -> list[str]:
    """Which analyses this circuit is actually set up to plot."""
    if example.is_netlist:
        return ["transient"]
    text = example.path.read_text(encoding="cp1252", errors="replace")
    declared = set(cir.analyses(text))
    plotted = {e.analysis for e in cir.expressions(text) if e.y}
    return [
        name
        for name, spelling in cir.CIR_ANALYSIS.items()
        if spelling in declared and spelling in plotted
    ]


def is_digital_only(example, analysis: str) -> bool:
    if example.is_netlist:
        return False
    text = example.path.read_text(encoding="cp1252", errors="replace")
    want = cir.CIR_ANALYSIS[analysis]
    ys = [e.y for e in cir.expressions(text) if e.analysis == want and e.y]
    return bool(ys) and all(_DIGITAL_EXPR.match(y) for y in ys)


def build_jobs(pool, points: int) -> tuple[list[Job], dict[str, tuple], list[Outcome]]:
    jobs: list[Job] = []
    meta: dict[str, tuple] = {}
    skipped: list[Outcome] = []
    for ex in pool:
        text = ex.path.read_text(encoding="cp1252", errors="replace")
        for analysis in targets(ex):
            key = f"{ex.name}|{analysis}"
            if is_digital_only(ex, analysis):
                skipped.append(
                    Outcome(ex.name, ex.domain, analysis, False, DIGITAL,
                            detail="plots only logic states")
                )
                continue
            jobs.append(
                Job(
                    key=key,
                    source=text,
                    kind="ckt" if ex.is_netlist else "cir",
                    analysis=analysis,
                    points=None if ex.is_netlist else points,
                )
            )
            meta[key] = (ex.name, ex.domain, analysis)
    return jobs, meta, skipped


def run(pool, points: int, chunk: int, progress, sink=None) -> list[Outcome]:
    jobs, meta, results = build_jobs(pool, points)
    by_name = {ex.name: ex for ex in pool}
    mc = MicroCap()
    done = 0
    ok = 0
    progress(0, len(jobs), "", 0)

    for start in range(0, len(jobs), chunk):
        batch = jobs[start : start + chunk]
        out = mc.run_many(batch)  # driver sizes the timeout from measured throughput
        for job in batch:
            name, domain, analysis = meta[job.key]
            r = out.get(job.key)
            o = Outcome(name, domain, analysis, False)
            ex = by_name.get(name)
            if isinstance(r, Exception) or r is None:
                o.bucket, o.detail = classify(r or MicroCapError("no result"), ex, analysis)
            else:
                try:
                    table = r.numeric.table
                    o.ok = True
                    o.points = len(table.rows)
                    if r.log.stats:
                        o.rejected = r.log.stats[0].rejected
                except Exception as e:  # noqa: BLE001
                    o.bucket, o.detail = classify(e, ex, analysis)
            results.append(o)
            # Persist as we go. A sweep is half an hour of compute; keeping the
            # only copy in memory means one crash and it is all gone.
            if sink:
                sink.write(json.dumps(asdict(o)) + "\n")
                sink.flush()
            done += 1
            ok += o.ok
            # ok must travel with each tick: computing it only at the end makes
            # the live display read "ok 0, failed <everything>" for the whole run.
            progress(done, len(jobs), f"{name} {analysis}", ok)
    mc.cleanup()
    return results


def report(results: list[Outcome], elapsed: float) -> None:
    total = len(results)
    ok = sum(r.ok for r in results)
    # Runs the circuit could never answer: it plots only logic states, or was
    # never configured for the analysis. Reported separately rather than
    # folded in silently — excluding them raises the headline number, so the
    # exclusion has to be visible enough to argue with.
    na = sum(r.bucket in (DIGITAL, NOT_CONFIGURED) for r in results)
    answerable = total - na
    print("\n" + "=" * 72)
    print(f"RUNS {total}   OK {ok} ({ok/total:.0%} of all)   FAILED {total-ok}   {elapsed:.0f}s")
    if na and answerable:
        print(f"  {na} runs were unanswerable by construction (digital-only / no setup)")
        print(f"  {ok}/{answerable} = {ok/answerable:.0%} of runs the circuit could answer")
    print("=" * 72)

    buckets = Counter(r.bucket for r in results if not r.ok)
    if buckets:
        print("\nfailures by class:")
        for b, n in buckets.most_common():
            print(f"  {n:4d}  {b}")
            for r in [x for x in results if x.bucket == b][:1]:
                print(f"        e.g. {r.circuit}/{r.analysis}: {r.detail[:84]}")

    by: dict[str, list[Outcome]] = defaultdict(list)
    for r in results:
        by[r.analysis].append(r)
    print("\nby analysis:")
    for a, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        n = sum(x.ok for x in rs)
        print(f"  {a:26s} {n:3d}/{len(rs):3d}  {n/len(rs):4.0%}")

    dom: dict[str, list[Outcome]] = defaultdict(list)
    for r in results:
        dom[r.domain].append(r)
    print("\nweakest domains:")
    rates = [(sum(x.ok for x in rs) / len(rs), d, len(rs)) for d, rs in dom.items() if len(rs) >= 3]
    for rate, d, n in sorted(rates)[:8]:
        print(f"  {d:32s} {rate:4.0%}  ({n} runs)")

    stressed = [r for r in results if r.ok and r.rejected]
    if stressed:
        print(f"\nconverged but solver strained ({len(stressed)}):")
        for r in sorted(stressed, key=lambda x: -(x.rejected or 0))[:5]:
            print(f"  {r.circuit}/{r.analysis}: {r.rejected} rejected solutions")


def make_window(q: "queue.Queue"):
    """Optional progress window.

    Micro-Cap's splash flashes once per launch and says nothing useful. This
    shows what is actually wanted: how far the sweep is and what is failing.
    """
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Micro-Cap corpus evaluation")
    root.geometry("460x140")
    label = ttk.Label(root, text="starting...", anchor="w")
    label.pack(fill="x", padx=12, pady=(14, 4))
    bar = ttk.Progressbar(root, length=430, mode="determinate")
    bar.pack(padx=12)
    stat = ttk.Label(root, text="", anchor="w", foreground="#666")
    stat.pack(fill="x", padx=12, pady=(8, 0))

    def poll():
        try:
            while True:
                done, total, current, ok = q.get_nowait()
                if total:
                    bar["maximum"] = total
                    bar["value"] = done
                    label.config(text=f"{done} / {total}   {current}")
                    stat.config(text=f"ok {ok}   failed {done - ok}")
                if done and done >= total:
                    root.after(600, root.destroy)
                    return
        except queue.Empty:
            pass
        root.after(120, poll)

    root.after(120, poll)
    root.mainloop()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="every circuit")
    ap.add_argument("--domain", help="restrict to one domain")
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--points", type=int, default=200, help="override each circuit's NPts")
    ap.add_argument("--chunk", type=int, default=25, help="circuits per Micro-Cap launch")
    ap.add_argument("--window", action="store_true", help="show a progress window")
    ap.add_argument("--out", default="eval/results/outcomes.jsonl",
                    help="append every outcome here as it happens")
    args = ap.parse_args()

    pool = corpus.list_examples(args.domain) if args.domain else corpus.list_examples()
    if not args.all and len(pool) > args.sample:
        random.Random(args.seed).shuffle(pool)
        pool = pool[: args.sample]

    q: queue.Queue = queue.Queue()
    results: list[Outcome] = []
    t0 = time.time()

    def progress(done, total, current, ok):
        q.put((done, total, current, ok))
        if done:
            print(f"  [{done}/{total}] ok={ok} {current}", flush=True)

    def work():
        nonlocal results
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as sink:
            results = run(pool, args.points, args.chunk, progress, sink=sink)
        q.put((len(results), len(results), "done", sum(r.ok for r in results)))

    if args.window:
        t = threading.Thread(target=work, daemon=True)
        t.start()
        make_window(q)
        t.join()
    else:
        work()

    report(results, time.time() - t0)


if __name__ == "__main__":
    main()
