"""Download and parse official ONS UK CPI index levels and basket weights.

Two ONS products are parsed here:

* the CPI detailed reference tables, sheet ``Table 38``, which publishes the
  COICOP index levels (2015=100) and the ONS analytical aggregates;
* Annex A table ``W1-CPI``, which publishes the official basket weights in
  parts per 1,000 for every classified node down to COICOP subclass.

Consumption-segment indices live in a different ONS dataset with a different
statistical status and are handled in ``scripts/segments.py``.
"""

from __future__ import annotations

import html
import io
import logging
import math
import re
import time
from datetime import date, datetime
from typing import SupportsFloat, SupportsIndex
from urllib.parse import urljoin, urlparse

import httpx
import pandas as pd

from scripts.config import (
    BACKOFF_FACTOR,
    DOWNLOAD_DELAY,
    MAX_DOWNLOAD_BYTES,
    MAX_RETRIES,
    MAX_RETRY_DELAY,
    RATE_LIMIT_BACKOFF,
    REQUEST_TIMEOUT,
    USER_AGENT,
)

logger = logging.getLogger(__name__)

SOURCE_NAME = "Office for National Statistics"
RELEASE_NAME = "Consumer Prices Index"
COUNTRY_CURRENCY = "GBP"
SOURCE_URL = (
    "https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/consumerpriceinflation"
)
WEIGHTS_PAGE_URL = (
    "https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/"
    "consumerpriceinflationupdatingweightsannexatablesw1tow3"
)
CPI_DOWNLOAD_URL = (
    "https://www.ons.gov.uk/file?uri=%2Feconomy%2Finflationandpriceindices%2Fdatasets%2F"
    "consumerpriceinflation%2Fcurrent%2Fconsumerpriceinflationdetailedreferencetables.xlsx"
)

# Table 38 carries an explicit ONS banner: the three-decimal detailed indices are
# published for analysis and are not accredited official statistics. The banner
# is stored with every series so the statistical status never has to be inferred.
TABLE38_DATASET = "ONS consumer price inflation detailed reference tables, Table 38"
TABLE38_PROVENANCE = (
    "ONS states that Table 38 figures are not accredited official statistics and "
    "should be used for analytical purposes only"
)
W1_DATASET = "ONS consumer price inflation updating weights, Annex A table W1-CPI"

# Explicit W1 combined/split classifications verified against Table 38 CDIDs.
# ONS publishes one index for a merged class; only the workbook code states which
# component classes the merged index covers.
W1_ALIASES = {
    "05.3.1/2": "D7E3",
    "06.1.2/3": "D7F8",
    "06.2.1/3": "D7FA",
    "07.1.1A": "D7E8",
    "07.1.1B": "D7E9",
    "07.1.2/3": "D7EA",
    "07.3.2/6": "D7EG",
    "08.2/3": "D7EM",
    "09.2.1/2/3": "D7FD",
    "09.3.4/5": "D7EU",
    "09.5.3/4": "D7FM",
    "12.1.2/3": "D7EZ",
    "12.5.3/5": "D7EQ",
}

# Table 38 layout invariants. Positions are only trusted after these pass.
TABLE38_SHEET = "Table 38"
TABLE38_CODE_ROW = 4
TABLE38_CDID_ROW = 5
TABLE38_NAME_ROW = 6
TABLE38_FIRST_DATA_ROW = 7
TABLE38_DATE_COLUMN = 1
TABLE38_FIRST_SERIES_COLUMN = 2
TABLE38_ROW_LABELS = {
    TABLE38_CODE_ROW: "aggregate number",
    TABLE38_CDID_ROW: "cdid",
    TABLE38_NAME_ROW: "name",
}
# A basket revision may legitimately add or drop series, so no exact count is
# enforced. These anchors are the published headline and structural cuts; losing
# any of them means the sheet layout moved, not that ONS changed the basket.
TABLE38_REQUIRED_CDIDS = frozenset({"D7BT", "D7BU", "D7C2", "D7C7", "D7F4", "D7F5"})
TABLE38_MIN_SERIES = 120
TABLE38_MIN_MONTHS = 120

