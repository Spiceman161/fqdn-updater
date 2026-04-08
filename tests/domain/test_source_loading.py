from __future__ import annotations

import pytest
from pydantic import ValidationError

from fqdn_updater.domain.source_loading import (
    NormalizedServiceSource,
    ServiceSourceFailure,
)


def test_normalized_service_source_sorts_and_deduplicates_entries() -> None:
    source = NormalizedServiceSource(
        service_key="telegram",
        entries=["b.example", "a.example", "b.example"],
    )

    assert source.entries == ("a.example", "b.example")


def test_service_source_failure_requires_non_blank_fields() -> None:
    with pytest.raises(ValidationError, match="message must not be empty"):
        ServiceSourceFailure(
            service_key="telegram",
            source_url="https://example.com/source.lst",
            message="   ",
        )
