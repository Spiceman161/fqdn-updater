from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AcmeCertificateStatus(BaseModel):
    """The operator-relevant fields returned by ``ip http ssl acme list``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: str
    is_expired: bool
    issued_at: str | None = None
    expires_at: str | None = None
    renewal_enabled: bool | None = None
    renewal_in_progress: bool | None = None