WEIGHTS_SHEET = "W1-CPI"
WEIGHTS_HEADER_ROW = 4
WEIGHTS_FIRST_DATA_ROW = 5
WEIGHTS_CODE_COLUMN = 2
WEIGHTS_CDID_COLUMN = 1
WEIGHTS_FIRST_VALUE_COLUMN = 3
WEIGHTS_MIN_ROWS = 250
WEIGHTS_MIN_REGIMES = 10
WEIGHTS_TOTAL = 1000.0
WEIGHTS_TOTAL_TOLERANCE = 1.0

ECO_GROUPS = frozenset({"consumer_prices"})
UNITS = frozenset({"index"})
FREQUENCIES = frozenset({"monthly"})
ALLOWED_HOSTS = frozenset({"www.ons.gov.uk", "ons.gov.uk"})
FAMILIES = frozenset({"COICOP", "ALT", "CS"})

_SERIES_CATALOG: dict[str, dict[str, str]] = {}
_ORIGINAL_WEIGHTS: dict[date, dict[str, float]] = {}
_ORIGINAL_WEIGHT_CATALOG: dict[str, dict[str, str]] = {}
_LAST_PUBLISH_DATE: date | None = None


def get_series_catalog() -> dict[str, dict[str, str]]:
    """Return a defensive copy of source metadata discovered during extraction."""
    return {series_id: fields.copy() for series_id, fields in _SERIES_CATALOG.items()}


def register_series(series_id: str, fields: dict[str, str]) -> None:
    """Record verified upstream descriptive fields for one series.

    ``scripts/segments.py`` publishes its own catalog rows through this call so
    that metadata always reads official upstream fields rather than re-deriving
    a name from the identifier.
    """
    existing = _SERIES_CATALOG.get(series_id)
    if existing is not None and existing != fields:
        raise ValueError(f"Conflicting catalog entries for {series_id}")
    _SERIES_CATALOG[series_id] = dict(fields)


def get_original_weights() -> dict[date, dict[str, float]]:
    """Return source-published weights, including weight-only subclasses."""
    return {month: dict(values) for month, values in _ORIGINAL_WEIGHTS.items()}


def get_original_weight_catalog() -> dict[str, dict[str, str]]:
    """Return original classification labels and explicit Table 38 mappings."""
    return {key: dict(value) for key, value in _ORIGINAL_WEIGHT_CATALOG.items()}


def register_original_weights(
    values: dict[date, dict[str, float]], catalog: dict[str, dict[str, str]]
) -> None:
    """Merge additional official untouched weights and their provenance rows."""
    for month, weights in values.items():
        target = _ORIGINAL_WEIGHTS.setdefault(month, {})
        for series_id, weight in weights.items():
            previous = target.get(series_id)
            if previous is not None and previous != weight:
                raise ValueError(f"Conflicting official weights for {series_id} at {month}")
            target[series_id] = weight
    for key, fields in catalog.items():
        existing = _ORIGINAL_WEIGHT_CATALOG.get(key)
        if existing is not None and existing != fields:
            raise ValueError(f"Conflicting original-weight catalog entries for {key}")
        _ORIGINAL_WEIGHT_CATALOG[key] = dict(fields)


def get_last_publish_date() -> date | None:
    """Return the release date parsed from the current ONS workbook."""
    return _LAST_PUBLISH_DATE


def _node_token(raw_code: object) -> tuple[str, str, str]:
    """Return ``(family, node, level)`` for one published ONS classification code."""
    value = str(raw_code).strip()
    if value in {"0", "0.0"}:
        return "COICOP", "ALL", "all_items"
    if value.lower().startswith("agg"):
        number = re.sub(r"\D", "", value)
        return "ALT", f"A{int(number):02d}", "analytical_aggregate"
    value = value.replace(".0", "") if re.fullmatch(r"\d+\.0", value) else value
    digits = re.sub(r"\D", "", value)
    depth = value.count(".") + 1
    if depth == 1:
        return "COICOP", f"D{int(digits):02d}", "division"
    if depth == 2:
        return "COICOP", f"G{digits.zfill(3)}", "group"
    return "COICOP", f"C{digits}", "class"


