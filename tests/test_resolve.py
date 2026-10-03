"""
Tests for the shared normalization/resolution pipeline in cryptocmd.utils.

All tests are offline: coin listings are injected directly and the fiat
table is reloaded from the bundled fiat_ids.json.
"""

import pytest

from cryptocmd import core, utils
from cryptocmd.utils import (
    AmbiguousCoinError,
    InvalidCoinCode,
    normalize,
    resolve_coin,
    resolve_fiat_id,
)


@pytest.fixture(autouse=True)
def _restore_fiat_table():
    """Ensure monkeypatched fiat tables never leak across tests."""
    yield
    utils.reload_fiat_ids()


def _coin(coin_id, symbol, name, slug, rank):
    return {
        "id": coin_id,
        "symbol": symbol,
        "name": name,
        "slug": slug,
        "cmcRank": rank,
    }


class TestNormalize:
    def test_shared_between_core_and_utils(self):
        assert core.normalize is utils.normalize

    def test_case_whitespace_hyphen_underscore(self):
        assert normalize("  Bitcoin_Cash\n") == "bitcoin cash"
        assert normalize("BITCOIN CASH") == "bitcoin cash"
        assert normalize("bitcoin-cash") == normalize("Bitcoin_Cash")

    def test_idempotent(self):
        assert normalize(normalize(" A-B_C ")) == normalize(" A-B_C ")

    def test_none_becomes_empty(self):
        assert normalize(None) == ""


class TestResolveCoin:
    def test_multiple_symbol_matches_pick_best_rank(self):
        coins = [
            _coin(2, "SOL", "Solcoin", "solcoin", 200),
            _coin(1, "SOL", "Solana", "solana", 5),
        ]
        assert resolve_coin("sol", coins=coins)["id"] == 1

    def test_tied_rank_raises_with_context(self):
        coins = [
            _coin(9, "ABC", "Abc One", "abc-one", 7),
            _coin(3, "ABC", "Abc Two", "abc-two", 7),
        ]
        with pytest.raises(AmbiguousCoinError) as excinfo:
            resolve_coin(" ABC ", coins=coins)
        err = excinfo.value
        assert err.coin_input == " ABC "
        assert err.normalized_key == "abc"
        # candidates sorted by rank, then id ascending
        assert [c["id"] for c in err.candidates] == [3, 9]
        message = str(err)
        assert " ABC " in message
        assert "abc" in message
        assert "abc-one" in message and "abc-two" in message

    def test_slug_conflict_resolved_by_rank(self):
        coins = [
            _coin(1, "XYZ", "Ex Why Zee", "ex-why-zee", 10),
            _coin(2, "ABC", "Xyz", "xyz", 20),
        ]
        # "xyz" matches the symbol of coin 1 and the slug of coin 2;
        # the better (lower) rank wins.
        assert resolve_coin("xyz", coins=coins)["id"] == 1

    def test_coin_name_filters_candidates(self):
        coins = [
            _coin(1, "SOL", "Solana", "solana", 5),
            _coin(2, "SOL", "Solcoin", "solcoin", 200),
        ]
        assert resolve_coin("sol", coin_name="SolCoin", coins=coins)["id"] == 2

    def test_no_match_raises_invalid_coin_code(self):
        with pytest.raises(InvalidCoinCode):
            resolve_coin("nope", coins=[_coin(1, "ABC", "Abc", "abc", 1)])

    def test_unranked_tie_raises(self):
        coins = [
            {"id": 1, "symbol": "ABC", "name": "A", "slug": "a"},
            {"id": 2, "symbol": "ABC", "name": "B", "slug": "b"},
        ]
        with pytest.raises(AmbiguousCoinError):
            resolve_coin("abc", coins=coins)

    def test_single_unranked_match_resolves(self):
        coins = [{"id": 1, "symbol": "BTC", "name": "Bitcoin"}]
        assert resolve_coin("btc", coins=coins)["id"] == 1


class TestResolveFiat:
    def test_code_lookup_is_normalized(self):
        assert resolve_fiat_id("USD") == 2781
        assert resolve_fiat_id(" usd ") == 2781

    def test_name_lookup_is_normalized(self):
        assert resolve_fiat_id("United States Dollar") == 2781
        assert resolve_fiat_id(" united_states-dollar ") == 2781

    def test_code_beats_name(self, monkeypatch):
        table = {
            "codes": {"usd": 111},
            "names": {"usd": 222},
            "code_labels": ["USD"],
            "name_labels": ["usd"],
        }
        monkeypatch.setattr(utils, "_load_fiat_table", lambda: table)
        utils.reload_fiat_ids()
        assert resolve_fiat_id("usd") == 111

    def test_unknown_fiat_raises(self):
        with pytest.raises(ValueError):
            resolve_fiat_id("not-a-fiat")

    def test_cache_invalidated_on_hot_reload(self):
        assert resolve_fiat_id("usd") == 2781
        assert utils.fiat_cache_snapshot()
        generation = utils.fiat_table_generation()
        utils.reload_fiat_ids()
        assert utils.fiat_table_generation() == generation + 1
        assert utils.fiat_cache_snapshot() == {}
        assert resolve_fiat_id("usd") == 2781

    def test_fiat_table_maps_names_and_codes_to_ints(self):
        table = utils.get_fiat_table()
        assert table["codes"]
        assert table["names"]
        for mapping in (table["codes"], table["names"]):
            for key, value in mapping.items():
                assert key == normalize(key)
                assert isinstance(value, int)
