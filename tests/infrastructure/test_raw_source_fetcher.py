from __future__ import annotations

from urllib import error

import pytest

from fqdn_updater.infrastructure.raw_source_fetcher import HttpRawSourceFetcher

_URLOPEN_PATH = "fqdn_updater.infrastructure.raw_source_fetcher.request.urlopen"


class _FakeHeaders:
    def __init__(self, charset: str = "utf-8") -> None:
        self._charset = charset

    def get_content_charset(self, default: str) -> str:
        return self._charset or default


class _FakeResponse:
    def __init__(self, payload: bytes, charset: str = "utf-8") -> None:
        self._payload = payload
        self.headers = _FakeHeaders(charset=charset)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_fetch_text_returns_decoded_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout: int):  # noqa: ANN001
        assert request.full_url == "https://example.com/source.lst"
        assert timeout == 30
        return _FakeResponse(b"example.com\n")

    monkeypatch.setattr(_URLOPEN_PATH, fake_urlopen)

    assert HttpRawSourceFetcher().fetch_text("https://example.com/source.lst") == "example.com\n"


def test_fetch_text_reports_network_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout: int):  # noqa: ANN001
        raise error.URLError("timeout")

    monkeypatch.setattr(_URLOPEN_PATH, fake_urlopen)

    with pytest.raises(RuntimeError, match="Failed to fetch source https://example.com/source.lst"):
        HttpRawSourceFetcher().fetch_text("https://example.com/source.lst")


def test_fetch_text_reports_decode_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout: int):  # noqa: ANN001
        return _FakeResponse(b"\xff", charset="utf-8")

    monkeypatch.setattr(_URLOPEN_PATH, fake_urlopen)

    with pytest.raises(
        RuntimeError, match="Failed to decode source https://example.com/source.lst"
    ):
        HttpRawSourceFetcher().fetch_text("https://example.com/source.lst")
