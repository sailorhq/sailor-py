# 🐧 Sailor Python - Consumer

A type-safe Python client for [Sailor](https://github.com/sailorhq/sailor) —
configuration and secret management for cloud-native applications. Conforms to the
Sailor v1 Consumer Specification (`SAILOR_CONSUMER.md`).

- **Typed** config & secrets via Pydantic v2 models (`resource(..., model=...)` and `consumer.get(name, Type)`)
- **Three fetch modes**: `PULL` (poll the Sailor API), `VOLUME` (read mounted directories with 500 ms debounced reloads), and `DEV` (cached local development with `.meta.json` sidecar & override detection)
- **End-to-end secret decryption** — envelope decryption (HKDF-SHA256 KEK → per-record DEK → AES-256-GCM)
- **Fallback** to HTTP snapshot (`SAILOR_FALLBACK_BASE_URL/{app}-{name}.sailor.fall`) when initial VOLUME or PULL fetch fails
- **Consumer reporting** — async change reports and 10-minute heartbeats when a token is configured
- **Structured redacted logging** — injectable `Logger` protocol with default stderr output

## Install

```bash
pip install sailor-py
```

Requires Python 3.9+.

## Quickstart (PULL)

```python
from pydantic import BaseModel
from sailor import Consumer, InitOption, ConnectionOption, defaults


class AppConfig(BaseModel):
    database_url: str
    port: int = 8080


class AppSecrets(BaseModel):
    api_key: str


init = InitOption(
    connection=ConnectionOption(
        uri="sailor://ACCESS:SECRET@sailor.example.com/team/billing?scheme=https",
        token="app-token",
    ),
    resources=[
        defaults.config_pull_default(AppConfig),       # poll "config" every 10s
        defaults.secrets_pull_default(AppSecrets),     # poll + decrypt "secret"
        defaults.misc_pull_default("flags"),           # raw bytes blob
    ],
)

with Consumer(init) as consumer:
    cfg = consumer.get("config", AppConfig)      # -> AppConfig
    secrets = consumer.get("secret", AppSecrets) # -> AppSecrets (decrypted)
    flags = consumer.get("flags", bytes)         # -> bytes

    print(cfg.database_url, secrets.api_key)
```

`Consumer` starts background threads to keep values fresh; `get(name, expected_type)`
always returns the latest snapshot and is thread-safe. Use it as a context manager
(as above) or call `.start()` / `.close()` explicitly.

## Volume mode (Kubernetes mounts)

```python
init = InitOption(
    resources=[
        defaults.config_map_default(AppConfig),   # reads /etc/sailor/_config
        defaults.secrets_default(AppSecrets),     # reads /etc/sailor/secret/_secret
    ],
    watch=True,  # hot-reload on directory changes (default True)
)
```

Connection is resolved automatically from `SAILOR_URI` (and `SAILOR_TOKEN` if set).

## Dev mode

`config_dev_default()` / `secrets_dev_default()` cache responses to
`~/.sailor/cache/{ns}-{app}-{env}-{name}.json` with a `.meta.json` sidecar,
revalidating pristine caches at start and preserving local edits (`resource.local_override`).

```python
init = InitOption(
    use_local_config=True,
    connection=ConnectionOption(namespace="team", app="billing"),
    resources=[defaults.config_dev_default(AppConfig), defaults.secrets_dev_default(AppSecrets)],
)
```

## Connection precedence

Connection is chosen by the first matching rule (never merged across sources):

1. `SAILOR_URI` env var (`sailor://<ak>:<sk>@<host>[:port]/<ns>/<app>[?scheme=http|https]`) + `SAILOR_TOKEN`
2. `use_local_config=True` (`~/.sailor/config`, requiring `namespace` and `app`; caller `token` overrides file token)
3. `ConnectionOption.uri` (`token` field, else `SAILOR_TOKEN`)
4. `ConnectionOption.addr` (requires `namespace` and `app`; `SAILOR_TOKEN` is ignored)

## Fallback

On initial `VOLUME` or `PULL` failure, the consumer fetches `{SAILOR_FALLBACK_BASE_URL}/{app}-{name}.sailor.fall` without `x-token` and requires HTTP `200`.

## API surface

| Symbol | Role |
|---|---|
| `Consumer(init)` | Validate config and resolve connection (no network/threads) |
| `consumer.start()` | Load all resources initially and start background pollers/watchers/heartbeats |
| `consumer.get(name, expected_type)` | Return current typed value or raise `ResourceNotLoadedError` / `TypeMismatchError` |
| `consumer.close()` | Idempotently stop and join all background threads and watchers |
| `resource(...)` / `raw(...)` | Declare custom named or raw-bytes resources |
| `sailor.defaults.*` | Standard constructors (`config_map_default`, `secrets_default`, `config_pull_default`, `secrets_pull_default`, `config_dev_default`, `secrets_dev_default`, `misc_pull_default`) |
| `SailorError` / `ErrorCode` | Typed exceptions carrying stable `.code` strings |

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest -q     # tests
uv run ruff check .  # lint
uv run mypy          # types
```

## License

GPL-3.0-or-later, matching upstream Sailor.
