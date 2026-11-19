"""Conformance tests C-19..C-24, C-38..C-41, and C-50..C-69."""

from __future__ import annotations

import hashlib
import re
import threading

import pytest
from pydantic import BaseModel

from sailor import (
    AlreadyStartedError,
    ConnectionOption,
    Consumer,
    ErrorCode,
    FetchDefinition,
    FetchOption,
    InitOption,
    ResourceDefinition,
    ResourceNotLoadedError,
    TypeMismatchError,
    defaults,
    raw,
    resource,
)
from sailor.report import HEARTBEAT_INTERVAL_SECONDS

from .conftest import encrypted_secrets_payload
from .fakes import Harness

ADDR = "https://sailor.test"
NS = "team"
APP = "billing"
AK = "access-key-secret-id"
SK = "super-secret-signing-key"
TOKEN = "super-secret-app-token"


class AppConfig(BaseModel):
    database_url: str
    port: int = 8080


class OtherModel(BaseModel):
    other: str


def _conn(token: str = TOKEN, **overrides: str) -> ConnectionOption:
    return ConnectionOption(
        addr=overrides.get("addr", ADDR),
        namespace=overrides.get("namespace", NS),
        app=overrides.get("app", APP),
        access_key=overrides.get("access_key", AK),
        secret_key=overrides.get("secret_key", SK),
        token=token,
    )


def _pull_once(name: str = "config", *, model: type[BaseModel] | None = None):
    return resource(
        ResourceDefinition(name=name),
        FetchDefinition(fetch=FetchOption.PULL, once=True),
        model=model,
    )


def test_c19_raw_json_bytes_stored_unchanged():
    h = Harness()
    raw_bytes = b'{"hello":  "world"}\n'
    h.server.set_resource("flags", raw_bytes)
    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[
                raw(
                    ResourceDefinition(name="flags"),
                    FetchDefinition(fetch=FetchOption.PULL, once=True),
                )
            ],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    with c:
        assert c.get("flags", bytes) == raw_bytes


def test_c20_missing_resource_version_header_no_error():
    h = Harness()
    h.server.set_resource("config", b'{"database_url":"pg"}', version="")
    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[_pull_once(model=AppConfig)],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    with c:
        assert c.get("config", AppConfig).database_url == "pg"
        assert c._by_name["config"].known_version() == ""


def test_c21_404_never_deployed_no_fallback_fails_start():
    h = Harness()
    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[_pull_once(model=AppConfig)],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    with pytest.raises(ResourceNotLoadedError) as exc:
        c.start()
    assert exc.value.code is ErrorCode.RESOURCE_NOT_LOADED


def test_c22_token_header_sent_and_401_fails_start():
    h = Harness()
    h.server.set_resource("config", b'{"message":"unauthorized"}', status_code=401)
    c = Consumer(
        InitOption(
            connection=_conn(token="my-tok"),
            resources=[_pull_once()],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    with pytest.raises(ResourceNotLoadedError):
        c.start()
    gets = h.server.resource_gets("config")
    assert len(gets) == 1
    assert gets[0].headers["x-token"] == "my-tok"


def test_c23_304_keeps_value_and_no_change_report():
    h = Harness()
    h.server.respect_if_none_match = True
    h.server.set_resource("config", b'{"v":1}', version="v1")

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_pull_default(interval=10.0)],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        h.clock.wait_for_waiters(2)
        h.clock.advance(10.0)
        h.clock.wait_for_waiters(2)
    finally:
        c.close()

    gets = h.server.resource_gets("config")
    assert len(gets) >= 2
    assert gets[1].headers["if-none-match"] == "v1"
    assert len(h.server.reports) == 1
    assert len(h.logger.by_name("resource.changed")) == 0


def test_c24_200_same_digest_no_change_no_report():
    h = Harness()
    h.server.respect_if_none_match = False
    h.server.set_resource("config", b'{"v":1}', version="v1")

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_pull_default(interval=10.0)],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        h.clock.wait_for_waiters(2)
        h.server.set_resource("config", b'{"v":1}', version="v2")  # different version, same bytes
        h.clock.advance(10.0)
        h.clock.wait_for_waiters(2)
    finally:
        c.close()

    assert len(h.server.reports) == 1
    assert len(h.logger.by_name("resource.changed")) == 0


