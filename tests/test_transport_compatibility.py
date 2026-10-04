"""Gen-2 ingestion and verified v3/modern response compatibility."""

import asyncio
import json
from base64 import b64encode
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import web

from nolongerevil.lib.types import DeviceObject
from nolongerevil.routes.control.command import execute_command
from nolongerevil.routes.nest.transport import (
    format_object_for_response,
    handle_legacy_v3_subscribe,
    handle_transport_get,
    handle_transport_put,
    handle_transport_subscribe,
    parse_put_body,
    parse_subscribe_body,
)
from nolongerevil.routes.nest.transport_diagnostics import (
    log_transport_request,
    transport_response_body,
)

SERIAL = "02AA01AA00000001"  # Synthetic test identifier; not a real device.
KEY = f"shared.{SERIAL}"
AUTH = "Basic " + b64encode(f"{SERIAL}:test-only-password".encode()).decode()


async def test_v3_disconnected_poll_releases_subscription(state_service, subscription_manager):
    request = Mock(spec=web.Request)
    request.app = {"state_service": state_service, "subscription_manager": subscription_manager}
    request.transport = None
    response = await handle_legacy_v3_subscribe(request, SERIAL, "session", [])
    assert response.body == b""
    assert subscription_manager.get_subscription_count(SERIAL) == 0


async def test_v3_multiple_changed_buckets_are_delivered_on_successive_polls(
    state_service, subscription_manager
):
    second = f"device.{SERIAL}"
    for key in (KEY, second):
        await state_service.upsert_object(
            DeviceObject(SERIAL, key, 2, 456, {"test": key}, datetime.now())
        )
    request = Mock(spec=web.Request)
    request.app = {"state_service": state_service, "subscription_manager": subscription_manager}
    request.path = "/nest/transport/v3/subscribe"
    objects = [
        {"object_key": key, "object_revision": 1, "object_timestamp": 123} for key in (KEY, second)
    ]
    first = await handle_legacy_v3_subscribe(request, SERIAL, "session", objects)
    assert first.headers["X-nl-skv-key"] == KEY
    objects[0].update(object_revision=2, object_timestamp=456)
    following = await handle_legacy_v3_subscribe(request, SERIAL, "session", objects)
    assert following.headers["X-nl-skv-key"] == second
    assert json.loads(following.body) == {"test": second}


@pytest.mark.parametrize("encoding", ["objects-array", "bucket-keyed", "legacy-v3"])
async def test_put_encodings_ingest_state_without_value_echo(state_service, encoding):
    values = {"current_temperature": 20.5, "target_temperature": 21.5}
    if encoding == "objects-array":
        body = {"objects": [{"object_key": KEY, "value": values}]}
    elif encoding == "bucket-keyed":
        body = {KEY: {"object_key": KEY, **values}}
    else:
        body = {
            "device": {SERIAL: {"current_humidity": 45}},
            "shared": {SERIAL: values},
        }
    req = Mock(spec=web.Request)
    req.path = f"/nest/transport/{'v3' if encoding == 'legacy-v3' else 'v7'}/put"
    req.headers = {"Authorization": AUTH}
    req.json = AsyncMock(return_value=body)
    req.app = {"state_service": state_service}
    response = await handle_transport_put(req)
    assert response.status == 200
    assert state_service.get_object(SERIAL, KEY).value == values
    if encoding == "legacy-v3":
        assert state_service.get_object(SERIAL, f"device.{SERIAL}").value["current_humidity"] == 45
    payload = json.loads(response.body)
    if encoding == "legacy-v3":
        assert set(payload["shared"][SERIAL]) == {"$version", "$timestamp"}
        assert set(payload["device"][SERIAL]) == {"$version", "$timestamp"}
        assert b": " not in response.body
        assert b", " not in response.body
    else:
        for obj in payload["objects"]:
            assert set(obj) == {"object_revision", "object_timestamp", "object_key"}


