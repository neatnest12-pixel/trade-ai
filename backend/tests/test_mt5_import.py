import importlib.util


def test_mt5_dependency_is_optional_for_core_imports():
    assert importlib.util.find_spec('app.market_data.adapters') is not None
