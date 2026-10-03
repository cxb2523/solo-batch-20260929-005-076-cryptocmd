#!/usr/bin/env python
"""
Audit the coin/fiat resolution pipeline over the full CoinMarketCap listing
and the local fiat table, then render a fixed-width reconciliation table.

Columns: checked / ambiguous / missed / stale-cache counts per scope, plus a
TOTAL row. Any non-empty row is followed by detail lines listing the input,
its normalized key, and the candidate list.

Usage: python scripts/audit_resolve.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cryptocmd import utils
from cryptocmd.utils import (
    AmbiguousCoinError,
    InvalidCoinCode,
    build_coin_index,
    get_coin_list,
    normalize,
    reload_fiat_ids,
    resolve_coin_id,
    resolve_fiat_id,
)

W_SCOPE = 12
W_NUM = 10


def _row(scope, checked, ambiguous, missed, stale):
    return (
        scope.ljust(W_SCOPE)
        + str(checked).rjust(W_NUM)
        + str(ambiguous).rjust(W_NUM)
        + str(missed).rjust(W_NUM)
        + str(stale).rjust(W_NUM)
    )


def _format_candidates(candidates):
    if not candidates:
        return "[]"
    return "[{}]".format(
        ", ".join(
            "{}(id={}, rank={})".format(c.get("name"), c.get("id"), c.get("cmcRank"))
            for c in sorted(
                candidates,
                key=lambda c: (
                    c.get("cmcRank")
                    if isinstance(c.get("cmcRank"), int)
                    else float("inf"),
                    c.get("id") if isinstance(c.get("id"), int) else 0,
                ),
            )
        )
    )


def _audit_coin_scope(coins, index, field):
    scope = f"coin:{field}"
    checked = ambiguous = missed = 0
    details = []
    for coin in coins:
        raw = coin.get(field)
        key = normalize(raw)
        if not key:
            continue
        checked += 1
        try:
            resolve_coin_id(raw, index=index)
        except AmbiguousCoinError as err:
            ambiguous += 1
            details.append((scope, raw, key, err.candidates))
        except InvalidCoinCode:
            missed += 1
            details.append((scope, raw, key, []))
    return scope, checked, ambiguous, missed, details


def _audit_fiat_scope(kind):
    scope = f"fiat:{kind}"
    checked = missed = 0
    details = []
    for raw in sorted(utils._FIAT_IDS):
        is_code = normalize(raw) in utils._FIAT_CODE_IDS
        if (kind == "code") != is_code:
            continue
        checked += 1
        expected = utils._FIAT_IDS[raw]
        resolved = resolve_fiat_id(raw)
        if resolved != expected:
            missed += 1
            details.append((scope, raw, normalize(raw), []))
    return scope, checked, missed, details


def main():
    coins = get_coin_list()
    index = build_coin_index(coins)

    rows = []
    details = []

    for field in ("symbol", "slug"):
        scope, checked, ambiguous, missed, scope_details = _audit_coin_scope(
            coins, index, field
        )
        rows.append([scope, checked, ambiguous, missed, 0])
        details.extend(scope_details)

    # Prime the fiat resolution cache, then hot-reload the fiat table and
    # count entries that survive the reload (must be zero).
    for raw in utils._FIAT_IDS:
        resolve_fiat_id(raw)
    reload_fiat_ids()
    stale = len(utils._RESOLVE_CACHE)

    for kind in ("code", "name"):
        scope, checked, missed, scope_details = _audit_fiat_scope(kind)
        rows.append([scope, checked, 0, missed, stale])
        details.extend(scope_details)

    totals = ["TOTAL"] + [sum(row[i] for row in rows) for i in range(1, 5)]

    header = _row("scope", "checked", "ambiguous", "missed", "stale")
    print(header)
    print("-" * len(header))
    for row in rows:
        print(_row(*row))
    print("-" * len(header))
    print(_row(*totals))

    if details:
        print()
        print("details:")
        for scope, raw, key, candidates in sorted(details, key=lambda d: (d[0], d[2])):
            print(
                f"  [{scope}] input={raw!r} key={key!r} candidates={_format_candidates(candidates)}"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
