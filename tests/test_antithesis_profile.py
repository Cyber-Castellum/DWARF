from profile_manager.profiles import find_profile, load_profiles


def test_closed_amaru_profile_loads():
    p = find_profile("profile-l-amaru-closed-devnet")
    assert p.node_type == "amaru"
    assert p.amaru_node_count == 1
    assert p.node_count == 0


def test_closed_amaru_profile_is_hermetic():
    p = find_profile("profile-l-amaru-closed-devnet")
    # No public network and no public upstream peer -> safe for Antithesis sim.
    assert p.public_network is None
    assert p.upstream_peer_address is None


def test_profile_l_is_discoverable():
    assert any(p.id == "profile-l-amaru-closed-devnet" for p in load_profiles())
