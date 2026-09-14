"""Consumption-segment classification, hierarchy and layout regressions."""

from __future__ import annotations

import io
from datetime import date

import httpx
import pandas as pd
import pytest

from scripts import segments
from tests.conftest import catalog_entry

# A catalog shaped like the published Table 38 nodes the segments attach to.
CATALOG = dict(
    entry
    for entry in [
        catalog_entry(
            "COICOP", "ALL", "D7BT", "CPI ALL ITEMS", classification="0", level="all_items"
        ),
        catalog_entry("COICOP", "D01", "D7BU", "FOOD", classification="1", level="division"),
        catalog_entry("COICOP", "G011", "D7C8", "FOOD", classification="01.1", level="group"),
        catalog_entry("COICOP", "C0111", "D7D5", "BREAD & CEREALS", classification="01.1.1"),
        catalog_entry("COICOP", "D07", "D7C2", "TRANSPORT", classification="7", level="division"),
        catalog_entry("COICOP", "G071", "D7CO", "PURCHASE OF VEHICLES", classification="07.1"),
        catalog_entry("COICOP", "C0711", "D7E8", "NEW CARS", classification="07.1.1"),
        catalog_entry("COICOP", "C0711", "D7E9", "SECOND-HAND CARS", classification="07.1.1"),
        catalog_entry("COICOP", "G073", "D7CQ", "TRANSPORT SERVICES", classification="07.3"),
        catalog_entry(
            "COICOP", "C0732", "D7EG", "PASSENGER TRANSPORT BY ROAD", classification="07.3.2"
        ),
        catalog_entry("COICOP", "D10", "D7C5", "EDUCATION", classification="10", level="division"),
        catalog_entry("COICOP", "G055", "D7CM", "TOOLS AND EQUIPMENT", classification="05.5"),
        catalog_entry("COICOP", "D05", "D7BY", "FURNITURE", classification="5", level="division"),
    ]
)
WEIGHT_CODES = ["07.1.1A", "07.1.1B", "07.3.2/6", "10.1/2/5", "05.5", "07.1", "07.3"]
RESOLVER = segments.ParentResolver(CATALOG, WEIGHT_CODES)


@pytest.mark.parametrize(
    ("code", "depth", "expected"),
    [
        ("CP0111", 4, "01.1.1"),
        ("CP01113", 5, "01.1.1.3"),
        ("CP01117_01118", 5, "01.1.1.7"),
        ("10101", 4, "01.1.1"),
        ("1010103", 5, "01.1.1.3"),
        ("110101", 4, "11.1.1"),
        ("11010101", 5, "11.1.1.1"),
        ("0", 4, None),
        ("", 5, None),
        ("nan", 4, None),
    ],
)
def test_classification_codes_normalize_to_one_official_spelling(
    code: str, depth: int, expected: str | None
) -> None:
    assert segments.normalize_coicop(code, depth) == expected


def test_exact_class_resolves_to_its_published_index() -> None:
    assert RESOLVER.resolve("07.3.2", "07.3.2.1") == "CPI_COICOP_C0732_D7EG"


def test_component_of_a_merged_class_resolves_to_the_merged_index() -> None:
    """ONS publishes one index for 07.3.2/6; only the weights code says so."""
    assert RESOLVER.resolve("07.3.6", "07.3.6.2") == "CPI_COICOP_C0732_D7EG"


def test_class_without_a_published_index_falls_back_to_its_group() -> None:
    assert RESOLVER.resolve("05.5.1", "05.5.1.1") == "CPI_COICOP_G055_D7CM"


def test_class_without_group_or_class_index_falls_back_to_the_division() -> None:
    assert RESOLVER.resolve("10.2.0", "10.2.0.0") == "CPI_COICOP_D10_D7C5"


def test_split_class_is_ambiguous_without_the_subclass() -> None:
    """New and second-hand cars share class 07.1.1 and must not be guessed."""
    with pytest.raises(ValueError, match="Ambiguous"):
        RESOLVER.resolve("07.1.1", None)


@pytest.mark.parametrize(
    ("subclass", "expected"),
    [("07.1.1.1", "CPI_COICOP_C0711_D7E8"), ("07.1.1.2", "CPI_COICOP_C0711_D7E9")],
)
def test_reviewed_subclass_alias_separates_the_split_class(subclass: str, expected: str) -> None:
    assert RESOLVER.resolve("07.1.1", subclass) == expected


def test_unknown_classification_fails_instead_of_attaching_to_all_items() -> None:
    with pytest.raises(ValueError, match="Unresolved"):
        RESOLVER.resolve(None, None)


