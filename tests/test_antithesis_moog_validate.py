from profile_manager.antithesis import build_antithesis_bundle
from profile_manager.profiles import find_profile
from profile_manager.moog import validate_moog_asset, moog_asset_summary


def test_validate_accepts_emitted_bundle(tmp_path):
    build_antithesis_bundle(find_profile("profile-l-amaru-closed-devnet"), tmp_path,
                            registry="REG", tag="x")
    result = validate_moog_asset(str(tmp_path))
    summary = moog_asset_summary(result)
    # compose is found under config/ and a services: section is present.
    # moog asset summary uses the ready/warn/blocked vocabulary.
    assert summary["state"] in {"ready", "warn"}
    assert summary["error_count"] == 0
    assert summary["docker_compose"]  # non-empty path