def make_series_id(family: str, node: str, native_id: str) -> str:
    """Build ``CPI_{family}_{node}_{native_id}`` from stable identifying fields only.

    The official name is deliberately absent. ONS revises series titles without
    revising the series, and a title-derived identifier would fork the stored
    history on a purely textual edit.
    """
    if family not in FAMILIES:
        raise ValueError(f"Unknown UK CPI series family: {family}")
    native = re.sub(r"[^A-Z0-9]+", "", str(native_id).strip().upper())
    node = str(node).strip().upper()
    if not native or not node:
        raise ValueError(f"Incomplete UK CPI series identity: {family} {node} {native_id}")
    series_id = f"CPI_{family}_{node}_{native}"
    if len(series_id) > 200:
        raise ValueError(f"series_id exceeds 200 characters: {series_id}")
    return series_id


def parse_series_id(series_id: str) -> tuple[str, str, str, str]:
    """Decode ``CPI_{family}_{node}_{native_id}`` into its four stable parts."""
    parts = series_id.split("_")
    if len(parts) != 4 or parts[0] != "CPI" or parts[1] not in FAMILIES:
        raise ValueError(f"Invalid UK CPI series_id: {series_id}")
    measure, family, node, native_id = parts
    if not node or not native_id:
        raise ValueError(f"Incomplete UK CPI series_id: {series_id}")
    return measure, family, node, native_id


def build_client() -> httpx.Client:
    """Build the single managed HTTP client used by a collection call.

    Public inside this collector: ``scripts/segments.py`` collects from a second
    ONS dataset through the same client, allowlist and retry policy.
    """
    return httpx.Client(
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        trust_env=True,
        follow_redirects=False,
    )


RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
RATE_LIMITED_STATUS = 429
# Headers that describe the wire body rather than the decoded one. They must not
# travel with the decoded bytes, or the body would look doubly encoded.
_TRANSFER_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})


def _retry_delay(attempt: int, response: httpx.Response | None) -> float:
    """Return the bounded wait before the next attempt.

    A throttled ONS file request needs a much longer pause than a transient
    transport error: the published rate limit is enforced per request, not per
    connection, so the ordinary one-second backoff simply spends another
    attempt. ``Retry-After`` is honoured when the source sends it.
    """
    delay = float(BACKOFF_FACTOR**attempt)
    if response is not None and response.status_code == RATE_LIMITED_STATUS:
        delay = max(delay, RATE_LIMIT_BACKOFF * (attempt + 1))
        header = response.headers.get("retry-after", "").strip()
        if header.isdigit():
            delay = max(delay, float(header))
    return min(delay, MAX_RETRY_DELAY)


def _bounded_body(response: httpx.Response, url: str) -> bytes:
    """Read a streamed body, refusing an implausibly large ONS download.

    The largest published artifact is the detailed reference workbook at a few
    megabytes. Reading in chunks keeps a mis-sized or over-compressed response
    from being materialized in full before the size is known.
    """
    declared = response.headers.get("content-length", "").strip()
    if declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"ONS declared {declared} bytes for {url}, above the download limit")
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"ONS response for {url} exceeded the {MAX_DOWNLOAD_BYTES} byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def http_get(client: httpx.Client, url: str, method: str = "GET") -> httpx.Response:
    """Request an allowlisted ONS URL with bounded exponential-backoff retries.

    Every ONS request in this collector goes through here, so the host
    allowlist, redirect policy, retry budget and download ceiling are enforced
    in exactly one place.
    """
    if urlparse(url).hostname not in ALLOWED_HOSTS:
        raise ValueError(f"Refusing non-ONS URL: {url}")
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        response = None
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
            response = None
            last_error = exc
        if attempt < MAX_RETRIES:
            delay = _retry_delay(attempt, response)
            logger.warning(
                "ONS request failed; retrying in %.1fs (%d/%d)", delay, attempt + 1, MAX_RETRIES
            )
            time.sleep(delay)
    assert last_error is not None
    raise last_error