@pytest.mark.parametrize(
    ("month", "year"),
    [(date(2026, 1, 1), 2025), (date(2026, 2, 1), 2026), (date(2026, 12, 1), 2026)],
)
def test_chain_year_runs_february_to_january(month: date, year: int) -> None:
    assert segments.chain_year(month) == year


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        (date(2026, 3, 1), date(2026, 4, 1), True),
        (date(2025, 12, 1), date(2026, 1, 1), False),
        (date(2026, 1, 1), date(2026, 2, 1), False),
    ],
)
def test_january_breaks_the_segment_link(previous: date, current: date, expected: bool) -> None:
    """Measured on the published panel, both January links fail to reconcile."""
    assert segments.linkable(previous, current) is expected


_PAGE = """
<a href="/file?uri=/a/consumptionsegmentindicesjuly2026/upload-consumptionsegments202607.csv">x</a>
<a href="/file?uri=/a/consumptionsegmentindicesjune2026/upload-consumptionsegments202606.csv">x</a>
<a href="/file?uri=/a/cpi2025classificationframework/upload-2025cpiclassificationframework.csv">x</a>
<a href="/file?uri=/a/cpi2025classificationframeworkrevised/upload-2025revised.csv">x</a>
<a href="/file?uri=/a/cpi2026classificationframeworkrevised/upload2026.xlsx">x</a>
<a href="/file?uri=/a/cpih2026classificationframeworkrevised/upload2026cpih.xlsx">x</a>
"""


def test_editions_are_discovered_by_published_month() -> None:
    editions = segments.discover_segment_editions(_PAGE)
    assert set(editions) == {date(2026, 7, 1), date(2026, 6, 1)}
    assert editions[date(2026, 7, 1)].endswith("upload-consumptionsegments202607.csv")


def test_revised_classification_framework_wins() -> None:
    frameworks = segments.discover_framework_editions(_PAGE)
    assert frameworks[2025].endswith("upload-2025revised.csv")
    assert frameworks[2026].endswith("upload2026.xlsx")


_CLASSIFIED_HEADER = (
    "INDEX_DATE,COICOP4_ID,COICOP5_ID,RPI_SECTION,CS_ID,CS_DESC,"
    "RPI_INDEX,CPI_INDEX,CPI_WEIGHT,RPI_WEIGHT,CPIH_WEIGHT"
)
_LEGACY_HEADER = "INDEX_DATE,CS_ID,CS_DESC,RPI_INDEX,CPI_INDEX,CPI_WEIGHT,RPI_WEIGHT,CPIH_WEIGHT"


def _classified(stamp: str, rows: list[tuple[str, str, str, str, float, float]]) -> bytes:
    body = [_CLASSIFIED_HEADER]
    body += [
        f"{stamp},{c4},{c5},RP1,{cs},{desc},100.0,{index},{weight},1.0,1.0"
        for c4, c5, cs, desc, index, weight in rows
    ]
    body += [
        f"{stamp},CP0111,CP01113,RP1,PAD{index:04d},PADDING {index},100.0,100.0,3.2,1.0,1.0"
        for index in range(segments.SEGMENT_MIN_ROWS)
    ]
    return "\n".join(body).encode()


def _legacy(stamp: str, rows: list[tuple[str, str, float, float]]) -> bytes:
    body = [_LEGACY_HEADER]
    body += [
        f"{stamp},{cs},{desc},100.0,{index},{weight},1.0,1.0" for cs, desc, index, weight in rows
    ]
    body += [
        f"{stamp},PAD{index:04d},PADDING {index},100.0,100.0,3.2,1.0,1.0"
        for index in range(segments.SEGMENT_MIN_ROWS)
    ]
    return "\n".join(body).encode()


def test_index_without_a_weight_is_not_stored() -> None:
    """CPIH-only segments carry a CPI index with a zero CPI weight."""
    blob = _classified("202607", [("CP0421", "CP04210", "410116", "IMPUTED RENTS", 101.7, 0.0)])
    frame = segments.parse_segment_csv(blob, date(2026, 7, 1))
    assert "410116" not in set(frame["CS_ID"])


def test_weight_without_an_index_is_not_stored() -> None:
    blob = _classified("202607", []).replace(b"100.0,100.0,3.2", b"100.0,,3.2", 1)
    frame = segments.parse_segment_csv(blob, date(2026, 7, 1))
    assert len(frame) == segments.SEGMENT_MIN_ROWS - 1


def test_repeated_segment_in_one_month_is_rejected() -> None:
    blob = _classified(
        "202607",
        [
            ("CP0732", "CP07321", "620205", "BUS FARES", 101.0, 2.0),
            ("CP0732", "CP07321", "620205", "BUS FARES", 102.0, 2.0),
        ],
    )
    with pytest.raises(ValueError, match="repeats"):
        segments.parse_segment_csv(blob, date(2026, 7, 1))


