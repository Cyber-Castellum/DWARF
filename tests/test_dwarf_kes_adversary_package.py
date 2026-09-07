from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "antithesis" / "components" / "dwarf-kes-adversary"


def test_kes_adversary_is_a_separate_additive_package():
    assert (PKG / "dwarf-kes-adversary.cabal").is_file()
    assert (PKG / "app" / "Main.hs").is_file()
    assert (PKG / "src" / "DwarfKesAdversary.hs").is_file()
    assert (PKG / "test" / "Main.hs").is_file()
    assert "dwarf-adversary" in (PKG / "dwarf-kes-adversary.cabal").read_text()


def test_mutator_is_exactly_scoped_to_the_signature_tail():
    source = (PKG / "src" / "DwarfKesAdversary.hs").read_text()
    assert "mutateKesSignatureBytes" in source
    assert "BS.init bytes" in source
    assert "BS.last bytes `xor` 1" in source


def test_server_requires_the_real_parent_intersection():
    source = (PKG / "src" / "DwarfKesAdversary.hs").read_text()
    assert "find (== parentPoint) points" in source
    assert "SendMsgIntersectNotFound" in source
    assert "SendMsgRollForward" in source