def test_c38_c39_c40_c67_outage_recovery_and_404_after_load():
    h = Harness()
    h.server.set_resource("config", b'{"v":1}', version="1")

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_pull_default(interval=10.0)],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        # 30 failing polls (500 and 404) -> exactly ONE resource.stale (C-38, C-40, C-67)
        h.server.set_resource("config", b'{"message":"err"}', status_code=500)
        for i in range(30):
            if i == 15:
                h.server.set_resource("config", b'{"message":"gone"}', status_code=404)
            h.clock.wait_for_waiters(2)
            h.clock.advance(10.0)
        h.clock.wait_for_waiters(2)

        assert c.get("config") == {"v": 1}
        assert len(h.logger.by_name("resource.stale")) == 1
        assert len(h.logger.by_name("resource.recovered")) == 0

        # Recover (C-39)
        h.server.set_resource("config", b'{"v":2}', version="2")
        h.clock.advance(10.0)
        h.clock.wait_for_waiters(2)

        assert c.get("config") == {"v": 2}
        assert len(h.logger.by_name("resource.recovered")) == 1
    finally:
        c.close()


def test_c41_pull_once_makes_exactly_one_request():
    h = Harness()
    h.server.set_resource("config", b'{"v":1}', version="1")

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[_pull_once()],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        h.clock.wait_for_waiters(1)  # only heartbeat waiter
        h.clock.advance(60.0)
        assert len(h.server.resource_gets("config")) == 1
    finally:
        c.close()


def test_c50_to_c58_reporting_and_heartbeat():
    h = Harness(
        env={"POD_NAME": "", "POD_IP": "10.0.0.5", "NODE_NAME": "node-a"},
        host="",  # unreadable/empty hostname -> "unknown" (C-58)
    )
    body1 = b'{"v":1}'
    body2 = b'{"v":2}'
    h.server.set_resource("config", body1, version="1")

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_pull_default(interval=10.0)],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        # Change digest (C-51)
        h.clock.wait_for_waiters(2)
        h.server.set_resource("config", body2, version="2")
        h.clock.advance(10.0)
        h.clock.wait_for_waiters(2)

        # Heartbeat (C-53) — advance 600s
        h.clock.advance(HEARTBEAT_INTERVAL_SECONDS)
        h.clock.wait_for_waiters(2)
    finally:
        c.close()

    # Wait until close() finishes joining report threads
    assert len(h.logger.by_name("resource.loaded")) == 1
    assert len(h.logger.by_name("resource.changed")) == 1
    assert len(h.server.reports) >= 3

    first = h.server.reports[0]
    assert first["consumer"] == "unknown"  # C-58
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", first["digest"])  # C-54
    assert first["digest"] == f"sha256:{hashlib.sha256(body1).hexdigest()}"
    # C-57: POD_NAME unset/empty omitted from details
    assert "pod_name" not in first["details"]
    assert first["details"] == {"pod_ip": "10.0.0.5", "node_name": "node-a"}

    # C-55: report 500 logs report.failed with truncated 4096-byte body
    h2 = Harness()
    h2.server.set_resource("config", b'{"v":1}', version="1")
    h2.server.report_status = 500
    h2.server.report_body = b"x" * 5000
    c2 = Consumer(
        InitOption(
            connection=_conn(),
            resources=[_pull_once()],
            http_client=h2.http_client(),
            logger=h2.logger,
        ),
        _deps=h2.deps(),
    )
    with c2:
        pass
    failed = h2.logger.by_name("report.failed")
    assert len(failed) == 1
    assert failed[0].fields["status"] == 500
    assert len(failed[0].fields["message"]) == 4096


def test_c56_no_token_skips_reports_and_logs_once():
    h = Harness()
    h.server.set_resource("config", b'{"v":1}', version="1")
    c = Consumer(
        InitOption(
            connection=_conn(token=""),
            resources=[defaults.config_pull_default(interval=10.0)],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        h.clock.wait_for_waiters(2)
        h.clock.advance(HEARTBEAT_INTERVAL_SECONDS)
        h.clock.wait_for_waiters(2)
    finally:
        c.close()

    assert h.server.reports == []
    assert len(h.logger.by_name("report.skipped_no_token")) == 1


def test_c59_blocking_report_does_not_block_get():
    h = Harness()
    h.server.set_resource("config", b'{"v":1}', version="1")
    h.server.report_gate = threading.Event()
    h.server.report_started = threading.Event()

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[_pull_once()],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        assert h.server.report_started.wait(timeout=2.0)
        assert c.get("config") == {"v": 1}
    finally:
        h.server.report_gate.set()
        c.close()


def test_c60_c61_c62_c63_lifecycle_and_independence():
    h1 = Harness()
    h2 = Harness()
    h1.server.set_resource("config", b'{"who":1}', version="1")
    h2.server.set_resource("config", b'{"who":2}', version="1")

    c1 = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_pull_default(interval=10.0)],
            http_client=h1.http_client(),
            logger=h1.logger,
        ),
        _deps=h1.deps(),
    )
    c2 = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_pull_default(interval=10.0)],
            http_client=h2.http_client(),
            logger=h2.logger,
        ),
        _deps=h2.deps(),
    )
    c1.start()
    c2.start()

    # C-60: independent state
    assert c1.get("config") == {"who": 1}
    assert c2.get("config") == {"who": 2}

    # C-61 & C-62: close during polling joins threads and is idempotent
    c1.close()
    c1.close()
    c2.close()

    # C-63: get after close returns last value without error
    assert c1.get("config") == {"who": 1}
    assert c2.get("config") == {"who": 2}

    # Cannot restart after start/close
    with pytest.raises(AlreadyStartedError) as exc:
        c1.start()
    assert exc.value.code is ErrorCode.ALREADY_STARTED