def _framework(rows: list[tuple[str, str, str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["CS_ID", "COICOP4_ID", "COICOP5_ID", "START_DATE", "END_DATE"]
    )


def test_legacy_layout_is_classified_from_the_framework_of_its_year() -> None:
    """Editions before February 2026 carry no COICOP columns at all."""
    frame = segments.parse_segment_csv(
        _legacy("202512", [("620205", "BUS FARES", 101.0, 2.0)]), date(2025, 12, 1)
    )
    framework = _framework(
        [
            ("620205", "70302", "7030201", "200502", "999999"),
            ("PAD0000", "10101", "1010103", "200502", "999999"),
        ]
    )
    panel = segments.build_panel(
        {date(2025, 12, 1): frame[frame["CS_ID"].isin(["620205", "PAD0000"])]},
        {2025: framework},
        RESOLVER,
    )
    assert panel.observations[date(2025, 12, 1)]["CPI_CS_SEG_620205"] == 101.0
    assert panel.hierarchy[date(2025, 12, 1)]["CPI_COICOP_C0732_D7EG"] == ["CPI_CS_SEG_620205"]
    assert panel.catalog["CPI_CS_SEG_620205"]["name"] == "BUS FARES"
    assert panel.catalog["CPI_CS_SEG_620205"]["classification"] == "07.3.2.1"


def test_legacy_month_without_a_framework_row_fails_explicitly() -> None:
    frame = segments.parse_segment_csv(
        _legacy("202512", [("620205", "BUS FARES", 101.0, 2.0)]), date(2025, 12, 1)
    )
    framework = _framework([("PAD0000", "10101", "1010103", "200502", "999999")])
    with pytest.raises(ValueError, match="does not classify"):
        segments.build_panel({date(2025, 12, 1): frame}, {2025: framework}, RESOLVER)


def test_legacy_month_without_any_framework_fails_explicitly() -> None:
    frame = segments.parse_segment_csv(
        _legacy("202512", [("620205", "BUS FARES", 101.0, 2.0)]), date(2025, 12, 1)
    )
    with pytest.raises(ValueError, match="no CPI classification framework"):
        segments.build_panel({date(2025, 12, 1): frame}, {}, RESOLVER)


def test_framework_validity_is_read_at_the_month_being_classified() -> None:
    """A segment reclassified mid-year must use the row valid in that month."""
    frame = segments.parse_segment_csv(
        _legacy("202512", [("620205", "BUS FARES", 101.0, 2.0)]), date(2025, 12, 1)
    )
    framework = _framework(
        [
            ("620205", "70101", "7010101", "200502", "202511"),
            ("620205", "70302", "7030201", "202512", "999999"),
            ("PAD0000", "10101", "1010103", "200502", "999999"),
        ]
    )
    panel = segments.build_panel(
        {date(2025, 12, 1): frame[frame["CS_ID"].isin(["620205", "PAD0000"])]},
        {2025: framework},
        RESOLVER,
    )
    assert panel.catalog["CPI_CS_SEG_620205"]["classification"] == "07.3.2.1"


def test_conflicting_framework_rows_are_rejected() -> None:
    frame = segments.parse_segment_csv(
        _legacy("202512", [("620205", "BUS FARES", 101.0, 2.0)]), date(2025, 12, 1)
    )
    framework = _framework(
        [
            ("620205", "70101", "7010101", "200502", "999999"),
            ("620205", "70302", "7030201", "200502", "999999"),
            ("PAD0000", "10101", "1010103", "200502", "999999"),
        ]
    )
    with pytest.raises(ValueError, match="more than one classification"):
        segments.build_panel({date(2025, 12, 1): frame}, {2025: framework}, RESOLVER)


def test_classified_layout_needs_no_framework() -> None:
    frame = segments.parse_segment_csv(
        _classified("202607", [("CP0732", "CP07321", "620205", "BUS FARES", 101.0, 2.0)]),
        date(2026, 7, 1),
    )
    panel = segments.build_panel({date(2026, 7, 1): frame}, {}, RESOLVER)
    assert panel.official_weights[date(2026, 7, 1)]["CPI_CS_SEG_620205"] == 2.0
    assert panel.catalog["CPI_CS_SEG_620205"]["provenance"] == segments.SEGMENT_PROVENANCE


def test_framework_xlsx_and_csv_are_read_the_same_way() -> None:
    rows = [
        ["item_id", "cs_id", "coicop4_id", "coicop5_id", "start_date", "end_date"],
        ["210102", "CP0111301", "10101", "1010103", "200502", "999999"],
    ]
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, header=False, index=False)
    from_xlsx = segments._read_table(buffer.getvalue(), "framework.xlsx")
    from_csv = segments._read_table(
        b"item_id ,cs_id,coicop4_id   ,coicop5_id,start_date,end_date\n"
        b"210102,CP0111301,10101,1010103,200502,999999\n",
        "framework.csv",
    )
    assert list(from_xlsx.columns) == list(from_csv.columns)
    assert from_xlsx.iloc[0].tolist() == from_csv.iloc[0].tolist()


def _collect_with(
    monkeypatch: pytest.MonkeyPatch,
    page: str,
    files: dict[str, bytes],
    statuses: dict[str, int],
    start_date: date = date(2026, 1, 1),
) -> segments.SegmentPanel:
    """Drive collect_segments against an in-process ONS stand-in."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == segments.SEGMENTS_PAGE_URL:
            return httpx.Response(200, request=request, content=page.encode())
        for name, blob in files.items():
            if url.endswith(name):
                return httpx.Response(statuses.get(name, 200), request=request, content=blob)
        return httpx.Response(404, request=request)

    monkeypatch.setattr(
        segments, "build_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(segments, "DOWNLOAD_DELAY", 0)
    return segments.collect_segments(start_date, CATALOG, WEIGHT_CODES)


_TWO_MONTH_PAGE = """
<a href="/file?uri=/a/consumptionsegmentindicesjune2026/upload-consumptionsegments202606.csv">x</a>
<a href="/file?uri=/a/consumptionsegmentindicesjuly2026/upload-consumptionsegments202607.csv">x</a>
"""


def test_both_published_editions_are_collected(monkeypatch: pytest.MonkeyPatch) -> None:
    files = {
        "upload-consumptionsegments202606.csv": _classified(
            "202606", [("CP0732", "CP07321", "620205", "BUS FARES", 100.4, 2.0)]
        ),
        "upload-consumptionsegments202607.csv": _classified(
            "202607", [("CP0732", "CP07321", "620205", "BUS FARES", 101.0, 2.0)]
        ),
    }
    panel = _collect_with(monkeypatch, _TWO_MONTH_PAGE, files, {})
    assert sorted(panel.observations) == [date(2026, 6, 1), date(2026, 7, 1)]
    assert panel.observations[date(2026, 7, 1)]["CPI_CS_SEG_620205"] == 101.0


def test_a_withdrawn_edition_does_not_lose_the_other_months(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """One missing monthly file is a soft failure, not a lost segment layer."""
    files = {
        "upload-consumptionsegments202606.csv": b"",
        "upload-consumptionsegments202607.csv": _classified(
            "202607", [("CP0732", "CP07321", "620205", "BUS FARES", 101.0, 2.0)]
        ),
    }
    with caplog.at_level("WARNING", logger="scripts.segments"):
        panel = _collect_with(
            monkeypatch, _TWO_MONTH_PAGE, files, {"upload-consumptionsegments202606.csv": 404}
        )
    assert sorted(panel.observations) == [date(2026, 7, 1)]
    assert any("has no file for" in record.getMessage() for record in caplog.records)


def test_layout_drift_is_never_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A file that arrives but no longer matches the layout must stop the run."""
    files = {
        "upload-consumptionsegments202606.csv": b"INDEX_DATE,SOMETHING_ELSE\n202606,1\n",
        "upload-consumptionsegments202607.csv": _classified("202607", []),
    }
    with pytest.raises(ValueError, match="missing columns"):
        _collect_with(monkeypatch, _TWO_MONTH_PAGE, files, {})


def test_a_window_after_the_last_edition_collects_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, content=_TWO_MONTH_PAGE.encode())

    monkeypatch.setattr(
        segments, "build_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
    )
    panel = segments.collect_segments(date(2027, 1, 1), CATALOG, WEIGHT_CODES)
    assert panel == segments.SegmentPanel({}, {}, {}, {})


def test_a_legacy_month_without_its_framework_stops_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = (
        '<a href="/file?uri=/a/consumptionsegmentindicesdecember2025/'
        'upload-consumptionsegments202512.csv">x</a>'
    )
    files = {
        "upload-consumptionsegments202512.csv": _legacy(
            "202512", [("620205", "BUS FARES", 101.0, 2.0)]
        )
    }
    monkeypatch.setattr(segments, "DOWNLOAD_DELAY", 0)
    with pytest.raises(ValueError, match="no CPI classification framework for 2025"):
        _collect_with(monkeypatch, page, files, {}, start_date=date(2025, 1, 1))