def _latest_weights_url(page_html: str) -> str:
    """Pick the highest published W1-W3 weights year, never the first HTML link.

    ONS currently lists the newest edition first, but nothing in the page
    contract guarantees that. Selecting on the year embedded in the official
    file name keeps the choice deterministic, and an unrecognised page fails
    loudly instead of silently ingesting a stale basket.
    """
    matches = re.findall(
        r'href="([^"]*annexa[^"?]*w1[^"?]*w3weights(20\d{2})[^"?]*\.xlsx[^"]*)"',
        page_html,
        re.IGNORECASE,
    )
    if not matches:
        raise ValueError(
            "ONS weights page contains no annexaw1w3weights<year>.xlsx link; "
            "the publication layout changed and the workbook must be re-verified"
        )
    best_href, best_year = max(matches, key=lambda item: (int(item[1]), item[0]))
    logger.info("Selected ONS W1 weights edition %s of %d published", best_year, len(matches))
    return urljoin(WEIGHTS_PAGE_URL, html.unescape(best_href))


# The spellings pandas uses for an empty cell. str() would turn each of them
# into a plausible-looking label ("NaT", "<NA>"), so they are matched by identity.
_MISSING = (None, pd.NaT, pd.NA)


def cell_float(value: object) -> float | None:
    """Return a finite float for one workbook cell, or None when it is not numeric.

    A workbook cell is whatever the source put there: a number, the ONS ".."
    not-available marker, a footnote, or an empty cell. Converting in one place
    keeps "not a usable number" a single, testable decision instead of a
    try/except repeated at every read site.
    """
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


def cell_text(value: object) -> str | None:
    """Return the trimmed text of a workbook cell, or None when it is empty."""
    if any(value is missing for missing in _MISSING):
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip()
    return text or None


def cell_month(value: object) -> date | None:
    """Return the first day of the month a date cell refers to, or None."""
    if not isinstance(value, (str, date, datetime)):
        return None
    try:
        return pd.Timestamp(value).date().replace(day=1)
    except (TypeError, ValueError):
        return None


def _excel_frame(blob: bytes, sheet_name: str) -> pd.DataFrame:
    """Read one workbook sheet without treating source rows as headers."""
    try:
        return pd.read_excel(
            io.BytesIO(blob), sheet_name=sheet_name, header=None, engine="openpyxl"
        )
    except ValueError as exc:
        raise ValueError(f"ONS workbook has no sheet {sheet_name!r}: {exc}") from exc


def _publication_date(blob: bytes) -> date | None:
    """Parse the source-stamped publication date from the contents sheet."""
    contents = _excel_frame(blob, "Contents")
    for value in contents.iloc[:, 0].dropna().astype(str):
        match = re.search(r"Publication date:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})", value)
        if match:
            parsed = time.strptime(match.group(1), "%d %B %Y")
            return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
    return None


def _assert_table38_layout(frame: pd.DataFrame, cdids: set[str], months: int) -> None:
    """Fail before persistence when the Table 38 layout moved under us."""
    for row, label in TABLE38_ROW_LABELS.items():
        found = str(frame.iat[row, TABLE38_DATE_COLUMN]).strip().lower()
        if found != label:
            raise ValueError(
                f"Table 38 row {row} column {TABLE38_DATE_COLUMN} should label {label!r}, "
                f"found {found!r}"
            )
    missing = sorted(TABLE38_REQUIRED_CDIDS - cdids)
    if missing:
        raise ValueError(f"Table 38 no longer publishes required CDIDs: {missing}")
    if len(cdids) < TABLE38_MIN_SERIES:
        raise ValueError(
            f"Table 38 collapsed to {len(cdids)} series, below the {TABLE38_MIN_SERIES} floor"
        )
    if months < TABLE38_MIN_MONTHS:
        raise ValueError(f"Table 38 returned {months} months, below the {TABLE38_MIN_MONTHS} floor")


def _parent_node(node: str) -> str | None:
    """Return the immediate parent token in the published COICOP hierarchy."""
    if node == "ALL":
        return None
    if node.startswith("D"):
        return "ALL"
    if node.startswith("G"):
        return f"D{node[1:3]}"
    if node.startswith("C"):
        return f"G{node[1:4]}"
    return None


