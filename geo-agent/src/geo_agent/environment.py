import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


def load_environment(env_file: Path | None = None) -> None:
    path = env_file if env_file is not None else Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(path, override=False, interpolate=False)
    # Local credentials win; explicit port, policy and mock-mode overrides still apply.
    for name, value in dotenv_values(path, interpolate=False).items():
        if value is not None and name.endswith(("_KEY", "_TOKEN")):
            os.environ[name] = value
