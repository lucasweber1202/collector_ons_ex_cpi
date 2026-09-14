"""Bounded, rate-limit aware, size-limited requests to the official ONS endpoint."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import pytest

from scripts import extract

URL = extract.CPI_DOWNLOAD_URL


def _response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    request = httpx.Request("GET", URL)
    return httpx.Response(status, request=request, headers=headers or {})


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_throttled_requests_wait_longer_than_transport_errors() -> None:
    """A one-second backoff only spends an attempt against a per-request limiter."""
    assert extract._retry_delay(0, _response(429)) >= extract.RATE_LIMIT_BACKOFF
    assert extract._retry_delay(0, _response(503)) == 1.0
    assert extract._retry_delay(0, None) == 1.0


def test_retry_after_is_honoured() -> None:
    assert extract._retry_delay(0, _response(429, {"retry-after": "45"})) == 45.0


def test_every_wait_stays_bounded() -> None:
    assert extract._retry_delay(9, _response(429, {"retry-after": "9999"})) == (
        extract.MAX_RETRY_DELAY
    )


def test_non_ons_urls_are_refused() -> None:
    with _client(lambda _request: _response(200)) as client:
        with pytest.raises(ValueError, match="Refusing non-ONS URL"):
            extract.http_get(client, "https://example.com/file.xlsx")


def test_retries_stop_and_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract.time, "sleep", lambda _seconds: None)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(429, request=request)

    with _client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError, match="retryable status 429"):
            extract.http_get(client, URL)
    assert len(attempts) == extract.MAX_RETRIES + 1


def test_a_successful_retry_returns_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract.time, "sleep", lambda _seconds: None)
    statuses = iter([503, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), request=request, content=b"workbook")

    with _client(handler) as client:
        response = extract.http_get(client, URL)
    assert (response.status_code, response.content) == (200, b"workbook")


def test_client_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A withdrawn monthly edition must surface at once, not after four tries."""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(404, request=request)

    with _client(handler) as client:
        with pytest.raises(httpx.HTTPStatusError):
            extract.http_get(client, URL)
    assert len(attempts) == 1


def test_headers_survive_but_transfer_encoding_does_not() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, headers={"etag": 'W/"abc"'}, content=b"body")

    with _client(handler) as client:
        response = extract.http_get(client, URL)
    assert response.headers["etag"] == 'W/"abc"'
    assert "content-encoding" not in response.headers
    assert response.text == "body"


def test_an_oversized_declared_length_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract, "MAX_DOWNLOAD_BYTES", 64)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, content=b"x" * 4096)

    with _client(handler) as client:
        with pytest.raises(ValueError, match="above the download limit"):
            extract.http_get(client, URL)


def test_an_oversized_stream_is_refused_before_it_is_fully_buffered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response that under-declares its length is still bounded while reading."""
    monkeypatch.setattr(extract, "MAX_DOWNLOAD_BYTES", 1024)
    produced: list[int] = []

    def chunks() -> Iterator[bytes]:
        for _ in range(1000):
            produced.append(1)
            yield b"y" * 512

    def streaming_handler(request: httpx.Request) -> httpx.Response:
        class _Stream(httpx.SyncByteStream):
            def __iter__(self) -> Iterator[bytes]:
                return chunks()

        return httpx.Response(200, request=request, stream=_Stream())

    with _client(streaming_handler) as client:
        with pytest.raises(ValueError, match="exceeded the 1024 byte limit"):
            extract.http_get(client, URL)
    assert len(produced) < 10
