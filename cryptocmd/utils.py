#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function

import json
import os
import sys
import datetime
import re
from functools import lru_cache
from requests import get


_CMC_DATA_API = "https://api.coinmarketcap.com/data-api/v3"

_FIAT_IDS_PATH = os.path.join(os.path.dirname(__file__), "fiat_ids.json")

_FIAT_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")

_HEADERS = {"User-Agent": "Mozilla/5.0"}


@lru_cache(maxsize=None)
def normalize(value):
    """
    Normalize a coin or fiat identifier for comparison: strips leading and
    trailing whitespace, lowercases, and treats hyphens and underscores as
    equivalent separators.
    :param value: raw identifier (code, name, or slug); ``None`` normalizes to ""
    :return: normalized key string
    """

    if value is None:
        return ""
    return str(value).strip().lower().replace("-", "_")


def _load_fiat_ids():
    with open(_FIAT_IDS_PATH) as f:
        return json.load(f)


def _build_fiat_indexes(fiat_ids):
    code_ids, name_ids = {}, {}
    for key, value in fiat_ids.items():
        if _FIAT_CODE_PATTERN.match(key):
            code_ids[normalize(key)] = value
        else:
            name_ids[normalize(key)] = value
    return code_ids, name_ids


_FIAT_IDS = _load_fiat_ids()
_FIAT_CODE_IDS, _FIAT_NAME_IDS = _build_fiat_indexes(_FIAT_IDS)

_SUPPORTED_FIATS = ", ".join(sorted(k.upper() for k in _FIAT_CODE_IDS))

# Cache of normalized resolution results keyed by (scope, normalized key).
# Invalidated whenever the fiat table is hot-reloaded.
_RESOLVE_CACHE = {}


def reload_fiat_ids():
    """
    Hot-reload the fiat table from disk and invalidate cached resolutions.
    """

    global _FIAT_IDS, _FIAT_CODE_IDS, _FIAT_NAME_IDS, _SUPPORTED_FIATS
    _FIAT_IDS = _load_fiat_ids()
    _FIAT_CODE_IDS, _FIAT_NAME_IDS = _build_fiat_indexes(_FIAT_IDS)
    _SUPPORTED_FIATS = ", ".join(sorted(k.upper() for k in _FIAT_CODE_IDS))
    _RESOLVE_CACHE.clear()


def resolve_fiat_id(fiat):
    """
    Resolve a fiat code or name to its numeric CoinMarketCap convert id.
    Codes take priority over names; lookups are normalized and cached.
    :param fiat: fiat code (e.g. USD) or fiat name (e.g. US Dollar)
    :return: numeric convert id, or None when the fiat is unknown
    """

    key = normalize(fiat)
    token = ("fiat", key)
    if token not in _RESOLVE_CACHE:
        _RESOLVE_CACHE[token] = _FIAT_CODE_IDS.get(key, _FIAT_NAME_IDS.get(key))
    return _RESOLVE_CACHE[token]


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


def get_coin_list():
    """
    Fetch the full cryptocurrency listing from coinmarketcap.com.
    :return: list of coin dicts as returned by the CMC data API
        (id, name, symbol, slug, cmcRank, ...)
    """

    api_url = (
        "{}/cryptocurrency/listing?start=1&limit=5000"
        "&sortBy=market_cap&sortType=desc&convert=USD"
        "&cryptoType=all&tagType=all&audited=false"
    ).format(_CMC_DATA_API)

    json_data = get_url_data(api_url).json()
    error_code = json_data["status"]["error_code"]
    if str(error_code) == "0":
        return json_data["data"]["cryptoCurrencyList"]
    if str(error_code) == "400":
        raise InvalidCoinCode("coin listing is unavailable on coinmarketcap.com")
    raise Exception(json_data["status"]["error_message"])


def _coin_rank(coin):
    rank = coin.get("cmcRank")
    if isinstance(rank, bool) or not isinstance(rank, int):
        return float("inf")
    return rank


