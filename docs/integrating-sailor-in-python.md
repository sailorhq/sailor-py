# Integrating Sailor into a Python app

How to wire the [`sailor-py`](https://pypi.org/project/sailor-py/) client into a Python
service so it reads its configuration and secrets from [Sailor](https://github.com/sailorhq/sailor).

Once it's set up, your app gets its config as ordinary Pydantic models. Deploy a new version
from the Sailor Console and the running process picks it up on its own, with no restart and
no polling code of your own.

- [How the client works](#how-the-client-works)
- [1. Install](#1-install)
- [2. Define typed config & secrets](#2-define-typed-config--secrets)
- [3. Create the Consumer (validate, no network)](#3-create-the-consumer-validate-no-network)
- [4. Start the Consumer (fetch + watch)](#4-start-the-consumer-fetch--watch)
- [5. Use config in your app](#5-use-config-in-your-app)
- [6. Fetch modes: VOLUME, PULL, DEV](#6-fetch-modes-volume-pull-dev)
- [7. Connecting to Sailor](#7-connecting-to-sailor)
- [8. Complete example (Kubernetes / production)](#8-complete-example-kubernetes--production)
- [9. One build for prod and dev](#9-one-build-for-prod-and-dev)
- [10. Fallback for outages](#10-fallback-for-outages)
- [11. Error handling](#11-error-handling)
- [12. Testing without a server](#12-testing-without-a-server)
- [Checklist](#checklist)

---

## How the client works

Here's what happens when your app boots:

1. The client figures out how to reach Sailor (from `SAILOR_URI`, the local config file, or
   options you pass).
2. It fetches the current config and secrets.
3. It leaves something running in the background to catch updates: a file watcher for VOLUME
   and DEV, or a polling thread for PULL.
4. From then on, `consumer.get()` hands back the latest value it has.

`get()` is cheap and thread-safe (it swaps an atomic snapshot underneath), so call it
wherever you need config rather than stashing the result in a variable. Cache it yourself and
you throw away the live updates.

Construction and start-up are deliberately separate. Building the `Consumer` only checks your
options; nothing touches the network. The fetch happens in `start()`. So a blip in Sailor's
availability shows up when you call `start()`, not at import time, and you get to decide how
to handle it.

---

## 1. Install

```bash
pip install sailor-py
```

Needs Python 3.9+. The dependency footprint is small: `pydantic`, `httpx`, `cryptography`,
and `watchdog`.

---

## 2. Define typed config & secrets

You describe your config and secrets as Pydantic models and the client parses Sailor's JSON
into them. Field names line up with the keys you set in the Sailor Console. There's no
registration step and no codegen. Default your fields where it makes sense so a missing key
doesn't blow up at startup.

```python
from pydantic import BaseModel


class FeatureFlags(BaseModel):
    new_checkout_flow: bool = False
    instant_refunds: bool = False


class AppConfig(BaseModel):
    port: int = 8080
    log_level: str = "info"
    database_url: str
    max_connections: int = 20
    feature_flags: FeatureFlags = FeatureFlags()


class AppSecrets(BaseModel):
    database_password: str = ""
    jwt_signing_key: str = ""
    stripe_api_key: str = ""
```

If your app has no secrets, drop the `secret` resource and the `secrets_type=` argument and
build the consumer without subscripting it: `Consumer(init, config_type=AppConfig)`. Note that
`Consumer[AppConfig]` on its own won't work — the class expects both type parameters and
raises `TypeError`. If you want the annotation anyway, give it an empty placeholder:
`class NoSecrets(BaseModel): pass`, then `Consumer[AppConfig, NoSecrets]`.

---

## 3. Create the Consumer (validate, no network)

```python
from sailor import Consumer, InitOption, defaults

init = InitOption(
    # No connection here — it's resolved from SAILOR_URI / env (see §7).
    resources=[
        defaults.config_map_default(),   # reads /etc/sailor/_config (K8s volume)
        defaults.secrets_default(),      # reads /etc/sailor/secret/_secret (K8s volume)
    ],
    logging=True,  # log fetch + reload events
)

consumer = Consumer[AppConfig, AppSecrets](
    init, config_type=AppConfig, secrets_type=AppSecrets
)
```

This checks the options — you need at least one resource and enough to resolve a connection —
and raises an `InitError` (such as `NoSailorURLError`) if something's missing. No data is
fetched yet.

> The `Consumer[AppConfig, AppSecrets]` annotation is there for your type checker. Python
> discards those parameters at runtime, which is why you repeat them as `config_type=` /
> `secrets_type=`. Leave them off and you get plain `dict` / `bytes` back.

---

## 4. Start the Consumer (fetch + watch)

`start()` runs once, before you take traffic. It does the first fetch and starts the
background reload.

```python
consumer.start()  # fetches initial values; starts the watcher/poller
```

Let a failed `start()` take the app down with it. A process serving requests with no config
is usually worse than one that refused to boot:

```python
try:
    consumer.start()
except Exception as exc:
    logging.critical("sailor: failed to start: %s", exc)
    raise SystemExit(1)
```

The exception is a service that has to come up even when Sailor is down. For that, lean on
fallback snapshots (§10) or fall back to defaults, but make it a conscious choice rather than
the norm.

The `Consumer` is also a context manager: `with Consumer(...) as c:` starts it on the way in
and closes it on the way out. That's fine for a one-shot read, but a long-running service
should hold onto the consumer rather than close it (see the next section).

---

## 5. Use config in your app

Call `consumer.get()` where you actually use the config, not once at startup. The client
keeps the value current, so reading it per request means you're always on the latest deployed
config.

```python
# In an HTTP handler
def handle_checkout(request):
    cfg = consumer.get()               # -> AppConfig (latest snapshot)
    if not cfg.feature_flags.new_checkout_flow:
        return serve_old_flow()
    return serve_new_flow()


# In a background worker
def run_worker():
    while True:
        cfg = consumer.get()
        secrets = consumer.get_secret()  # -> AppSecrets (already decrypted)
        connect(cfg.database_url, password=secrets.database_password)
        do_work()
        time.sleep(cfg.poll_interval_seconds)
```

There's nothing to gain by caching the result yourself; it just defeats the live reload.

---

## 6. Fetch modes: VOLUME, PULL, DEV

Every resource decides how it's fetched, and you can mix modes in one consumer.

| Mode | Reads from | Refresh | Use when |
|------|-----------|---------|----------|
| **VOLUME** | mounted files (`/etc/sailor/_config`, `/etc/sailor/secret/_secret`) | watches the file, reloads on change | production in Kubernetes |
| **PULL** | Sailor HTTP API | polls every `pull_interval` (default 10s) | deployed without a volume mount |
| **DEV** | fetch once, cache to `~/.sailor/cache`, watch the cache | reloads on local edit | local development |

In production on Kubernetes, use VOLUME. Sailor's plugin keeps the ConfigMap and Secret up to
date, the platform mounts them under `/etc/sailor`, and your app just reads files and picks up
changes when the mount updates. Nothing leaves the pod on the hot path. Reach for PULL only
where there's no mount to read from, like a plain VM. DEV is for your laptop.

The default helpers:

```python
defaults.config_map_default()        # CONFIGS via VOLUME  (/etc/sailor/_config)
defaults.secrets_default()           # SECRETS via VOLUME  (/etc/sailor/secret/_secret)

defaults.config_pull_default()       # CONFIGS via PULL
defaults.secrets_pull_default()      # SECRETS via PULL (decrypted)
defaults.misc_pull_default("flags")  # MISC blob via PULL
defaults.misc_once_default("flags")  # MISC, fetched once

defaults.config_dev_default()        # CONFIGS via DEV
defaults.secrets_dev_default()       # SECRETS via DEV
```

VOLUME and DEV watch files by default; pass `watch=False` in `InitOption` to turn that off.

---

## 7. Connecting to Sailor

The client tries a few sources in order until it has a connection. Whichever you use, a
connection is needed in every mode, VOLUME included: it identifies the namespace and app, and
it holds the access/secret keys that decrypt secrets locally. That's why `SAILOR_URI` is set
in production even when the config itself arrives over a volume mount.

### Option 1 — `SAILOR_URI` env var (production)

The usual production setup. One variable holds the host, namespace, app, and keys:

```
SAILOR_URI=sailor://ACCESS_KEY:SECRET_KEY@sailor-api.sailor.svc.cluster.local:7766/payments/checkout-api
```

```
sailor://ACCESS_KEY:SECRET_KEY@host:port/namespace/app
         └───┬────┘ └───┬────┘ └───┬───┘ └───┬───┘ └─┬─┘
          access    secret       host       ns     app
```

In code you don't pass a connection at all; the client fills it in from `SAILOR_URI`:

```python
init = InitOption(resources=[defaults.config_map_default(), defaults.secrets_default()])
```

Those keys are scoped read credentials for this app, so it's fine to put `SAILOR_URI`
straight into the pod's env (see §8). If you'd rather keep it in a Kubernetes `Secret` and
pull it in with `secretKeyRef`, that works just as well.

### Option 2 — local config (development)

Reads the connection from `~/.sailor/config`, which `sailor login` writes for you, so there's
no env var on your machine. You still have to say which namespace and app:

```python
init = InitOption(
    use_sailor_config=True,
    connection=ConnectionOption(namespace="payments", app="checkout-api"),
    resources=[defaults.config_dev_default(), defaults.secrets_dev_default()],
    logging=True,
)
```

### Option 3 — programmatic URI

When you'd rather read the URI from your own environment variable:

```python
init = InitOption(
    connection=ConnectionOption(uri=os.environ["MY_SAILOR_URI"]),
    resources=[...],
)
```

### Option 4 — explicit fields

Spell everything out:

```python
init = InitOption(
    connection=ConnectionOption(
        addr="http://localhost:7766",
        namespace="payments",
        app="checkout-api",
        access_key=os.environ["SAILOR_ACCESS_KEY"],
        secret_key=os.environ["SAILOR_SECRET_KEY"],
    ),
    resources=[...],
)
```

---

## 8. Complete example (Kubernetes / production)

A Kubernetes service that reads config and secrets from mounted files, with `SAILOR_URI`
supplying the connection and the decryption keys.

```python
import logging
import sys

from pydantic import BaseModel
from sailor import Consumer, InitOption, defaults


class AppConfig(BaseModel):
    port: int = 8080
    log_level: str = "info"


class AppSecrets(BaseModel):
    database_password: str = ""


def build_consumer() -> Consumer[AppConfig, AppSecrets]:
    init = InitOption(
        # SAILOR_URI (pod env) supplies the connection.
        resources=[
            defaults.config_map_default(),   # /etc/sailor/_config
            defaults.secrets_default(),      # /etc/sailor/secret/_secret
        ],
        logging=True,
    )
    return Consumer[AppConfig, AppSecrets](
        init, config_type=AppConfig, secrets_type=AppSecrets
    )


consumer = build_consumer()
try:
    consumer.start()
except Exception as exc:
    logging.critical("sailor: failed to start: %s", exc)
    sys.exit(1)

cfg = consumer.get()
logging.info("starting on port %d (log level: %s)", cfg.port, cfg.log_level)
# ... hand `consumer` to your web framework and call consumer.get() per request ...
```

The pod spec sets `SAILOR_URI` and mounts the config volume:

```yaml
spec:
  template:
    spec:
      containers:
        - name: checkout-api
          image: my-registry/checkout-api:latest
          env:
            - name: SAILOR_URI
              value: "sailor://ak_xJk2:sk_p9Nq@sailor-api.sailor.svc.cluster.local:7766/payments/checkout-api"
          volumeMounts:
            - name: sailor-config
              mountPath: /etc/sailor
              readOnly: true
      volumes:
        - name: sailor-config
          configMap:
            name: checkout-api-config
            items:
              - key: _config
                path: _config
```

---

## 9. One build for prod and dev

Check whether `SAILOR_URI` is set and pick the fetch mode from that, so a single build runs
in both environments with no code change:

```python
from sailor import Consumer, InitOption, ConnectionOption, defaults


def build_consumer() -> Consumer[AppConfig, AppSecrets]:
    if os.environ.get("SAILOR_URI"):
        # Production: SAILOR_URI supplies the connection; read from the mounts.
        init = InitOption(
            resources=[defaults.config_map_default(), defaults.secrets_default()],
            logging=True,
        )
    else:
        # Local: connection from ~/.sailor/config, config/secrets in DEV mode.
        init = InitOption(
            use_sailor_config=True,
            connection=ConnectionOption(namespace="payments", app="checkout-api"),
            resources=[defaults.config_dev_default(), defaults.secrets_dev_default()],
            logging=True,
        )
    return Consumer[AppConfig, AppSecrets](
        init, config_type=AppConfig, secrets_type=AppSecrets
    )
```

---

## 10. Fallback for outages

If a service has to start even when Sailor is unreachable, turn on fallback per resource and
give the client somewhere to read a snapshot from:

```
SAILOR_FALLBACK_BASE_URL=https://cdn.your-company.com/sailor-fallback
```

When the primary fetch fails (unreachable host, network error, a non-2xx response), the client
reads from:

```
https://cdn.your-company.com/sailor-fallback/{app}-{kind}.sailor.fall
```

so `checkout-api-config.sailor.fall` in this case. Turn it on with `fallback_enabled=True`
(the `*_default()` helpers set it already). Write those snapshots out from your last
known-good config on a schedule and park them on a CDN or bucket. It's your break-glass for a
Sailor outage.

---

## 11. Error handling

Everything derives from `sailor.SailorError`, so you catch the specific exception types you
care about.

```python
from sailor import (
    SailorError,
    NoSailorURLError,          # (and NoSailorNamespaceError, NoSailorAppError, etc.)
    ConfigsNotLoadedError,
    SecretsNotLoadedError,
    FetchFallbackFailedError,
    DecryptionError,
)
```

What each group tells you:

- Construction errors (`InitError` subclasses like `NoSailorURLError`) mean the connection
  couldn't be resolved. That's a config or deploy problem, so check the environment.
- A `start()` error means the first fetch failed. Fail hard (§4) unless you've wired up
  fallback (§10).
- `ConfigsNotLoadedError` / `SecretsNotLoadedError` from `get()` / `get_secret()` mean you
  asked for a value before it loaded. With a fail-hard `start()`, your handlers never hit this.
- `DecryptionError` is almost always a sign the access/secret keys don't match the secrets
  you're decrypting.

---

## 12. Testing without a server

Keep the real Sailor server out of your tests. Two approaches work well.

Put config access behind a small function and monkeypatch it to return an `AppConfig(...)`.
Most tests then need no consumer at all.

Or mock the HTTP layer. With `respx` you stub the resource endpoints directly:

```python
import httpx, respx
from sailor import (
    Consumer, InitOption, ConnectionOption,
    ResourceDefinition, ResourceOption, FetchDefinition, FetchOption, ResourceKind,
)

BASE = "https://sailor.test/api/v1/resource/payments/checkout-api"


@respx.mock
def test_reads_config():
    respx.get(f"{BASE}/config").mock(
        return_value=httpx.Response(200, json={"database_url": "postgres://x"})
    )
    init = InitOption(
        connection=ConnectionOption(
            addr="https://sailor.test", namespace="payments", app="checkout-api",
            access_key="ak", secret_key="sk",
        ),
        resources=[ResourceOption(
            definition=ResourceDefinition(kind=ResourceKind.CONFIGS),
            fetch_def=FetchDefinition(fetch=FetchOption.PULL, once=True),
        )],
    )
    with Consumer(init, config_type=AppConfig) as c:  # no secrets in this test
        assert c.get().database_url == "postgres://x"
```

`once=True` keeps the poll thread from sticking around after the test.

---

## Checklist

- [ ] `pip install sailor-py`
- [ ] `AppConfig` / `AppSecrets` models defined, field names matching the Sailor keys, defaults where sensible
- [ ] Connection available: `SAILOR_URI` in prod (needed in every mode, VOLUME included, since it carries the decryption keys); `~/.sailor/config` locally
- [ ] Fetch mode per environment: VOLUME (K8s production), PULL (no mount), DEV (local)
- [ ] `Consumer(...)` built with `config_type=` / `secrets_type=`, which validates without touching the network
- [ ] `start()` called before serving, failing hard on error unless fallback is configured
- [ ] `get()` / `get_secret()` read per request, not cached
- [ ] `SAILOR_FALLBACK_BASE_URL` + `fallback_enabled` if the service needs to survive an outage
- [ ] Tests mock HTTP or inject config, with `once=True` to avoid stray threads
