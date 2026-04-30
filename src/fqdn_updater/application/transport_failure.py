from __future__ import annotations

from fqdn_updater.domain.run_artifact import FailureCategory


def classify_transport_failure(message: str) -> FailureCategory | None:
    normalized_message = message.lower()

    if (
        "name or service not known" in normalized_message
        or "temporary failure in name resolution" in normalized_message
    ):
        return FailureCategory.DNS_RESOLUTION_FAILED
    if (
        "hostname mismatch" in normalized_message
        or "certificate is not valid for" in normalized_message
    ):
        return FailureCategory.TLS_CERT_HOSTNAME_MISMATCH
    if "unable to get local issuer certificate" in normalized_message:
        return FailureCategory.TLS_CERT_CHAIN_UNTRUSTED
    if "handshake operation timed out" in normalized_message:
        return FailureCategory.TLS_HANDSHAKE_TIMEOUT
    if "connection reset by peer" in normalized_message:
        return FailureCategory.CONNECTION_RESET
    if "unexpected_eof_while_reading" in normalized_message:
        return FailureCategory.TLS_UNEXPECTED_EOF
    if (
        "certificate_verify_failed" in normalized_message
        or "certificate verify failed" in normalized_message
    ):
        return FailureCategory.TLS_CERT_VERIFY_FAILED
    if "transport failed after" in normalized_message:
        return FailureCategory.TRANSPORT_FAILED
    return None


def is_transport_failure(message: str) -> bool:
    return classify_transport_failure(message) is not None