def _link_coicop_parents(catalog: dict[str, dict[str, str]]) -> None:
    """Resolve each COICOP node's immediate parent series, failing on ambiguity."""
    by_node: dict[str, list[str]] = {}
    for series_id, fields in catalog.items():
        if fields["family"] == "COICOP":
            by_node.setdefault(fields["node"], []).append(series_id)
    for series_id, fields in catalog.items():
        if fields["family"] != "COICOP":
            fields["parent_series_id"] = ""
            continue
        parent_node = _parent_node(fields["node"])
        if parent_node is None:
            fields["parent_series_id"] = ""
            continue
        parents = by_node.get(parent_node, [])
        if len(parents) != 1:
            raise ValueError(
                f"Cannot resolve unique parent {parent_node} for {series_id}: {sorted(parents)}"
            )
        fields["parent_series_id"] = parents[0]


def parse_cpi_workbook(
    blob: bytes, start_date: date | None = None
) -> dict[date, dict[str, float | None]]:
    """Parse all ONS CPI detailed index levels from workbook Table 38."""
    global _LAST_PUBLISH_DATE
    frame = _excel_frame(blob, TABLE38_SHEET)
    catalog: dict[str, dict[str, str]] = {}
    column_ids: dict[int, str] = {}
    cdids: set[str] = set()
    for column in range(TABLE38_FIRST_SERIES_COLUMN, len(frame.columns)):
        raw_code = cell_text(frame.iat[TABLE38_CODE_ROW, column])
        native = cell_text(frame.iat[TABLE38_CDID_ROW, column])
        name = cell_text(frame.iat[TABLE38_NAME_ROW, column])
        if raw_code is None or native is None or name is None:
            continue
        family, node, level = _node_token(raw_code)
        native_id = native.upper()
        series_id = make_series_id(family, node, native_id)
        if series_id in catalog:
            raise ValueError(f"Table 38 publishes {series_id} in two columns")
        column_ids[column] = series_id
        cdids.add(native_id)
        catalog[series_id] = {
            "family": family,
            "node": node,
            "level": level,
            "native_id": native_id,
            "name": name,
            "classification": raw_code,
            "dataset": TABLE38_DATASET,
            "source_url": SOURCE_URL,
            "provenance": TABLE38_PROVENANCE,
            "parent_series_id": "",
        }

    parsed: dict[date, dict[str, float | None]] = {}
    months = 0
    for row in range(TABLE38_FIRST_DATA_ROW, len(frame.index)):
        ref_date = cell_month(frame.iat[row, TABLE38_DATE_COLUMN])
        if ref_date is None:
            continue
        months += 1
        if start_date and ref_date < start_date.replace(day=1):
            continue
        values: dict[str, float | None] = {
            series_id: cell_float(frame.iat[row, column])
            for column, series_id in column_ids.items()
        }
        if values:
            parsed[ref_date] = values

    _assert_table38_layout(frame, cdids, months)
    if not parsed:
        raise ValueError("Table 38 contained no usable CPI observations")
    _link_coicop_parents(catalog)
    _SERIES_CATALOG.clear()
    _SERIES_CATALOG.update(catalog)
    _ORIGINAL_WEIGHTS.clear()
    _ORIGINAL_WEIGHT_CATALOG.clear()
    _LAST_PUBLISH_DATE = _publication_date(blob)
    logger.info(
        "Parsed %d CPI series across %d months (%s to %s)",
        len(catalog),
        len(parsed),
        min(parsed),
        max(parsed),
    )
    return parsed


def _weight_header(raw: object) -> tuple[int, tuple[int, ...]] | None:
    """Decode a W1 year/regime header into the months in which it applies."""
    if isinstance(raw, (int, float)) and not pd.isna(raw):
        year = int(raw)
        return year, tuple(range(1, 13))
    text = str(raw).replace("\n", " ").strip()
    match = re.search(r"(20\d{2})", text)
    if not match:
        return None
    year = int(match.group(1))
    return (year, (1,)) if "Jan" in text and "Feb" not in text else (year, tuple(range(2, 13)))


def _weight_code_name(raw_name: object) -> tuple[str, str] | None:
    """Split a W1 label into a classification code and official name."""
    text = str(raw_name).strip()
    match = re.match(r"^(\d{1,2}(?:\.\d+(?:/\d+)*)*(?:[AB])?)\s+(.+)$", text)
    if match:
        code = match.group(1)
        return code, match.group(2).strip()
    if text.lower() in {"all goods", "all services"}:
        return text.upper().replace(" ", ""), text
    if "overall index" in text.lower():
        return "0", text
    return None


