"""Phone matching for PBX / SMS lead lookup."""

import pytest
from django.db import IntegrityError

from crm.models import Client, ClientPhoneNumber
from integrations.services.phone_match import (
    canonical_phone_key,
    find_client_by_phone,
    phone_match_keys,
)


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


def test_canonical_phone_key_iraq_local_vs_e164():
    assert canonical_phone_key("07812113063") == "9647812113063"
    assert canonical_phone_key("+9647812113063") == "9647812113063"
    assert canonical_phone_key("unknown") == ""


@pytest.mark.django_db
def test_company_phone_normalized_unique_constraint(company):
    c1 = Client.objects.create(
        name="A", company=company, priority="low", type="fresh", phone_number="+9647812113063"
    )
    ClientPhoneNumber.objects.create(
        client=c1, phone_number="+9647812113063", phone_type="mobile", is_primary=True
    )
    c2 = Client.objects.create(
        name="B", company=company, priority="low", type="fresh", phone_number="07812113063"
    )
    with pytest.raises(IntegrityError):
        ClientPhoneNumber.objects.create(
            client=c2, phone_number="07812113063", phone_type="mobile", is_primary=True
        )


@pytest.mark.django_db
def test_find_client_by_phone_uses_normalized_key(company):
    c1 = Client.objects.create(
        name="A", company=company, priority="low", type="fresh", phone_number="+9647812113063"
    )
    ClientPhoneNumber.objects.create(
        client=c1, phone_number="+9647812113063", phone_type="mobile", is_primary=True
    )
    found = find_client_by_phone(company, "07812113063")
    assert found is not None
    assert found.id == c1.id
