"""Selective ONS Table 38 extraction for the ten UK CPI exclusion indices."""

from __future__ import annotations

import io
import logging
import math
import re
import time
from datetime import date, datetime
from typing import SupportsFloat, SupportsIndex
from urllib.parse import urlparse

import httpx
import pandas as pd

from scripts.config import (
    BACKOFF_FACTOR,
    MAX_DOWNLOAD_BYTES,
    MAX_RETRIES,
    MAX_RETRY_DELAY,
    RATE_LIMIT_BACKOFF,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from scripts.special_aggregates import EX_CPI_SPECIAL_AGGREGATES

logger = logging.getLogger(__name__)
SOURCE_NAME = "Office for National Statistics"
RELEASE_NAME = "Consumer price inflation: special aggregates"
COUNTRY_CURRENCY = "GBR"
SOURCE_URL = (
    "https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/consumerpriceinflation"
)
CPI_DOWNLOAD_URL = (
    "https://www.ons.gov.uk/file?uri=%2Feconomy%2Finflationandpriceindices%2Fdatasets%2F"
    "consumerpriceinflation%2Fcurrent%2Fconsumerpriceinflationdetailedreferencetables.xlsx"
)
TABLE38_DATASET = "ONS consumer price inflation detailed reference tables, Table 38"
TABLE38_PROVENANCE = (
    "ONS states that Table 38 figures are not accredited official statistics and "
    "should be used for analytical purposes only"
)
TABLE38_SHEET = "Table 38"
TABLE38_CODE_ROW = 4
TABLE38_CDID_ROW = 5
TABLE38_NAME_ROW = 6
TABLE38_FIRST_DATA_ROW = 7
TABLE38_DATE_COLUMN = 1
TABLE38_FIRST_SERIES_COLUMN = 2
TARGET_CDIDS = frozenset(row["index_cdid"] for row in EX_CPI_SPECIAL_AGGREGATES)
ECO_GROUPS = frozenset({"consumer_prices"})
UNITS = frozenset({"index"})
FREQUENCIES = frozenset({"monthly"})
ALLOWED_HOSTS = frozenset({"www.ons.gov.uk", "ons.gov.uk"})
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_TRANSFER_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})
_SERIES_CATALOG: dict[str, dict[str, str]] = {}
_LAST_PUBLISH_DATE: date | None = None
_MISSING = (None, pd.NaT, pd.NA)


def make_series_id(native_id: str) -> str:
    native = re.sub(r"[^A-Z0-9]+", "", native_id.strip().upper())
    if native not in TARGET_CDIDS:
        raise ValueError(f"Unsupported EX-CPI CDID: {native_id}")
    return f"EXCPI_INDEX_NATIVE_{native}"


def parse_series_id(series_id: str) -> tuple[str, str, str, str]:
    parts = series_id.split("_")
    if len(parts) != 4 or parts[:3] != ["EXCPI", "INDEX", "NATIVE"]:
        raise ValueError(f"Invalid EX-CPI series_id: {series_id}")
    if parts[3] not in TARGET_CDIDS:
        raise ValueError(f"Unknown EX-CPI native CDID: {parts[3]}")
    return tuple(parts)  # type: ignore[return-value]


def get_series_catalog() -> dict[str, dict[str, str]]:
    return {key: value.copy() for key, value in _SERIES_CATALOG.items()}


def get_last_publish_date() -> date | None:
    return _LAST_PUBLISH_DATE


def build_client() -> httpx.Client:
    return httpx.Client(
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        trust_env=True,
        follow_redirects=False,
    )


def _retry_delay(attempt: int, response: httpx.Response | None) -> float:
    delay = float(BACKOFF_FACTOR**attempt)
    if response is not None and response.status_code == 429:
        delay = max(delay, RATE_LIMIT_BACKOFF * (attempt + 1))
        retry_after = response.headers.get("retry-after", "").strip()
        if retry_after.isdigit():
            delay = max(delay, float(retry_after))
    return min(delay, MAX_RETRY_DELAY)


def _bounded_body(response: httpx.Response, url: str) -> bytes:
    declared = response.headers.get("content-length", "").strip()
    if declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"ONS response exceeds download limit: {url}")
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"ONS response exceeded {MAX_DOWNLOAD_BYTES} bytes: {url}")
        chunks.append(chunk)
    return b"".join(chunks)