def expand_classification_code(code: str) -> list[str]:
    """Expand an ONS combined or split classification code into its components.

    ``07.3.2/6`` covers classes ``07.3.2`` and ``07.3.6``; ``07.1.1A`` is one of
    two published splits of class ``07.1.1``.
    """
    if code and code[-1] in "AB":
        return [code[:-1]]
    if "/" not in code:
        return [code]
    head, _, tail = code.rpartition(".")
    if not head:
        return [code]
    return [f"{head}.{part}" for part in tail.split("/")]


def _match_weight_series(code: str, name: str, catalog: dict[str, dict[str, str]]) -> str | None:
    """Use exact classification or reviewed CDID aliases, never fuzzy matching."""
    if code.count(".") > 2 or code in {"ALLGOODS", "ALLSERVICES"}:
        return None
    family, node, _ = _node_token(code)
    native_id = W1_ALIASES.get(code)
    candidates = [
        series_id
        for series_id, fields in catalog.items()
        if fields["family"] == family
        and (fields["native_id"] == native_id if native_id else fields["node"] == node)
    ]
    if len(candidates) > 1:
        raise ValueError(f"Ambiguous W1 mapping: {code} {name}: {candidates}")
    if native_id and not candidates:
        raise ValueError(f"Reviewed W1 alias {code} requires missing CDID {native_id}")
    return candidates[0] if candidates else None


def _assert_weights_layout(
    frame: pd.DataFrame, headers: dict[int, tuple[int, tuple[int, ...]] | None], rows: int
) -> None:
    """Fail before persistence when the W1-CPI layout moved under us."""
    regimes = sum(1 for header in headers.values() if header is not None)
    if regimes < WEIGHTS_MIN_REGIMES:
        raise ValueError(
            f"W1-CPI exposes {regimes} weight regimes, below the {WEIGHTS_MIN_REGIMES} floor"
        )
    if rows < WEIGHTS_MIN_ROWS:
        raise ValueError(f"W1-CPI exposes {rows} classified rows, below {WEIGHTS_MIN_ROWS}")
    overall = [
        row
        for row in range(WEIGHTS_FIRST_DATA_ROW, len(frame.index))
        if "overall index" in str(frame.iat[row, WEIGHTS_CODE_COLUMN]).lower()
    ]
    if len(overall) != 1:
        raise ValueError(f"W1-CPI should carry exactly one overall-index row, found {overall}")


