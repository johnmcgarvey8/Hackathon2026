import pytest

from geo_agent.artifact_storage import LocalArtifactStorage
from geo_agent.workflow import Conflict, NotFound


def test_local_artifact_storage_is_idempotent_and_path_safe(tmp_path):
    storage = LocalArtifactStorage(tmp_path / "artifacts")
    storage.put("owner/run/hash.zip", b"immutable bundle")
    storage.put("owner/run/hash.zip", b"immutable bundle")

    assert storage.get("owner/run/hash.zip") == b"immutable bundle"
    with pytest.raises(Conflict, match="different content"):
        storage.put("owner/run/hash.zip", b"changed bundle")
    with pytest.raises(ValueError, match="safe relative"):
        storage.put("../outside.zip", b"content")
    with pytest.raises(NotFound, match="not found"):
        storage.get("owner/run/missing.zip")