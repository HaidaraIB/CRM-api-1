import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_owner_creates_and_lists_quick_replies(owner_user, company, subscription):
    client = APIClient()
    client.force_authenticate(user=owner_user)
    created = client.post(
        "/api/v1/integrations/quick-replies/",
        {"title": "Hello", "body": "How can I help?"},
        format="json",
    )
    assert created.status_code == 201
    listed = client.get("/api/v1/integrations/quick-replies/")
    assert listed.status_code == 200
    rows = listed.data["data"] if isinstance(listed.data, dict) else listed.data
    assert any(row["title"] == "Hello" for row in rows)
