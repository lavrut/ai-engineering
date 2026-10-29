import sys

import pytest
from conftest import FIXTURES, ROOT

import config
import ledger


def test_prices_are_the_labs():
    assert ledger.lab_prices()["claude-haiku-5-5"] == (0.10, 0.50)


def test_cost_matches_the_proxys_formula():
    usage = {"input_tokens": 1000, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 300, "output_tokens": 100}
    assert ledger.cost_of("claude-haiku-5-5", usage, ledger.lab_prices()) == pytest.approx((1000*0.10 + 200*1.25*0.10 + 300*0.10*0.10 + 100*0.50) / 1e6)
    sys.path.insert(0, str(ROOT / "llms/python")); sys.path.insert(0, str(ROOT / "agents/proxy"))
    import proxy_cost                                                          # read-only, as the agents tests do
    assert ledger.cost_of("claude-haiku-5-5", usage, ledger.lab_prices()) == proxy_cost.ledger_cost("claude-haiku-5-5", usage)


def test_unknown_model_costs_none():
    assert ledger.cost_of("claude-x", {k: 1 for k in ledger.USAGE_FIELDS}, ledger.lab_prices()) is None


def test_a_missing_usage_field_costs_none():
    assert ledger.cost_of("claude-haiku-5-5", {"input_tokens": 1}, ledger.lab_prices()) is None


def test_multipliers_and_fields():
    assert ledger.CACHE_WRITE_MULTIPLIER == 1.25 and ledger.CACHE_READ_MULTIPLIER == 0.10
    assert ledger.USAGE_FIELDS == ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def test_resolve_prices_turns_lab_into_the_table_and_pairs_into_tuples():
    d = config.to_json(config.load(ROOT / "infra/gateway.yaml"))
    d["models"]["claude-sonnet-5-5"] = {"rpm": 10, "tpm": 1000, "price": [3.0, 15.0]}
    prices = ledger.resolve_prices(config.from_dict(d))
    assert prices == {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (3.0, 15.0)}


def test_worst_case_usd():
    prices = ledger.lab_prices()
    assert ledger.worst_case_usd("claude-haiku-5-5", 1000, 2000, prices) == pytest.approx((1000*0.10 + 2000*0.50) / 1e6)
    assert ledger.worst_case_usd("claude-x", 1000, 2000, prices) == 0.0


def test_from_dict_round_trips_the_export_and_validates():
    import json
    d = json.loads((FIXTURES / "config.json").read_text())
    assert config.to_json(config.from_dict(d)) == d
    d["budgets"]["desk"]["warn_usd"] = 2.0
    with pytest.raises(config.ConfigError, match="warn_usd"):
        config.from_dict(d)
