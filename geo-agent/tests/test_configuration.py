import os

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


def test_existing_environment_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBIQ_API_KEY", "dummy-existing-value")
    env_file = tmp_path / ".env"
    env_file.write_text("WEBIQ_API_KEY=dummy-file-value\n", encoding="utf-8")
    load_environment(env_file)
    assert os.environ["WEBIQ_API_KEY"] == "dummy-existing-value"


def test_missing_env_file_is_optional(tmp_path, monkeypatch):
    monkeypatch.delenv("WEBIQ_API_KEY", raising=False)
    load_environment(tmp_path / "missing.env")
    assert "WEBIQ_API_KEY" not in os.environ


def test_default_env_file_is_relative_to_agent_not_working_directory(tmp_path, monkeypatch):
    import geo_agent.__main__ as launcher

    monkeypatch.setattr(launcher, "__file__", str(tmp_path / "src" / "geo_agent" / "__main__.py"))
    monkeypatch.delenv("WEBIQ_API_KEY", raising=False)
    (tmp_path / ".env").write_text("WEBIQ_API_KEY=dummy-agent-value\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".env").write_text("WEBIQ_API_KEY=dummy-wrong-directory\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    load_environment()
    assert os.environ["WEBIQ_API_KEY"] == "dummy-agent-value"