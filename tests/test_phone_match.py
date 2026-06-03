"""Phone matching for PBX / SMS lead lookup."""

from integrations.services.phone_match import phone_match_keys


def test_phone_match_keys_iraq_local_vs_e164():
    local = phone_match_keys("07812113063")
    e164 = phone_match_keys("+9647812113063")
    assert local & e164, "local 07… and +964… should share match keys"
    assert "9647812113063" in local
    assert "9647812113063" in e164
    assert "07812113063" in e164


def test_phone_match_keys_964_without_plus():
    a = phone_match_keys("9647812113063")
    b = phone_match_keys("07812113063")
    assert a & b