def _coin_sort_key(coin):
    return _coin_rank(coin), coin.get("id", float("inf"))


def build_coin_index(coins):
    """
    Build an index mapping normalized symbol/slug keys to matching coins.
    :param coins: list of coin dicts as returned by :func:`get_coin_list`
    :return: dict of normalized key -> list of coin dicts
    """

    index = {}
    for coin in coins:
        for raw in (coin.get("symbol"), coin.get("slug")):
            key = normalize(raw)
            if key:
                index.setdefault(key, []).append(coin)
    return index


def resolve_coin_id(coin_code, coin_name=None, coins=None, index=None):
    """
    Resolve a coin code or slug to a unique numeric coinmarketcap.com id.

    Candidates match on normalized symbol or slug. Multiple candidates are
    ordered by rank and then id (ascending) and the first one wins; when the
    top two candidates tie on rank an :class:`AmbiguousCoinError` is raised
    carrying the input, the normalized key, and the candidate list.

    :param coin_code: coin code of a cryptocurrency e.g. btc
    :param coin_name: coin name in case of many coins with same code e.g. sol -> solana, solcoin
    :param coins: (optional) pre-fetched coin listing
    :param index: (optional) pre-built index from :func:`build_coin_index`
    :return: numeric coin id for the cryptocurrency on coinmarketcap.com
    """

    key = normalize(coin_code)
    if index is None:
        if coins is None:
            coins = get_coin_list()
        index = build_coin_index(coins)

    candidates = []
    seen_ids = set()
    for coin in index.get(key, []):
        coin_id = coin.get("id")
        if coin_id not in seen_ids:
            seen_ids.add(coin_id)
            candidates.append(coin)

    if coin_name is not None:
        name_key = normalize(coin_name)
        candidates = [c for c in candidates if normalize(c.get("name")) == name_key]

    if not candidates:
        raise InvalidCoinCode(
            "'{}' coin code is unavailable on coinmarketcap.com".format(coin_code)
        )

    candidates.sort(key=_coin_sort_key)
    if len(candidates) > 1 and _coin_rank(candidates[0]) == _coin_rank(candidates[1]):
        raise AmbiguousCoinError(coin_code, key, candidates)
    return candidates[0]["id"]


def get_coin_id(coin_code, coin_name):
    """
    This method fetches the id of currency from the given code
    :param coin_code: coin code of a cryptocurrency e.g. btc
    :param coin_name: coin name in case of many coins with same code e.g. sol -> solana, solcoin
    :return: numeric coin id for the cryptocurrency on coinmarketcap.com
    """

    try:
        return resolve_coin_id(coin_code, coin_name)
    except Exception as e:
        print("Error fetching coin id data for coin code {}".format(coin_code))
        print("Error message:", e)
        raise e


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
    if convert_id is None:
        raise ValueError(f"Unknown fiat '{fiat}'. Supported: {_SUPPORTED_FIATS}.")

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
        # chunk's timeStart equals the previous chunk's timeEnd — no overlap, no gap.
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


class InvalidParameters(ValueError):
    """Passed parameters are invalid."""


class InvalidCoinCode(NotImplementedError):
    """This coin code is unavailable on 'coinmarketcap.com'"""


class AmbiguousCoinError(ValueError):
    """A coin identifier resolved to multiple equally-ranked coins."""

    def __init__(self, input_value, normalized_key, candidates):
        self.input_value = input_value
        self.normalized_key = normalized_key
        self.candidates = list(candidates)
        summary = ", ".join(
            "{}(id={}, rank={})".format(
                c.get("name"), c.get("id"), c.get("cmcRank")
            )
            for c in self.candidates
        )
        super().__init__(
            "Ambiguous coin identifier {!r} (normalized: {!r}); "
            "candidates: {}".format(input_value, normalized_key, summary)
        )
