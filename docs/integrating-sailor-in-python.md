# Integrating Sailor into a Python app

How to wire [`sailor-py`](https://pypi.org/project/sailor-py/) into a Python
service so it reads configuration and secrets from [Sailor](https://github.com/sailorhq/sailor)
in full conformance with the Sailor v1 Consumer Specification.

## How the client works

1. `Consumer(init)` validates resources and resolves the connection from `SAILOR_URI`, `~/.sailor/config`, `ConnectionOption.uri`, or `ConnectionOption.addr`. No network calls or background threads start yet.
2. `consumer.start()` loads every declared resource once (falling back to `SAILOR_FALLBACK_BASE_URL` for `VOLUME`/`PULL` on failure), then starts background polling (`PULL`), debounced directory watching (`VOLUME`/`DEV`), and 10-minute heartbeats.
3. `consumer.get(name, expected_type)` reads the latest snapshot in memory without blocking on I/O.
4. `consumer.close()` stops and joins all background threads and file watchers idempotently.

---

## 1. Define typed config & secrets

```python
from pydantic import BaseModel


class FeatureFlags(BaseModel):
    new_checkout_flow: bool = False
    instant_refunds: bool = False


class AppConfig(BaseModel):
    port: int = 8080
    log_level: str = "info"
    database_url: str
    feature_flags: FeatureFlags = FeatureFlags()


class AppSecrets(BaseModel):
    database_password: str = ""
    stripe_api_key: str = ""
```

---

## 2. Build and start the Consumer

```python
from sailor import Consumer, InitOption, defaults

init = InitOption(
    resources=[
        defaults.config_map_default(AppConfig),   # reads /etc/sailor/_config
        defaults.secrets_default(AppSecrets),     # reads /etc/sailor/secret/_secret
    ],
)

consumer = Consumer(init)
consumer.start()
```

Read values per request via `consumer.get`:

```python
cfg = consumer.get("config", AppConfig)
secrets = consumer.get("secret", AppSecrets)
```

For custom resource names or non-JSON decoders:

```python
import yaml  # optional third-party decoder
from sailor import FetchDefinition, FetchOption, ResourceDefinition, resource, raw

init = InitOption(
    resources=[
        resource(
            ResourceDefinition(name="routing", lang="yaml"),
            FetchDefinition(fetch=FetchOption.PULL, pull_interval=30.0),
            model=RoutingModel,
        ),
        raw(
            ResourceDefinition(name="banner"),
            FetchDefinition(fetch=FetchOption.PULL, once=True),
        ),
    ],
    decoders={"yaml": lambda b: yaml.safe_load(b)},
)
```

---

## 3. Connecting to Sailor

Precedence is strictly exclusive (first matching rule wins):

1. `SAILOR_URI` env var (`sailor://AK:SK@host:port/ns/app?scheme=https`) + optional `SAILOR_TOKEN`.
2. `use_local_config=True` (`~/.sailor/config`, with `namespace` and `app` on `ConnectionOption`; caller `token` overrides file token).
3. `ConnectionOption.uri` (uses `ConnectionOption.token`, else `SAILOR_TOKEN`).
4. `ConnectionOption.addr` (requires `namespace` and `app`; uses `ConnectionOption.token` only).

Note: URIs without `?scheme=https` default to `http`. When a `token` is set over non-loopback `http://`, the consumer emits `config.insecure_transport` at `warn` level.

---

## 4. Fallback for outages

Set `SAILOR_FALLBACK_BASE_URL` (e.g. `https://cdn.example.com/sailor-fallback`).
When an initial `VOLUME` or `PULL` load fails, the consumer requests:

```
{SAILOR_FALLBACK_BASE_URL}/{app}-{name}.sailor.fall
```

without sending `x-token`, requiring HTTP `200`. `DEV` mode never uses fallback.

---

## 5. Error handling & logging

All exceptions inherit from `sailor.SailorError` and carry `.code: sailor.ErrorCode`
(e.g. `ErrorCode.RESOURCE_NOT_LOADED`, `ErrorCode.TYPE_MISMATCH`, `ErrorCode.DECRYPT_FAILED`).

Inject a custom logger matching `sailor.Logger` (`debug`, `info`, `warn`, `error`) via
`InitOption(logger=...)`, or rely on the default `StderrLogger` which emits one structured
`event=...` line per event to `stderr` with secrets and URI userinfo redacted.

---

## 6. Testing without a server

Inject `http_client=httpx.Client(transport=httpx.MockTransport(...))` into `InitOption`:

```python
import httpx
from sailor import (
    ConnectionOption, Consumer, FetchDefinition, FetchOption,
    InitOption, ResourceDefinition, resource,
)

def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/config"):
        return httpx.Response(200, json={"database_url": "postgres://x"})
    return httpx.Response(404)

init = InitOption(
    connection=ConnectionOption(addr="https://sailor.test", namespace="ns", app="app"),
    resources=[
        resource(
            ResourceDefinition(name="config"),
            FetchDefinition(fetch=FetchOption.PULL, once=True),
            model=AppConfig,
        )
    ],
    http_client=httpx.Client(transport=httpx.MockTransport(handler)),
)

with Consumer(init) as c:
    assert c.get("config", AppConfig).database_url == "postgres://x"
```
