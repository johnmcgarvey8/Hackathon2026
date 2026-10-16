import os
from importlib import import_module

import pytest

from geo_agent.__main__ import load_environment


def test_env_file_loads_endpoints_and_preserves_literal_key(tmp_path, monkeypatch):
    for name in ("WEBIQ_BROWSE_ENDPOINT", "WEBIQ_SEARCH_ENDPOINT", "WEBIQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WEBIQ_BROWSE_ENDPOINT=https://api.microsoft.ai/v3/browse\n"
        "WEBIQ_SEARCH_ENDPOINT=https://api.microsoft.ai/v3/search/web\n"
        "WEBIQ_API_KEY='dummy-${NOT_AN_ENV_REFERENCE}-value'\n",
        encoding="utf-8",
    )
    load_environment(env_file)
    assert os.environ["WEBIQ_BROWSE_ENDPOINT"] == "https://api.microsoft.ai/v3/browse"
    assert os.environ["WEBIQ_SEARCH_ENDPOINT"] == "https://api.microsoft.ai/v3/search/web"
    assert os.environ["WEBIQ_API_KEY"] == "dummy-${NOT_AN_ENV_REFERENCE}-value"


@pytest.mark.parametrize("name", ["WEBIQ_API_KEY", "ANTHROPIC_API_KEY", "GEO_API_TOKEN", "GEO_MCP_AGENT_TOKEN"])
def test_env_file_credentials_override_inherited_values(tmp_path, monkeypatch, name):
    monkeypatch.setenv(name, "dummy-existing-value")
    env_file = tmp_path / ".env"
    env_file.write_text(f"{name}=dummy-file-value\n", encoding="utf-8")
    load_environment(env_file)
    assert os.environ[name] == "dummy-file-value"


def test_empty_file_key_does_not_fall_back_to_inherited_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBIQ_API_KEY", "dummy-existing-value")
    env_file = tmp_path / ".env"
    env_file.write_text("WEBIQ_API_KEY=\n", encoding="utf-8")
    load_environment(env_file)
    assert os.environ["WEBIQ_API_KEY"] == ""


def test_unspecified_credentials_and_explicit_runtime_overrides_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBIQ_API_KEY", "dummy-existing-value")
    monkeypatch.setenv("GEO_PORT", "8091")
    monkeypatch.setenv("GEO_MEASUREMENT_POLICY", "measurement-policy.json")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GEO_PORT=8088\nGEO_MEASUREMENT_POLICY=measurement-live-policy.json\n",
        encoding="utf-8",
    )
    load_environment(env_file)
    assert os.environ["WEBIQ_API_KEY"] == "dummy-existing-value"
    assert os.environ["GEO_PORT"] == "8091"
    assert os.environ["GEO_MEASUREMENT_POLICY"] == "measurement-policy.json"


@pytest.mark.parametrize("module_name", [
    "geo_agent.__main__",
    "geo_agent.measurement_worker",
    "geo_agent.mock_measurement_worker",
    "geo_agent.mcp_server",
])
def test_local_entrypoints_share_the_environment_loader(module_name):
    assert import_module(module_name).load_environment is load_environment


def test_missing_env_file_is_optional(tmp_path, monkeypatch):
    monkeypatch.delenv("WEBIQ_API_KEY", raising=False)
    load_environment(tmp_path / "missing.env")
    assert "WEBIQ_API_KEY" not in os.environ


def test_default_env_file_is_relative_to_agent_not_working_directory(tmp_path, monkeypatch):
    import geo_agent.environment as environment

    monkeypatch.setattr(environment, "__file__", str(tmp_path / "src" / "geo_agent" / "environment.py"))
    monkeypatch.delenv("WEBIQ_API_KEY", raising=False)
    (tmp_path / ".env").write_text("WEBIQ_API_KEY=dummy-agent-value\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".env").write_text("WEBIQ_API_KEY=dummy-wrong-directory\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    load_environment()
    assert os.environ["WEBIQ_API_KEY"] == "dummy-agent-value"


def test_main_reports_token_path_from_configured_data_directory(tmp_path, monkeypatch, capsys):
    import geo_agent.__main__ as launcher

    monkeypatch.setattr(launcher, "load_environment", lambda: None)
    monkeypatch.setattr(launcher, "create_app", lambda *args, **kwargs: object())
    monkeypatch.setattr(launcher.uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setenv("GEO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GEO_API_TOKEN", raising=False)
    for name in ("GEO_LIVE_POLICY", "GEO_BUDGET_GRANT", "GEO_CHAT_POLICY", "GEO_ANALYSIS_POLICY", "GEO_MEASUREMENT_POLICY"):
        monkeypatch.delenv(name, raising=False)

    launcher.main()

    assert capsys.readouterr().out.strip() == f"Local API token: {(tmp_path / 'local-api-token').resolve()} (value not logged)"