def test_bucket_keyed_put_preserves_conditional_metadata():
    session, objects = parse_put_body(
        {
            "session": "session",
            KEY: {
                "object_key": KEY,
                "base_object_revision": 2,
                "if_object_revision": 3,
                "target_temperature": 22.0,
            },
        }
    )
    assert session == "session"
    assert objects == [
        {
            "object_key": KEY,
            "base_object_revision": 2,
            "if_object_revision": 3,
            "value": {"target_temperature": 22.0},
        }
    ]


def test_legacy_put_ignores_non_bucket_and_non_object_entries():
    _, objects = parse_put_body(
        {
            "session": "session",
            "unknown": {SERIAL: {"untrusted": True}},
            "shared": {SERIAL: {"target_temperature": 21.5}, "invalid": 1},
        }
    )
    assert objects == [{"object_key": KEY, "value": {"target_temperature": 21.5}}]


@pytest.mark.parametrize("version", ["v3", "v7"])
def test_response_format_matches_explicit_transport_version(version):
    obj = DeviceObject(SERIAL, KEY, 3, 123, {"target_temperature": 22.0}, datetime.now())
    objects = [format_object_for_response(obj)]
    with patch("nolongerevil.routes.nest.transport_diagnostics.logger") as logger:
        result = transport_response_body(
            f"/nest/transport/{version}/subscribe", SERIAL, objects, reason="queued-update"
        )
    if version == "v3":
        assert result == {
            "shared": {SERIAL: {"$version": 3, "$timestamp": 123, "target_temperature": 22.0}}
        }
    else:
        assert result == {"objects": objects}
        assert result["objects"][0]["value"]["target_temperature"] == 22.0
    assert list(objects[0])[:3] == ["object_revision", "object_timestamp", "object_key"]
    assert ("legacy-v3" if version == "v3" else "modern-objects") in logger.debug.call_args.args


def test_request_diagnostics_identify_nested_shape_without_logging_secrets():
    body = {"session": "SECRET", "shared": {SERIAL: {"target_temperature": 22.0}}}
    _, objects = parse_put_body(body)
    with patch("nolongerevil.routes.nest.transport_diagnostics.logger") as logger:
        log_transport_request("/nest/transport/v3/put", SERIAL, body, objects)
    args = logger.debug.call_args.args
    assert "nested-or-unrecognized" in args
    assert "SECRET" not in str(args)
    assert [{"target_temperature": 22.0}] in args


async def test_no_subscriber_command_and_legacy_overwrite_are_visible(
    state_service, subscription_manager
):
    await state_service.upsert_object(
        DeviceObject(SERIAL, KEY, 1, 123, {"target_temperature": 21.0}, datetime.now())
    )
    with patch("nolongerevil.routes.control.command.logger") as command_logger:
        await execute_command(state_service, subscription_manager, SERIAL, "set_temperature", 22.0)
    assert "subscribers_notified=%s" in command_logger.debug.call_args.args[0]
    assert command_logger.debug.call_args.args[-1] == 0
    req = Mock(spec=web.Request)
    req.path = "/nest/transport/v3/put"
    req.headers = {"Authorization": AUTH}
    req.json = AsyncMock(
        return_value={
            "shared": {SERIAL: {"target_temperature": 21.0, "target_change_pending": False}}
        }
    )
    req.app = {"state_service": state_service}
    with patch("nolongerevil.routes.nest.transport.logger") as transport_logger:
        await handle_transport_put(req)
    changes = next(
        call.args[-1]
        for call in transport_logger.debug.call_args_list
        if "Transport PUT while target change pending" in call.args[0]
    )
    assert changes["target_temperature"] == {"stored": 22.0, "incoming": 21.0}
    # Instrumentation observes the overwrite without changing merge semantics.
    assert state_service.get_object(SERIAL, KEY).value["target_temperature"] == 21.0


