"""Access to the reference circuits shipped with Micro-Cap.

MC12's DATA folder holds ~470 working circuits, already sorted into domain
folders (Filters, Oscillators, PWM Switch, RF Amplifiers, Vacuum Tubes, ...).
They are the ground truth an agent should reach for before inventing a
topology from memory, and they double as an evaluation set.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

from .runner import find_install

# Circuits sitting loose in DATA rather than in a domain folder.
ROOT_DOMAIN = "(root)"


@dataclass(frozen=True)
class Example:
    name: str
    domain: str
    path: Path

    @property
    def is_netlist(self) -> bool:
        """.CKT files are plain SPICE; .CIR files also carry schematic layout."""
        return self.path.suffix.upper() == ".CKT"


@cache
def _index() -> dict[str, list[Example]]:
    data = find_install() / "DATA"
    by_domain: dict[str, list[Example]] = {}
    for path in sorted(data.rglob("*")):
        if path.suffix.upper() not in (".CIR", ".CKT") or not path.is_file():
            continue
        if path.name.upper().startswith("MCP_"):  # our own scratch files
            continue
        rel = path.relative_to(data)
        domain = rel.parts[0] if len(rel.parts) > 1 else ROOT_DOMAIN
        by_domain.setdefault(domain, []).append(
            Example(name=path.stem, domain=domain, path=path)
        )
    return by_domain


def domains() -> dict[str, int]:
    """Domain name -> number of reference circuits."""
    return {d: len(v) for d, v in sorted(_index().items())}


def list_examples(domain: str | None = None) -> list[Example]:
    idx = _index()
    if domain is None:
        return [e for v in idx.values() for e in v]
    for key in idx:
        if key.lower() == domain.lower():
            return idx[key]
    raise KeyError(f"no domain {domain!r}; have {sorted(idx)}")


def find(name: str) -> Example:
    """Find one reference circuit by name (case-insensitive)."""
    for e in list_examples():
        if e.name.lower() == name.lower():
            return e
    raise KeyError(f"no reference circuit named {name!r}")


def search(query: str, limit: int = 25) -> list[Example]:
    """Substring search over circuit name, domain, and domain purpose.

    The shipped names are cryptic — a bandpass filter is ``BPFILT`` — so a
    plain name search misses obvious queries: ``search("bandpass")`` found
    nothing though the corpus has bandpass filters. Matching the query against
    each domain's purpose ("...low/high/band-pass, notch") bridges the gap, so
    a semantic term reaches the right domain's circuits. Name/domain hits rank
    first; purpose-only hits follow.
    """
    from .knowledge import DOMAIN_PURPOSE

    q = query.lower().replace("-", "")

    def norm(s: str) -> str:
        return s.lower().replace("-", "")

    direct, viapurpose = [], []
    for e in list_examples():
        if q in norm(e.name) or q in norm(e.domain):
            direct.append(e)
        elif q in norm(DOMAIN_PURPOSE.get(e.domain, "")):
            viapurpose.append(e)
    return (direct + viapurpose)[:limit]
