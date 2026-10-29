import json

import pytest
from conftest import FIXTURES, ROOT

import config


def test_loads_the_course_config():
    cfg = config.load(ROOT / "infra/gateway.yaml")
    assert cfg.upstream == "http://127.0.0.1:8791"
    assert cfg.models["claude-haiku-5-5"].rpm == 60 and cfg.models["claude-haiku-5-5"].tpm == 200000
    assert cfg.models["claude-haiku-5-5"].price == "lab"
    assert cfg.routes["claude-haiku-5-5"].to == "claude-haiku-5-5" and cfg.deny_unknown is True
    assert cfg.budgets["desk"].limit_usd == 1.00 and cfg.budgets["desk"].warn_usd == 0.80
    assert cfg.budgets["judge"].limit_usd == 0.50 and cfg.budgets["judge"].warn_usd is None
    assert cfg.untagged == "refuse"
    assert config.SECTIONS == ("upstream", "models", "routes", "cache", "retry", "limit", "budgets", "traces")


@pytest.mark.parametrize("name,needle", [
    ("missing_section", "budgets"), ("unknown_key", "retries"), ("negative_limit", "limit_usd"),
    ("route_to_undefined_model", "claude-sonnet-5-5"), ("warn_above_limit", "warn_usd"),
    ("nan_rate", "rpm"), ("zero_ttl", "ttl_s"), ("non_loopback_upstream", "api.anthropic.com")])
def test_each_rule_names_its_key(name, needle):
    with pytest.raises(config.ConfigError, match=needle):
        config.load(FIXTURES / "configs" / f"{name}.yaml")


def test_live_allows_a_remote_upstream():
    cfg = config.load(FIXTURES / "configs" / "non_loopback_upstream.yaml", live=True)
    assert cfg.upstream.startswith("https://api.anthropic.com")


@pytest.mark.parametrize("url,ok", [("http://127.0.0.1:8791", True), ("http://localhost:1", True),
                                    ("http://[::1]:8791", True), ("https://api.anthropic.com", False),
                                    ("http://127.0.0.1.evil.com", False)])
def test_is_loopback(url, ok):
    assert config.is_loopback(url) is ok


def test_export_matches_the_yaml():
    exported = json.loads((FIXTURES / "config.json").read_text())
    assert exported == config.to_json(config.load(ROOT / "infra/gateway.yaml"))
    assert exported["models"]["claude-haiku-5-5"]["price"] == "lab"
    assert config.main(["--check"]) == 0