@pytest.mark.parametrize("version", ["v7"])
async def test_temperature_command_reaches_open_subscribe(
    state_service, subscription_manager, aiohttp_client, version
):
    await state_service.upsert_object(
        DeviceObject(SERIAL, KEY, 1, 123, {"target_temperature": 21.0}, datetime.now())
    )
    app = web.Application()
    app["state_service"] = state_service
    app["subscription_manager"] = subscription_manager
    # No storage entry: avoid unrelated pairing bucket injection.
    app.router.add_post("/nest/transport/{version}/subscribe", handle_transport_subscribe)
    client = await aiohttp_client(app)
    response = await client.post(
        f"/nest/transport/{version}/subscribe",
        headers={"Authorization": AUTH},
        json={
            "chunked": True,
            "session": "session",
            "objects": [{"object_key": KEY, "object_revision": 1, "object_timestamp": 123}],
        },
    )
    try:
        async with asyncio.timeout(2):
            while subscription_manager.get_subscription_count(SERIAL) == 0:
                await asyncio.sleep(0.01)
        await execute_command(state_service, subscription_manager, SERIAL, "set_temperature", 22.0)
        async with asyncio.timeout(5):
            payload = await response.json()
        assert response.headers["Transfer-Encoding"] == "chunked"
        update = payload["objects"][0]
        assert update["object_key"] == KEY
        assert update["value"]["target_temperature"] == 22.0
        assert update["value"]["target_change_pending"] is True
        assert update["object_revision"] == 2
    finally:
        response.close()
    assert subscription_manager.get_subscription_count(SERIAL) == 0


def test_v3_subscription_keys():
    session, chunked, objects = parse_subscribe_body(
        {"keys": [{"key": KEY, "version": 3, "timestamp": 123}]}
    )
    assert session == ""
    assert chunked
    assert objects == [{"object_key": KEY, "object_revision": 3, "object_timestamp": 123}]


async def test_v3_temperature_command_delivers_skv_response(
    state_service, subscription_manager, aiohttp_client
):
    await state_service.upsert_object(
        DeviceObject(SERIAL, KEY, 1, 123, {"target_temperature": 21.0}, datetime.now())
    )
    app = web.Application()
    app["state_service"] = state_service
    app["subscription_manager"] = subscription_manager
    app.router.add_post("/nest/transport/v3/subscribe", handle_transport_subscribe)
    client = await aiohttp_client(app)
    request = asyncio.create_task(
        client.post(
            "/nest/transport/v3/subscribe",
            headers={"Authorization": AUTH},
            json={"keys": [{"key": KEY, "version": 1, "timestamp": 123}]},
        )
    )
    try:
        async with asyncio.timeout(2):
            while subscription_manager.get_subscription_count(SERIAL) == 0:
                await asyncio.sleep(0.01)
        await execute_command(state_service, subscription_manager, SERIAL, "set_temperature", 22.0)
        response = await asyncio.wait_for(request, 2)
        assert response.headers["X-nl-skv-key"] == KEY
        assert response.headers["X-nl-skv-version"] == "2"
        assert int(response.headers["X-nl-skv-timestamp"]) > 123
        payload = await response.json()
        assert payload == {"target_temperature": 22.0, "target_change_pending": True}
    finally:
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)
    assert subscription_manager.get_subscription_count(SERIAL) == 0


@pytest.mark.parametrize("version", ["v3", "v7"])
async def test_versioned_device_bootstrap(state_service, aiohttp_client, version):
    await state_service.upsert_object(
        DeviceObject(SERIAL, KEY, 3, 123, {"target_temperature": 22.0}, datetime.now())
    )
    app = web.Application()
    app["state_service"] = state_service
    app.router.add_get("/nest/transport/{path:.*}", handle_transport_get)
    client = await aiohttp_client(app)
    response = await client.get(f"/nest/transport/{version}/device/device.{SERIAL}")
    payload = await response.json()
    if version == "v3":
        assert payload["shared"][SERIAL] == {
            "$version": 3,
            "$timestamp": 123,
            "target_temperature": 22.0,
        }
        assert ": " not in await response.text()
    else:
        obj = next(obj for obj in payload["objects"] if obj["object_key"] == KEY)
        assert set(obj) == {"object_revision", "object_timestamp", "object_key"}
