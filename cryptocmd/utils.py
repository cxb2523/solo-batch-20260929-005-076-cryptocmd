#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import json
import os
import re
import sys
import datetime
from functools import lru_cache
from requests import get


_CMC_DATA_API = "https://api.coinmarketcap.com/data-api/v3"

_FIAT_IDS_PATH = os.path.join(os.path.dirname(__file__), "fiat_ids.json")

_HEADERS = {"User-Agent": "Mozilla/5.0"}


@lru_cache(maxsize=None)
def normalize(value):
    """
    Shared normalization for coin and fiat lookup keys.

    Lowercases the input, strips leading/trailing whitespace and unifies
    runs of hyphens, underscores and whitespace into single spaces, so
    e.g. slugs ("bitcoin-cash") and names ("Bitcoin Cash") compare equal.
    """
    if value is None:
        return ""
    return re.sub(r"[-_\s]+", " ", str(value)).strip().lower()


class InvalidParameters(ValueError):
    """Passed parameters are invalid."""


class InvalidCoinCode(NotImplementedError):
    """This coin code is unavailable on 'coinmarketcap.com'"""


class AmbiguousCoinError(ValueError):
    """A coin lookup matched multiple coins that tie for the best rank."""

    def __init__(self, coin_input, normalized_key, candidates):
        self.coin_input = coin_input
        self.normalized_key = normalized_key
        self.candidates = list(candidates)
        super(AmbiguousCoinError, self).__init__(
            "Ambiguous coin {!r} (normalized key {!r}); "
            "tied candidates: {}".format(
                coin_input, normalized_key, self.candidates
            )
        )


# ---------------------------------------------------------------------------
# Fiat table (hot-reloadable) and cached fiat resolution
# ---------------------------------------------------------------------------


def _load_fiat_table():
    with open(_FIAT_IDS_PATH) as f:
        raw = json.load(f)
    return {
        "codes": {normalize(k): int(v) for k, v in raw["codes"].items()},
        "names": {normalize(k): int(v) for k, v in raw["names"].items()},
        "code_labels": sorted(raw["codes"]),
        "name_labels": sorted(raw["names"]),
    }


_FIAT_TABLE = {"codes": {}, "names": {}, "code_labels": [], "name_labels": []}
_FIAT_GENERATION = -1
_SUPPORTED_FIATS = ""
_fiat_resolve_cache = {}


def reload_fiat_ids():
    """
    Hot-reload the fiat table from fiat_ids.json.

    Bumps the table generation and invalidates every cached fiat
    resolution so stale ids are never served after a reload.
    """
    global _FIAT_TABLE, _FIAT_GENERATION, _SUPPORTED_FIATS
    _FIAT_TABLE = _load_fiat_table()
    _FIAT_GENERATION += 1
    _fiat_resolve_cache.clear()
    _SUPPORTED_FIATS = ", ".join(_FIAT_TABLE["code_labels"])
    return _FIAT_TABLE


def get_fiat_table():
    """Return the active fiat table (normalized code/name -> integer id)."""
    return _FIAT_TABLE


def fiat_table_generation():
    """Return the generation of the active fiat table."""
    return _FIAT_GENERATION


def fiat_cache_snapshot():
    """Return {normalized key: (generation, fiat id)} for cache auditing."""
    return dict(_fiat_resolve_cache)


def lookup_fiat_id(fiat):
    """
    Uncached fiat lookup. Normalized code matches win over name matches.
    Returns the integer id, or None when the fiat is unknown.
    """
    key = normalize(fiat)
    if key in _FIAT_TABLE["codes"]:
        return _FIAT_TABLE["codes"][key]
    return _FIAT_TABLE["names"].get(key)


def resolve_fiat_id(fiat, use_cache=True):
    """
    Resolve a fiat code or name to its integer id on coinmarketcap.com.

    Code lookup takes priority over name lookup. Results are cached per
    fiat-table generation; reload_fiat_ids() invalidates the cache.
    Raises ValueError for unknown fiats.
    """
    key = normalize(fiat)
    if use_cache:
        cached = _fiat_resolve_cache.get(key)
        if cached is not None and cached[0] == _FIAT_GENERATION:
            return cached[1]
    fiat_id = lookup_fiat_id(fiat)
    if fiat_id is None:
        raise ValueError(
            "Unknown fiat '{}'. Supported: {}.".format(fiat, _SUPPORTED_FIATS)
        )
    if use_cache:
        _fiat_resolve_cache[key] = (_FIAT_GENERATION, fiat_id)
    return fiat_id


