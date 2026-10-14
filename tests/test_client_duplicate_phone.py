"""Repeating a phone on client save must not 500 on unique_client_phone_number."""

import pytest

from crm.models import Client, ClientPhoneNumber


@pytest.mark.django_db
def test_patch_client_with_repeated_phone_keeps_one_row(authenticated_admin, company):
    client = Client.objects.create(
        name="Phone lead",
        company=company,
        priority="low",
        type="cold",
    )
    response = authenticated_admin.patch(
        f"/api/v1/clients/{client.id}/",
        {
            "phone_numbers": [
                {"phone_number": "+9647725820300", "phone_type": "mobile", "is_primary": True, "client": 1},
                {"phone_number": "+9647725820300", "phone_type": "work", "is_primary": False},
            ]
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    rows = list(ClientPhoneNumber.objects.filter(client=client))
    assert len(rows) == 1
    assert rows[0].phone_number == "+9647725820300"
    assert rows[0].is_primary is True
    assert rows[0].phone_type == "mobile"
