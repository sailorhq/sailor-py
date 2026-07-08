# Sailor-Py

A type-safe Python client for [Sailor](https://github.com/sailorhq/sailor) —
configuration and secret management for cloud-native applications. This is the
Python counterpart to [`sailor-go`](https://github.com/sailorhq/sailor-go) and
speaks the same wire protocol.

- **Typed** config & secrets via Pydantic v2 models
- **Two fetch modes**: `PULL` (poll the Sailor API) and `VOLUME` (read mounted
  ConfigMaps/Secrets, hot-reloaded via a file watcher)
- **End-to-end secret decryption** — envelope decryption (HKDF-SHA256 KEK →
  per-record DEK → AES-256-GCM), compatible with the Sailor server's vault
- **Fallback** to a local/remote snapshot when the server is unreachable
- Flexible connection config: URI, explicit options, env vars, or `~/.sailor/config`

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
    connection=ConnectionOption(uri="sailor://ACCESS:SECRET@sailor.example.com/team/billing"),
    resources=[
        defaults.config_pull_default(),      # poll config every 10s
        defaults.secrets_pull_default(),     # poll + decrypt secrets
        defaults.misc_pull_default("flags"), # arbitrary blob
    ],
)

with Consumer[AppConfig, AppSecrets](
    init, config_type=AppConfig, secrets_type=AppSecrets
) as consumer:
    cfg = consumer.get()               # -> AppConfig
    secrets = consumer.get_secret()    # -> AppSecrets (decrypted)
    flags = consumer.get_misc("flags") # -> bytes

    print(cfg.database_url, secrets.api_key)
```

`Consumer` starts background threads to keep values fresh; `get()` /
`get_secret()` / `get_misc()` always return the latest snapshot and are
thread-safe. Use it as a context manager (as above) or call `.start()` /
`.close()` explicitly.

## Volume mode (Kubernetes mounts)

```python
init = InitOption(
    connection=ConnectionOption(namespace="team", app="billing", access_key="AK", secret_key="SK"),
    resources=[
        defaults.config_map_default(),   # reads /etc/sailor/_config
        defaults.secrets_default(),      # reads /etc/sailor/_secret
    ],
    watch=True,  # hot-reload on file change (default when volumes are used)
)
```

## Connection resolution

Fields are resolved in this order (earlier wins, never overwritten):

1. Explicit `ConnectionOption` fields
2. `sailor://accessKey:secretKey@host/namespace/app` URI (`uri` field or `SAILOR_URL`)
3. Environment: `SAILOR_URL`, `SAILOR_NS`, `SAILOR_APP`, `SAILOR_ACCESS_KEY`, `SAILOR_SECRET_KEY`
4. `~/.sailor/config` (only when `use_sailor_config=True`)

The optional bearer token is sent as the `x-token` header.

## Fallback

Set `fallback_enabled=True` on a resource and point `SAILOR_FALLBACK_BASE_URL`
at a directory or `http(s)://` base. On fetch failure the client reads
`{base}/{app}-{kind}.sailor.fall`.

## API surface

| Go (`sailor-go`)              | Python (`sailor-py`)                              |
| ----------------------------- | ------------------------------------------------- |
| `NewConsumer[C,S](opts)`      | `Consumer[C,S](init, config_type=, secrets_type=)`|
| `Start()`                     | `start()` (or `with Consumer(...)`)               |
| `Get()` / `GetSecret()`       | `get()` / `get_secret()`                          |
| `GetMisc(name)`               | `get_misc(name)`                                  |
| `opts.*` structs              | `sailor.options.*` Pydantic models                |
| `defaults.*`                  | `sailor.defaults.*`                               |

Errors are Python exceptions under `sailor.SailorError` (e.g.
`ConfigsNotLoadedError`, `FetchFallbackFailedError`, `DecryptionError`) — catch
by type instead of Go's `errors.Is`.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest        # tests
uv run ruff check .  # lint
uv run mypy          # types
```

## Releasing

Releases publish to PyPI automatically via
[`.github/workflows/publish.yml`](.github/workflows/publish.yml) using PyPI
**Trusted Publishing** (OIDC) — no API tokens or repository secrets.

One-time PyPI setup: create the project's *pending publisher* pointing at this
repo, the `publish.yml` workflow, and a `pypi` environment.

To cut a release, bump `version` in `pyproject.toml`, then tag and push:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The tag triggers a build (`uv build`) and upload. Test the flow first with
TestPyPI or `twine check dist/*` locally.

## License

GPL-3.0-or-later, matching upstream Sailor.