def parse_weights_workbook(
    blob: bytes,
    catalog: dict[str, dict[str, str]],
    start_date: date | None = None,
) -> dict[date, dict[str, float]]:
    """Parse official W1 CPI weights and expand each annual regime by month."""
    originals: dict[date, dict[str, float]] = {}
    original_catalog: dict[str, dict[str, str]] = {}
    frame = _excel_frame(blob, WEIGHTS_SHEET)
    headers = {
        column: _weight_header(frame.iat[WEIGHTS_HEADER_ROW, column])
        for column in range(WEIGHTS_FIRST_VALUE_COLUMN, len(frame.columns))
    }
    parsed: dict[date, dict[str, float]] = {}
    matched: set[str] = set()
    claimed_by: dict[str, tuple[str, str]] = {}
    unmatched = 0
    classified_rows = 0
    for row in range(WEIGHTS_FIRST_DATA_ROW, len(frame.index)):
        label = _weight_code_name(frame.iat[row, WEIGHTS_CODE_COLUMN])
        if label is None:
            continue
        classified_rows += 1
        series_id = _match_weight_series(label[0], label[1], catalog)
        original_id = "CPI_W1_" + label[0].replace(".", "P").replace("/", "S")
        raw_cdid = cell_text(frame.iat[row, WEIGHTS_CDID_COLUMN])
        original_catalog[original_id] = {
            "code": label[0],
            "name": label[1],
            "native_id": "" if raw_cdid is None else raw_cdid.upper(),
            "mapped_series_id": series_id or "",
            "dataset": W1_DATASET,
            "source_url": WEIGHTS_PAGE_URL,
        }
        if series_id is None:
            # An official weight we cannot attribute is a reconciliation gap, not
            # noise: report it instead of dropping the row silently.
            unmatched += 1
            logger.warning(
                "W1 row %d matched no Table 38 series: code=%s name=%s",
                row,
                label[0],
                label[1],
            )
        if series_id in claimed_by and claimed_by[series_id] != label:
            # Two official rows describing one series is a mapping the last
            # write would otherwise resolve silently.
            logger.warning(
                "W1 rows code=%s name=%s and code=%s name=%s both map to %s",
                *claimed_by[series_id],
                *label,
                series_id,
            )
        if series_id is not None:
            claimed_by[series_id] = label
            matched.add(series_id)
        for column, header in headers.items():
            if header is None:
                continue
            weight = cell_float(frame.iat[row, column])
            if weight is None:
                continue
            if weight < 0:
                raise ValueError(f"Negative W1 weight: {label}")
            year, months = header
            for month in months:
                ref_date = date(year, month, 1)
                if start_date and ref_date < start_date.replace(day=1):
                    continue
                original_month = originals.setdefault(ref_date, {})
                if original_month.get(original_id, weight) != weight:
                    raise ValueError(f"Conflicting W1 weights for {original_id} at {ref_date}")
                original_month[original_id] = weight
                if series_id is None:
                    continue
                previous = parsed.setdefault(ref_date, {}).get(series_id)
                if previous is not None and previous != weight:
                    # Duplicate rows agreeing on a value are harmless; disagreeing
                    # ones mean the stored weight depends on workbook row order.
                    raise ValueError(
                        f"Conflicting W1 weights for {series_id} at {ref_date}: "
                        f"{previous!r} then {weight!r} (row {row}, code={label[0]})"
                    )
                parsed[ref_date][series_id] = weight
    _assert_weights_layout(frame, headers, classified_rows)
    if not parsed:
        raise ValueError("W1-CPI contained no usable weights")
    _assert_weight_totals(originals)
    register_original_weights(originals, original_catalog)
    logger.info(
        "Parsed weights for %d/%d CPI series across %d months (%d W1 rows unmatched)",
        len(matched),
        len(catalog),
        len(parsed),
        unmatched,
    )
    return parsed


def _assert_weight_totals(originals: dict[date, dict[str, float]]) -> None:
    """The published overall-index row must stay at the documented 1,000 total."""
    for month, weights in sorted(originals.items()):
        total = weights.get("CPI_W1_0")
        if total is None:
            raise ValueError(f"W1 published no overall-index weight for {month}")
        if abs(total - WEIGHTS_TOTAL) > WEIGHTS_TOTAL_TOLERANCE:
            raise ValueError(f"W1 overall weight for {month} is {total}, not {WEIGHTS_TOTAL}")


def get_workbook_fingerprint() -> str | None:
    """Return the CPI workbook's current entity tag without downloading its body.

    Release polling compares this between attempts, so an unchanged workbook
    costs one header request instead of a full download and parse. Returns
    ``None`` when the source exposes no validator, which makes the caller fall
    back to downloading rather than risk missing a release.
    """
    with build_client() as client:
        response = http_get(client, CPI_DOWNLOAD_URL, method="HEAD")
    return response.headers.get("etag")


def collect_raw_data(start_date: date | None = None) -> dict[date, dict[str, float | None]]:
    """Download current official CPI workbook and return standardized observations."""
    with build_client() as client:
        response = http_get(client, CPI_DOWNLOAD_URL)
    return parse_cpi_workbook(response.content, start_date)


def collect_weights(start_date: date | None = None) -> dict[date, dict[str, float]]:
    """Discover and download the latest official W1-W3 weights workbook."""
    if not _SERIES_CATALOG:
        raise RuntimeError("collect_raw_data must run before collect_weights")
    with build_client() as client:
        page = http_get(client, WEIGHTS_PAGE_URL)
        weights_url = _latest_weights_url(page.text)
        if DOWNLOAD_DELAY:
            time.sleep(DOWNLOAD_DELAY)
        workbook = http_get(client, weights_url)
    return parse_weights_workbook(workbook.content, get_series_catalog(), start_date)
