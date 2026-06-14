import pytest
from profile_manager.backends.base import BackendArtifacts, write_artifacts


def test_write_artifacts_writes_files_and_returns_paths(tmp_path):
    arts = BackendArtifacts(
        backend="demo",
        files={"a.txt": "hello\n", "sub/b.txt": "world\n"},
        summary={"ok": True},
    )
    written = write_artifacts(arts, tmp_path)
    assert set(written) == {"a.txt", "sub/b.txt"}
    assert (tmp_path / "a.txt").read_text() == "hello\n"
    assert (tmp_path / "sub" / "b.txt").read_text() == "world\n"


def test_get_backend_unknown_raises():
    from profile_manager.backends import get_backend
    with pytest.raises(KeyError):
        get_backend("nope")


def test_local_backend_matches_existing_compose_template():
    from profile_manager.backends import get_backend
    from profile_manager.profiles import find_profile, compose_template

    profile = find_profile("profile-a-haskell-peersharing-disabled")
    arts = get_backend("local").render(profile)
    assert arts.backend == "local"
    # Delegates to the existing emitter — byte-for-byte, so local behavior is unchanged.
    assert arts.files["docker-compose.yml"] == compose_template(profile)
    assert arts.summary["compose_project"] == profile.compose_project