reload_fiat_ids()


def get_url_data(url):
    """
    This method downloads the data of the web page.
    :param url: 'url' of the web page to download
    :return: response object of get request of the 'url'
    """

    try:
        response = get(url, headers=_HEADERS)
        return response
    except Exception as e:
        print("Error message (get_url_data) :", e)
        raise e


# ---------------------------------------------------------------------------
# Coin listing and cached coin resolution
# ---------------------------------------------------------------------------

_coin_list_cache = None
_coin_index_cache = None
_coin_resolve_cache = {}


def get_coin_list():
    """
    Fetch the full cryptocurrency listing from coinmarketcap.com.

    :return: list of coin dicts as returned by the CMC data API
    """

    global _coin_list_cache
    if _coin_list_cache is not None:
        return _coin_list_cache

    api_url = (
        "{}/cryptocurrency/listing?start=1&limit=5000"
        "&sortBy=market_cap&sortType=desc&convert=USD"
        "&cryptoType=all&tagType=all&audited=false"
    ).format(_CMC_DATA_API)

    json_data = get_url_data(api_url).json()
    error_code = json_data["status"]["error_code"]
    if str(error_code) == "400":
        raise InvalidCoinCode("coin listing is unavailable on coinmarketcap.com")
    if str(error_code) != "0":
        raise Exception(json_data["status"]["error_message"])
    _coin_list_cache = json_data["data"]["cryptoCurrencyList"]
    return _coin_list_cache


def build_coin_index(coins):
    """Index coins by normalized symbol and slug for repeatable lookups."""
    index = {}
    for coin in coins:
        keys = {normalize(coin.get("symbol")), normalize(coin.get("slug"))}
        for key in keys:
            if key:
                index.setdefault(key, []).append(coin)
    return index


def _get_coin_index():
    global _coin_index_cache
    if _coin_index_cache is None:
        _coin_index_cache = build_coin_index(get_coin_list())
    return _coin_index_cache


def _coin_rank(coin):
    rank = coin.get("cmcRank")
    return rank if isinstance(rank, int) else float("inf")


def _coin_candidate(coin):
    return {
        "id": coin.get("id"),
        "symbol": coin.get("symbol"),
        "name": coin.get("name"),
        "slug": coin.get("slug"),
        "rank": coin.get("cmcRank"),
    }


def resolve_coin(coin_code, coin_name=None, coins=None, index=None):
    """
    Resolve a coin code to a unique coin from the listing.

    Candidates match on normalized symbol or slug. The unique winner is
    picked by rank, then id (both ascending). If the top two candidates
    tie on rank, an AmbiguousCoinError is raised carrying the input, the
    normalized key and the candidate list.
    """
    if index is None:
        index = build_coin_index(coins) if coins is not None else _get_coin_index()
    code_key = normalize(coin_code)
    candidates = list(index.get(code_key, ()))
    if coin_name is not None:
        name_key = normalize(coin_name)
        candidates = [c for c in candidates if normalize(c.get("name")) == name_key]
    if not candidates:
        raise InvalidCoinCode(
            "'{}' coin code is unavailable on coinmarketcap.com".format(coin_code)
        )
    candidates.sort(key=lambda c: (_coin_rank(c), c.get("id") or 0))
    if len(candidates) > 1 and _coin_rank(candidates[0]) == _coin_rank(candidates[1]):
        raise AmbiguousCoinError(
            coin_code, code_key, [_coin_candidate(c) for c in candidates]
        )
    return candidates[0]


def get_coin_id(coin_code, coin_name):
    """
    This method fetches the id of currency from the given code
    :param coin_code: coin code of a cryptocurrency e.g. btc
    :param coin_name: coin name in case of many coins with same code e.g. sol -> solana, solcoin
    :return: numeric coin id for the cryptocurrency on coinmarketcap.com
    """

    cache_key = (
        normalize(coin_code),
        normalize(coin_name) if coin_name is not None else None,
    )
    if cache_key in _coin_resolve_cache:
        return _coin_resolve_cache[cache_key]
    try:
        coin = resolve_coin(coin_code, coin_name)
    except Exception as e:
        print("Error fetching coin id data for coin code {}".format(coin_code))
        print("Error message:", e)
        raise e
    _coin_resolve_cache[cache_key] = coin["id"]
    return coin["id"]


