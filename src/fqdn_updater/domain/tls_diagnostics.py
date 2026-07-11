from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TlsEndpointDiagnostic(BaseModel):
    """Result of one direct TLS handshake made with the configured SNI name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    address: str
    family: str
    port: int = Field(ge=1, le=65535)
    subject: str | None = None
    issuer: str | None = None
    not_before: str | None = None
    not_after: str | None = None
    subject_alt_names: tuple[str, ...] = Field(default_factory=tuple)
    san_matches_hostname: bool | None = None
    error: str | None = None


class TlsSanDiagnostic(BaseModel):
    """Safe, password-free diagnostic of certificates served for an RCI hostname."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hostname: str
    port: int = Field(ge=1, le=65535)
    endpoints: tuple[TlsEndpointDiagnostic, ...] = Field(default_factory=tuple)
    resolution_error: str | None = None

    @property
    def is_complete(self) -> bool:
        return (
            self.resolution_error is None
            and bool(self.endpoints)
            and all(endpoint.error is None for endpoint in self.endpoints)
        )

    @property
    def san_matches_hostname(self) -> bool:
        return self.is_complete and all(
            endpoint.san_matches_hostname is True for endpoint in self.endpoints
        )

    @property
    def is_healthy(self) -> bool:
        return self.is_complete and self.san_matches_hostname

    def compact_summary(self) -> str:
        """Return a log-safe one-line summary; it never contains credentials."""
        endpoint_summary = (
            ",".join(
                f"{endpoint.family}/{endpoint.address}:{endpoint.port}:"
                f"{'ok' if endpoint.error is None else 'error'}:"
                f"san_match={endpoint.san_matches_hostname}"
                for endpoint in self.endpoints
            )
            or "none"
        )
        return (
            f"tls_san hostname={self.hostname} complete={self.is_complete} "
            f"san_matches={self.san_matches_hostname} "
            f"resolution_error={self.resolution_error or '-'} "
            f"endpoints={endpoint_summary}"
        )
