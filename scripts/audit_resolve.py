#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Audit the coin/fiat resolution pipeline over the full CMC listing.

Renders a fixed-width reconciliation table (plus a totals row) with one
column each for ambiguity (AMB), misses (MISS) and stale-cache hits
(STALE). Every non-empty row is followed by a detail line listing the
normalized key and the full candidate set, so any discrepancy stays
traceable. Output is deterministic across repeated runs.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptocmd.utils import (  # noqa: E402
    AmbiguousCoinError,
    InvalidCoinCode,
    build_coin_index,
    fiat_cache_snapshot,
    fiat_table_generation,
    get_coin_list,
    get_fiat_table,
    normalize,
    reload_fiat_ids,
    resolve_coin,
    resolve_fiat_id,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

WIDTHS = (5, 30, 30, 9, 3, 4, 5, 60)
HEADER = (
    "KIND",
    "INPUT",
    "NORMALIZED KEY",
    "RESOLVED",
    "AMB",
    "MISS",
    "STALE",
    "CANDIDATES",
)


def _fit(text, width):
    text = str(text)
    if len(text) > width:
        text = text[: max(width - 2, 1)] + ".."
    return text.ljust(width)


def _render_row(cells):
    return " ".join(_fit(cell, w) for cell, w in zip(cells, WIDTHS)).rstrip()


def _format_candidates(candidates):
    if not candidates:
        return "[]"
    return "[" + ", ".join(
        "id={id} symbol={symbol} name={name} slug={slug} rank={rank}".format(
            id=c.get("id"),
            symbol=c.get("symbol"),
            name=c.get("name"),
            slug=c.get("slug"),
            rank=c.get("rank"),
        )
        for c in candidates
    ) + "]"


def _short_candidates(candidates):
    if not candidates:
        return ""
    return ",".join(
        "{}:{}#{}".format(c.get("id"), c.get("symbol"), c.get("rank"))
        for c in candidates
    )


def audit_coins():
    """Resolve every normalized symbol/slug key from the full coin list."""
    coins = get_coin_list()
    index = build_coin_index(coins)
    inputs = {}
    for coin in coins:
        for raw in (coin.get("symbol"), coin.get("slug")):
            key = normalize(raw)
            if key:
                inputs.setdefault(key, set()).add(str(raw))

    rows = []
    totals = {"amb": 0, "miss": 0, "stale": 0}
    for key in sorted(inputs):
        coin_input = sorted(inputs[key])[0]
        try:
            coin = resolve_coin(coin_input, index=index)
        except AmbiguousCoinError as err:
            totals["amb"] += 1
            rows.append(
                ("COIN", coin_input, key, "-", 1, 0, 0, err.candidates)
            )
        except InvalidCoinCode:
            totals["miss"] += 1
            rows.append(("COIN", coin_input, key, "-", 0, 1, 0, None))
        else:
            rows.append(("COIN", coin_input, key, coin["id"], 0, 0, 0, None))
    return rows, totals


def audit_fiats():
    """Resolve every fiat code/name and verify cache invalidation."""
    table = get_fiat_table()
    labels = list(table["code_labels"]) + list(table["name_labels"])

    # Warm the resolution cache, then hot-reload the fiat table: every
    # cached entry from the previous generation must be invalidated.
    for label in labels:
        resolve_fiat_id(label)
    reload_fiat_ids()
    generation = fiat_table_generation()
    leftover = sorted(
        key
        for key, (gen, _value) in fiat_cache_snapshot().items()
        if gen != generation
    )

    rows = []
    totals = {"amb": 0, "miss": 0, "stale": 0}
    for key in leftover:
        totals["stale"] += 1
        rows.append(("FIAT", key, key, "-", 0, 0, 1, None))

    for label in sorted(labels, key=lambda s: (normalize(s), s)):
        key = normalize(label)
        try:
            cached = resolve_fiat_id(label)
        except ValueError:
            totals["miss"] += 1
            rows.append(("FIAT", label, key, "-", 0, 1, 0, None))
            continue
        fresh = resolve_fiat_id(label, use_cache=False)
        stale = 1 if cached != fresh else 0
        totals["stale"] += stale
        rows.append(("FIAT", label, key, cached, 0, 0, stale, None))
    return rows, totals


def main():
    coin_rows, coin_totals = audit_coins()
    fiat_rows, fiat_totals = audit_fiats()
    rows = coin_rows + fiat_rows
    totals = {k: coin_totals[k] + fiat_totals[k] for k in coin_totals}

    separator = " ".join("-" * w for w in WIDTHS)
    print(_render_row(HEADER))
    print(separator)
    for kind, coin_input, key, resolved, amb, miss, stale, candidates in rows:
        print(
            _render_row(
                (
                    kind,
                    coin_input,
                    key,
                    resolved,
                    amb,
                    miss,
                    stale,
                    _short_candidates(candidates),
                )
            )
        )
        if amb or miss or stale:
            print(
                "  -> key={!r} candidates={}".format(
                    key, _format_candidates(candidates)
                )
            )
    print(separator)
    print(
        _render_row(
            (
                "TOTAL",
                "{} rows".format(len(rows)),
                "",
                "",
                totals["amb"],
                totals["miss"],
                totals["stale"],
                "",
            )
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