def http_get(client: httpx.Client, url: str, method: str = "GET") -> httpx.Response:
    if urlparse(url).hostname not in ALLOWED_HOSTS:
        raise ValueError(f"Refusing non-ONS URL: {url}")
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        response: httpx.Response | None = None
        try:
            with client.stream(method, url) as streamed:
                response = streamed
                if streamed.status_code not in RETRYABLE_STATUSES:
                    streamed.raise_for_status()
                    body = _bounded_body(streamed, url)
                    return httpx.Response(
                        streamed.status_code,
                        headers=[
                            (name, value)
                            for name, value in streamed.headers.multi_items()
                            if name.lower() not in _TRANSFER_HEADERS
                        ],
                        content=body,
                        request=streamed.request,
                    )
                streamed.read()
                last_error = httpx.HTTPStatusError(
                    f"retryable status {streamed.status_code}",
                    request=streamed.request,
                    response=streamed,
                )
        except httpx.TransportError as exc:
            last_error = exc
        if attempt < MAX_RETRIES:
            delay = _retry_delay(attempt, response)
            logger.warning(
                "ONS request failed; retrying in %.1fs (%d/%d)", delay, attempt + 1, MAX_RETRIES
            )
            time.sleep(delay)
    assert last_error is not None
    raise last_error


def _excel_frame(blob: bytes, sheet_name: str) -> pd.DataFrame:
    try:
        return pd.read_excel(
            io.BytesIO(blob), sheet_name=sheet_name, header=None, engine="openpyxl"
        )
    except ValueError as exc:
        raise ValueError(f"ONS workbook has no sheet {sheet_name!r}: {exc}") from exc


def _cell_text(value: object) -> str | None:
    if any(value is missing for missing in _MISSING):
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text or None


def _cell_float(value: object) -> float | None:
    if any(value is missing for missing in _MISSING):
        return None
    if isinstance(value, str):
        value = value.strip()
    if not isinstance(value, (SupportsFloat, SupportsIndex, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _cell_month(value: object) -> date | None:
    if not isinstance(value, (str, date, datetime)):
        return None
    try:
        return pd.Timestamp(value).date().replace(day=1)
    except (TypeError, ValueError):
        return None


def _publication_date(blob: bytes) -> date | None:
    contents = _excel_frame(blob, "Contents")
    for value in contents.iloc[:, 0].dropna().astype(str):
        match = re.search(r"Publication date:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})", value)
        if match:
            parsed = time.strptime(match.group(1), "%d %B %Y")
            return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
    return None


def parse_cpi_workbook(
    blob: bytes, start_date: date | None = None
) -> dict[date, dict[str, float | None]]:
    global _LAST_PUBLISH_DATE
    frame = _excel_frame(blob, TABLE38_SHEET)
    columns: dict[int, str] = {}
    catalog: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    for column in range(TABLE38_FIRST_SERIES_COLUMN, len(frame.columns)):
        native = _cell_text(frame.iat[TABLE38_CDID_ROW, column])
        if native is None or native.upper() not in TARGET_CDIDS:
            continue
        native = native.upper()
        if native in seen:
            raise ValueError(f"Table 38 publishes duplicate required CDID {native}")
        seen.add(native)
        series_id = make_series_id(native)
        name = _cell_text(frame.iat[TABLE38_NAME_ROW, column])
        if name is None:
            raise ValueError(f"Table 38 has no title for required CDID {native}")
        columns[column] = series_id
        catalog[series_id] = {
            "family": "INDEX",
            "node": "NATIVE",
            "level": "special_aggregate",
            "native_id": native,
            "name": name,
            "classification": _cell_text(frame.iat[TABLE38_CODE_ROW, column]) or "",
            "dataset": TABLE38_DATASET,
            "source_url": SOURCE_URL,
            "provenance": TABLE38_PROVENANCE,
            "parent_series_id": "",
        }
    missing = sorted(TARGET_CDIDS - seen)
    if missing:
        raise ValueError(f"Table 38 is missing required EX-CPI CDIDs: {missing}")
    parsed: dict[date, dict[str, float | None]] = {}
    for row in range(TABLE38_FIRST_DATA_ROW, len(frame.index)):
        month = _cell_month(frame.iat[row, TABLE38_DATE_COLUMN])
        if month is None or (start_date and month < start_date.replace(day=1)):
            continue
        parsed[month] = {
            series_id: _cell_float(frame.iat[row, column]) for column, series_id in columns.items()
        }
    if not parsed:
        raise ValueError("Table 38 contained no EX-CPI observations")
    _SERIES_CATALOG.clear()
    _SERIES_CATALOG.update(catalog)
    _LAST_PUBLISH_DATE = _publication_date(blob)
    logger.info("Parsed 10 EX-CPI series across %d months", len(parsed))
    return parsed


def get_workbook_fingerprint() -> str | None:
    with build_client() as client:
        response = http_get(client, CPI_DOWNLOAD_URL, method="HEAD")
    return response.headers.get("etag")


def collect_raw_data(start_date: date | None = None) -> dict[date, dict[str, float | None]]:
    with build_client() as client:
        response = http_get(client, CPI_DOWNLOAD_URL)
    return parse_cpi_workbook(response.content, start_date)
