"""
Unit tests for the shared normalization pipeline: normalize(), fiat
resolution (code priority, cache invalidation on hot reload), and coin
resolution (rank/id ordering, ambiguity errors).
"""

import json

import pytest

from cryptocmd import utils
from cryptocmd.utils import (
    AmbiguousCoinError,
    InvalidCoinCode,
    build_coin_index,
    normalize,
    resolve_coin_id,
    resolve_fiat_id,
)


def _coin(coin_id, symbol, name, slug, rank):
    return {
        "id": coin_id,
        "symbol": symbol,
        "name": name,
        "slug": slug,
        "cmcRank": rank,
    }


class TestNormalize:
    def test_case_and_whitespace(self):
        assert normalize("  BTC ") == "btc"

    def test_hyphen_and_underscore_are_equivalent(self):
        assert normalize("Shiba-Inu") == normalize("shiba_inu") == "shiba_inu"

    def test_none_normalizes_to_empty(self):
        assert normalize(None) == ""

    def test_results_are_cached(self):
        normalize("cache-check")
        info = normalize.cache_info()
        assert info.hits + info.misses > 0
        assert normalize("cache-check") == "cache_check"
        assert normalize.cache_info().hits > info.hits


class TestResolveFiatId:
    def test_code_lookup_is_normalized(self):
        assert resolve_fiat_id("usd") == 2781
        assert resolve_fiat_id(" USD ") == 2781

    def test_name_lookup_is_normalized(self):
        assert resolve_fiat_id("united states dollar") == 2781
        assert resolve_fiat_id(" United States Dollar ") == 2781

    def test_name_used_as_code_is_normalized_before_lookup(self):
        assert resolve_fiat_id("EURO") == 2790
        assert resolve_fiat_id(" euro ") == 2790

    def test_unknown_fiat_returns_none(self):
        assert resolve_fiat_id("NOPE") is None

    def test_code_takes_priority_over_name(self, monkeypatch):
        monkeypatch.setitem(utils._FIAT_CODE_IDS, "zzz", 111)
        monkeypatch.setitem(utils._FIAT_NAME_IDS, "zzz", 222)
        utils._RESOLVE_CACHE.clear()
        assert resolve_fiat_id("ZZZ") == 111

    def test_cache_invalidated_on_hot_reload(self, tmp_path, monkeypatch):
        assert resolve_fiat_id("USD") == 2781
        assert utils._RESOLVE_CACHE, "cache should be primed"

        table = {"USD": 2781, "US Dollar": 2781, "XTS": 9999, "Testcredit": 9999}
        path = tmp_path / "fiat_ids.json"
        path.write_text(json.dumps(table), encoding="utf-8")
        monkeypatch.setattr(utils, "_FIAT_IDS_PATH", str(path))
        try:
            utils.reload_fiat_ids()
            assert not utils._RESOLVE_CACHE, "reload must invalidate the cache"
            assert resolve_fiat_id("xts") == 9999
            assert resolve_fiat_id("TestCredit") == 9999
            assert resolve_fiat_id("EUR") is None
        finally:
            monkeypatch.undo()
            utils.reload_fiat_ids()

        assert resolve_fiat_id("EUR") == 2790


class TestResolveCoinId:
    def test_multiple_symbol_matches_pick_lowest_rank(self):
        coins = [
            _coin(3, "SOL", "Solcoin", "solcoin", 500),
            _coin(1, "SOL", "Solana", "solana", 5),
        ]
        assert resolve_coin_id("sol", coins=coins) == 1

    def test_slug_conflict_resolved_by_rank(self):
        coins = [
            _coin(7, "FOO", "Foo", "bar", 10),
            _coin(4, "BAR", "Bar", "bar-chain", 20),
        ]
        # 'bar' matches the symbol of id 4 and the slug of id 7; rank wins.
        assert resolve_coin_id(" BAR ", coins=coins) == 7

    def test_same_coin_matched_via_symbol_and_slug_is_deduplicated(self):
        coins = [_coin(1, "ABC", "Abc", "abc", 3)]
        assert resolve_coin_id("abc", coins=coins) == 1

    def test_rank_tie_raises_with_input_key_and_candidates(self):
        coins = [
            _coin(2, "AAA", "Alpha B", "alpha-b", 5),
            _coin(1, "AAA", "Alpha A", "alpha-a", 5),
        ]
        with pytest.raises(AmbiguousCoinError) as exc_info:
            resolve_coin_id(" aaa ", coins=coins)
        err = exc_info.value
        assert err.input_value == " aaa "
        assert err.normalized_key == "aaa"
        # candidates are ordered by rank, then id ascending
        assert [c["id"] for c in err.candidates] == [1, 2]
        assert "aaa" in str(err)

    def test_unranked_tie_raises(self):
        coins = [
            _coin(1, "AAA", "Alpha A", "alpha-a", None),
            _coin(2, "AAA", "Alpha B", "alpha-b", None),
        ]
        with pytest.raises(AmbiguousCoinError):
            resolve_coin_id("aaa", coins=coins)

    def test_coin_name_filter(self):
        coins = [
            _coin(1, "SOL", "Solana", "solana", 5),
            _coin(2, "SOL", "Solcoin", "solcoin", 6),
        ]
        assert resolve_coin_id("sol", " Solcoin ", coins=coins) == 2

    def test_no_match_raises_invalid_coin_code(self):
        with pytest.raises(InvalidCoinCode):
            resolve_coin_id("nope", coins=[])

    def test_build_coin_index_ignores_empty_keys(self):
        index = build_coin_index([{"id": 1, "symbol": "BTC", "name": "Bitcoin"}])
        assert index["btc"][0]["id"] == 1
        assert "" not in index
