from __future__ import annotations

from typing import Any


def iter_rci_status_errors(payload: Any) -> tuple[str, ...]:
    errors: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            status = value.get("status")
            if isinstance(status, list):
                for item in status:
                    if not isinstance(item, dict) or item.get("status") != "error":
                        continue
                    message = item.get("message")
                    code = item.get("code")
                    ident = item.get("ident")
                    parts = [
                        str(part)
                        for part in (ident, code, message)
                        if part is not None and str(part).strip()
                    ]
                    errors.append(" - ".join(parts) if parts else "RCI status error")
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return tuple(errors)
