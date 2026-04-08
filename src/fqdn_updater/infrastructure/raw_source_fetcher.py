from __future__ import annotations

from urllib import error, request


class HttpRawSourceFetcher:
    def fetch_text(self, url: str) -> str:
        req = request.Request(url, headers={"User-Agent": "fqdn-updater/0.1.0"})
        try:
            with request.urlopen(req, timeout=30) as response:
                charset = response.headers.get_content_charset("utf-8")
                payload = response.read()
        except error.URLError as exc:
            raise RuntimeError(f"Failed to fetch source {url}: {exc.reason}") from exc

        try:
            return payload.decode(charset)
        except UnicodeDecodeError as exc:
            raise RuntimeError(
                f"Failed to decode source {url} with charset {charset}: {exc}"
            ) from exc
