#!/usr/bin/env python3
"""Audita ``ui/`` en busca de valores literales que deberían ser tokens.

Uso: ``python scripts/audit_ui.py [--strict] [--lines]``.
Con ``--strict`` sale con código 1 si hay algún hallazgo.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
TOKENS = Path("ui/theme/tokens.py")

# nombre -> (regex, ignorar tokens.py)
RULES: dict[str, tuple[re.Pattern[str], bool]] = {
    "border_radius literal": (re.compile(r"\bborder_radius\s*=\s*\d"), False),
    "RADIUS_BUTTON/INPUT/DIALOG/RAIL*": (re.compile(r"\bRADIUS_(BUTTON|INPUT|DIALOG|RAIL\w*)\b"), True),
    "size 8/9/10": (re.compile(r"\bsize\s*=\s*(8|9|10)\b(?!\.)"), False),
    "TEXT_DIM fuera de tokens": (re.compile(r"\bTEXT_DIM\b"), True),
    "padding/spacing literal": (re.compile(r"\b(padding|spacing)\s*=\s*\d"), False),
    "hex fuera de tokens": (re.compile(r"[\"']#[0-9A-Fa-f]{6,8}[\"']"), True),
    "ft.Animation(<num>)": (re.compile(r"\bft\.Animation\(\s*\d"), False),
}


def scan(root: Path = ROOT) -> dict[str, list[tuple[str, int, str]]]:
    """Devuelve ``{regla: [(ruta, línea, texto)]}`` para todos los .py bajo ``ui/``."""
    out: dict[str, list[tuple[str, int, str]]] = {k: [] for k in RULES}
    for path in sorted((root / "ui").rglob("*.py")):
        rel = path.relative_to(root)
        is_tokens = rel == TOKENS
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name, (rx, skip_tokens) in RULES.items():
                if skip_tokens and is_tokens:
                    continue
                if rx.search(line):
                    out[name].append((str(rel), n, line.strip()))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true", help="exit 1 si hay hallazgos")
    ap.add_argument("--lines", action="store_true", help="lista cada ruta:línea")
    args = ap.parse_args(argv)
    res = scan()
    total = 0
    for name, hits in res.items():
        total += len(hits)
        print(f"{name}: {len(hits)}")
        if args.lines:
            for f, n, text in hits:
                print(f"  {f}:{n}: {text}")
    print(f"TOTAL: {total}")
    return 1 if args.strict and total else 0


if __name__ == "__main__":
    sys.exit(main())
