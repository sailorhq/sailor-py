"""Option model + defaults-helper tests."""

from __future__ import annotations

from sailor import defaults
from sailor.options import (
    DEFAULT_CONFIG_VOLUME_PATH,
    DEFAULT_PULL_INTERVAL_SECONDS,
    DEFAULT_SECRET_VOLUME_PATH,
    FetchOption,
    ResourceKind,
)


def test_resource_kind_values_match_wire():
    assert ResourceKind.CONFIGS.value == "config"
    assert ResourceKind.SECRETS.value == "secret"
    assert ResourceKind.MISC.value == "misc"


def test_fetch_option_values_match_go():
    assert FetchOption.VOLUME == 1
    assert FetchOption.PULL == 2
    assert FetchOption.DEV == 3


def test_config_map_default():
    opt = defaults.config_map_default()
    assert opt.definition.kind is ResourceKind.CONFIGS
    assert opt.definition.path == DEFAULT_CONFIG_VOLUME_PATH
    assert opt.fetch_def.fetch is FetchOption.VOLUME


def test_secrets_default_path():
    assert defaults.secrets_default().definition.path == DEFAULT_SECRET_VOLUME_PATH


def test_config_pull_default_interval():
    opt = defaults.config_pull_default()
    assert opt.fetch_def.fetch is FetchOption.PULL
    assert opt.fetch_def.pull_interval == DEFAULT_PULL_INTERVAL_SECONDS
    assert opt.fetch_def.once is False


def test_misc_once_default():
    opt = defaults.misc_once_default("banner")
    assert opt.definition.kind is ResourceKind.MISC
    assert opt.definition.name == "banner"
    assert opt.fetch_def.once is True


def test_misc_pull_default_custom_interval():
    opt = defaults.misc_pull_default("flags", interval=2.5)
    assert opt.fetch_def.pull_interval == 2.5
    assert opt.fetch_def.once is False