def test_c64_concurrent_reads_and_updates():
    h = Harness()
    h.server.set_resource("config", b'{"n":0}', version="0")
    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_pull_default(interval=10.0)],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    c.start()
    errors: list[Exception] = []
    stop_readers = threading.Event()

    def reader() -> None:
        while not stop_readers.is_set():
            try:
                val = c.get("config", dict)
                assert "n" in val
            except Exception as exc:
                errors.append(exc)
                return

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in readers:
        t.start()

    try:
        for i in range(1, 20):
            h.clock.wait_for_waiters(2)
            h.server.set_resource("config", f'{{"n":{i}}}'.encode(), version=str(i))
            h.clock.advance(10.0)
        h.clock.wait_for_waiters(2)
    finally:
        stop_readers.set()
        for t in readers:
            t.join(timeout=2.0)
        c.close()

    assert errors == []


def test_c65_c66_c68_redaction_and_event_catalogue():
    h = Harness()
    secret_val = "ultra-sensitive-db-pass-999"
    h.server.set_resource(
        "secret",
        encrypted_secrets_payload({"db_pass": secret_val}, secret_key=SK, access_key=AK),
        version="1",
    )
    c = Consumer(
        InitOption(
            connection=ConnectionOption(
                uri=f"sailor://{AK}:{SK}@sailor.prod:7766/{NS}/{APP}",
                token=TOKEN,
            ),
            resources=[defaults.secrets_pull_default()],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    with c:
        assert c.get("secret", dict) == {"db_pass": secret_val}

    dump = h.logger.all_serialized()
    for sensitive in (TOKEN, AK, SK, secret_val):
        assert sensitive not in dump

    # C-68: verify event ordering & required fields
    resolved = h.logger.by_name("config.resolved")[0]
    assert list(resolved.fields.keys())[0] == "event"
    assert resolved.fields["addr"] == "http://sailor.prod:7766"
    assert resolved.fields["namespace"] == NS
    assert resolved.fields["app"] == APP
    assert resolved.fields["token"] is True

    loaded = h.logger.by_name("resource.loaded")[0]
    assert loaded.fields["resource"] == "secret"
    assert loaded.fields["mode"] == "pull"
    assert re.fullmatch(r"sha256:[0-9a-f]{12}", loaded.fields["digest"])

    started = h.logger.by_name("consumer.started")[0]
    assert started.fields["resources"] == 1

    stopped = h.logger.by_name("consumer.stopped")[0]
    assert stopped.fields["resources"] == 1
    assert "uptime_seconds" in stopped.fields


def test_c69_wrong_type_returns_type_mismatch():
    h = Harness()
    h.server.set_resource("config", b'{"database_url":"pg"}')
    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[_pull_once(model=AppConfig)],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    with c:
        cfg = c.get("config", AppConfig)
        assert cfg.database_url == "pg"
        with pytest.raises(TypeMismatchError) as exc:
            c.get("config", OtherModel)
        assert exc.value.code is ErrorCode.TYPE_MISMATCH


def test_poll_decode_failure_logged_once_per_bad_digest():
    h = Harness()
    h.server.set_resource("config", b'{"v":1}', version="1")
    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_pull_default(interval=10.0)],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        h.server.set_resource("config", b"{bad-json", version="2")
        for _ in range(3):
            h.clock.wait_for_waiters(2)
            h.clock.advance(10.0)
        h.clock.wait_for_waiters(2)
        assert c.get("config") == {"v": 1}
        assert len(h.logger.by_name("resource.decode_failed")) == 1
    finally:
        c.close()