def download_coin_data(
    coin_code, start_date, end_date, fiat, coin_name, id_number=None
):
    """
    Download price history for the specified cryptocurrency and time range from CoinMarketCap.

    :param coin_code: coin code of a cryptocurrency e.g. btc
    :param start_date: date since when to scrape data (in the format of dd-mm-yyyy)
    :param end_date: date to which scrape the data (in the format of dd-mm-yyyy).
        Defaults to today. CMC only returns completed daily candles, so the latest
        candle returned will always be the last completed UTC day regardless of this value.
    :param fiat: fiat code eg. USD, EUR
    :param coin_name: coin name in case of many coins with same code e.g. sol -> solana, solcoin
    :param id_number: id number for the token on coinmarketcap. Will override coin_code and coin_name when provided.

    :return: json data with historical OHLCV data for the cryptocurrency
    """

    if start_date is None:
        # default start date on coinmarketcap.com
        start_date = "28-4-2013"

    if end_date is None:
        end_date = datetime.date.today().strftime("%d-%m-%Y")

    coin_id = id_number if id_number else get_coin_id(coin_code, coin_name)
    convert_id = resolve_fiat_id(fiat)

    start_dt = datetime.datetime.strptime(start_date, "%d-%m-%Y").replace(
        tzinfo=datetime.timezone.utc
    )
    end_dt = datetime.datetime.strptime(end_date, "%d-%m-%Y").replace(
        tzinfo=datetime.timezone.utc
    )

    try:
        # CMC caps responses at ~365 entries per request; split into yearly chunks
        # so long/all-time ranges always return daily granularity.
        # timeStart is exclusive (first returned day = timeStart + 1), so each
        # chunk's timeStart equals the previous chunk's timeEnd - no overlap, no gap.
        all_quotes = []
        result_json = None
        chunk_start = start_dt - datetime.timedelta(days=1)

        while chunk_start < end_dt:
            chunk_end = min(chunk_start + datetime.timedelta(days=365), end_dt)
            api_url = (
                "{}/cryptocurrency/historical"
                "?id={}&convertId={}&timeStart={}&timeEnd={}"
            ).format(
                _CMC_DATA_API,
                coin_id,
                convert_id,
                int(chunk_start.timestamp()),
                int(chunk_end.timestamp()),
            )
            json_data = get_url_data(api_url).json()
            status = json_data.get("status", {})
            error_code = status.get("error_code")
            if error_code and str(error_code) != "0":
                raise Exception(status.get("error_message", "Unknown error"))
            if result_json is None:
                result_json = json_data
            all_quotes.extend(json_data["data"]["quotes"])
            chunk_start = chunk_end + datetime.timedelta(days=1)

        result_json["data"]["quotes"] = all_quotes

        if id_number:
            show_coin_info = False
            if coin_code and normalize(coin_code) != normalize(
                result_json["data"]["symbol"]
            ):
                print(
                    f"INFO: Using 'id_number'! The 'coin_code' ({coin_code}) provided "
                    + "is different from the symbol returned."
                )
                show_coin_info = True
            if coin_name and normalize(coin_name) != normalize(
                result_json["data"]["name"]
            ):
                print(
                    f"INFO: Using 'id_number'! The 'coin_name' ({coin_name}) provided "
                    + "is different from the name returned."
                )
                show_coin_info = True
            if show_coin_info:
                print(
                    f"""The returned data belongs to coin "{result_json['data']['name']}", """
                    + f"""with symbol "{result_json['data']['symbol']}" """
                )
        return result_json
    except Exception as e:
        print(
            "Error fetching price data for {} for interval '{}' and '{}'".format(
                f"(id {id_number})" if id_number else coin_code,
                start_date,
                end_date,
            )
        )
        print("Error message (download_data) :", e)
        raise e


def _replace(s, bad_chars):
    if sys.version_info > (3, 0):
        # For Python 3
        without_bad_chars = str.maketrans("", "", bad_chars)
        return s.translate(without_bad_chars)
    else:
        # For Python 2
        import string

        identity = string.maketrans("", "")
        return s.translate(identity, bad_chars)
