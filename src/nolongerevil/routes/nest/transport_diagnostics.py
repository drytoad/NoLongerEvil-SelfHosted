"""Version-specific transport envelopes and focused control diagnostics."""

import json
from typing import Any

from nolongerevil.lib.logger import get_logger

logger = get_logger(__name__)

TARGET_FIELDS = (
    "target_temperature",
    "target_temperature_low",
    "target_temperature_high",
    "target_temperature_type",
    "target_change_pending",
)


def is_legacy_v3(path: str) -> bool:
    """Use the explicit transport version, never the thermostat generation."""
    return "/transport/v3/" in path


def legacy_json_dumps(body: Any) -> str:
    """The v3 hand-written bucket scanner does not skip separator whitespace."""
    return json.dumps(body, separators=(",", ":"))


def target_values(value: dict[str, Any]) -> dict[str, Any]:
    """Select control fields without logging unrelated device/user data."""
    return {key: value[key] for key in TARGET_FIELDS if key in value}


def log_transport_request(
    path: str, serial: str, body: dict[str, Any], objects: list[dict[str, Any]]
) -> None:
    """Record encoding evidence without credentials, session IDs or full values."""
    if isinstance(body.get("objects"), list):
        request_format = "objects-array"
    elif isinstance(body.get("keys"), list):
        request_format = "legacy-v3-keys"
    elif any(isinstance(value, dict) and "object_key" in value for value in body.values()):
        request_format = "named-descriptors"
    else:
        request_format = "nested-or-unrecognized"
    logger.debug(
        "Transport request: serial=%s path=%s request_format=%s chunked=%s "
        "body_keys=%s parsed_keys=%s targets=%s",
        serial,
        path,
        request_format,
        body.get("chunked", False),
        sorted(body),
        [obj.get("object_key") for obj in objects],
        [target_values(obj.get("value") or {}) for obj in objects],
    )


def transport_response_body(
    path: str, serial: str, objects: list[dict[str, Any]], *, reason: str
) -> dict[str, Any]:
    """Format v3 nested buckets or modern object descriptors.

    Firmware 4.0.6's nlCZUpdateParser reads $version/$timestamp from each
    nested bucket payload. Metadata-only PUT acknowledgements clear dirty
    state without echoing values that could overwrite local schedules.
    """
    response_format = "legacy-v3" if is_legacy_v3(path) else "modern-objects"
    logger.debug(
        "Transport response: serial=%s path=%s response_format=%s "
        "reason=%s object_keys=%s targets=%s",
        serial,
        path,
        response_format,
        reason,
        [obj.get("object_key") for obj in objects],
        [target_values(obj.get("value") or {}) for obj in objects],
    )
    if not is_legacy_v3(path):
        return {"objects": objects}
    result: dict[str, Any] = {}
    for obj in objects:
        bucket, identifier = obj["object_key"].split(".", 1)
        result.setdefault(bucket, {})[identifier] = {
            **(obj.get("value") or {}),
            "$version": obj["object_revision"],
            "$timestamp": obj["object_timestamp"],
        }
    return result
