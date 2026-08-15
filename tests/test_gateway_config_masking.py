"""
PaymentGateway.config must never leave the server readable.

The admin panel prefills its settings form from the config it reads back and
posts the whole form again, so masking on read is only safe if writes drop the
values that came back still masked. These tests pin both halves.
"""
import pytest

from subscriptions.gateway_config import (
    MASK_CHAR,
    is_secret_key,
    mask_gateway_config,
    merge_config_for_write,
    strip_masked_values,
)
from subscriptions.models import PaymentGateway, PaymentGatewayStatus
from subscriptions.serializers import PaymentGatewaySerializer


class TestSecretClassification:
    @pytest.mark.parametrize(
        "key",
        ["secretKey", "serverKey", "webhookSecret", "merchantSecret",
         "clientSecret", "password", "apiKey", "merchant_secret"],
    )
    def test_credentials_are_secret(self, key):
        assert is_secret_key(key) is True

    @pytest.mark.parametrize(
        "key",
        ["publishableKey", "publicKey", "clientId", "profileId",
         "terminalId", "username", "environment", "merchantId", "msisdn"],
    )
    def test_identifiers_are_not_secret(self, key):
        assert is_secret_key(key) is False


class TestMasking:
    def test_secret_is_masked_keeping_last_four(self):
        masked = mask_gateway_config({"secretKey": "sk_live_abcd1234"})
        assert masked["secretKey"].endswith("1234")
        assert "sk_live" not in masked["secretKey"]
        assert MASK_CHAR in masked["secretKey"]

    def test_short_secret_reveals_nothing(self):
        masked = mask_gateway_config({"password": "abc"})
        assert masked["password"] == MASK_CHAR * 8

    def test_identifiers_pass_through(self):
        masked = mask_gateway_config(
            {"environment": "live", "terminalId": "T-1", "publishableKey": "pk_live_xyz"}
        )
        assert masked == {
            "environment": "live",
            "terminalId": "T-1",
            "publishableKey": "pk_live_xyz",
        }

    def test_empty_values_are_left_alone(self):
        assert mask_gateway_config({"secretKey": ""}) == {"secretKey": ""}


class TestWriteMerge:
    def test_masked_echo_does_not_overwrite_stored_secret(self):
        stored = {"secretKey": "sk_live_abcd1234", "environment": "test"}
        echoed = mask_gateway_config(stored)
        merged = merge_config_for_write(stored, echoed)
        assert merged["secretKey"] == "sk_live_abcd1234"

    def test_real_edit_is_applied(self):
        stored = {"secretKey": "sk_live_old0000", "environment": "test"}
        incoming = dict(mask_gateway_config(stored), secretKey="sk_live_new1111")
        merged = merge_config_for_write(stored, incoming)
        assert merged["secretKey"] == "sk_live_new1111"
        assert merged["environment"] == "test"

    def test_unrelated_keys_survive_partial_submit(self):
        stored = {"secretKey": "sk_1", "webhookSecret": "whsec_1"}
        merged = merge_config_for_write(stored, {"environment": "live"})
        assert merged["secretKey"] == "sk_1"
        assert merged["webhookSecret"] == "whsec_1"
        assert merged["environment"] == "live"

    def test_strip_masked_values_drops_only_masked(self):
        assert strip_masked_values(
            {"a": f"{MASK_CHAR * 8}1234", "b": "real"}
        ) == {"b": "real"}


@pytest.mark.django_db
class TestSerializerRoundTrip:
    @pytest.fixture
    def gateway(self):
        return PaymentGateway.objects.create(
            name="Stripe",
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
            config={
                "secretKey": "sk_live_abcd1234",
                "publishableKey": "pk_live_readable",
                "webhookSecret": "whsec_secret9999",
                "environment": "live",
            },
        )

    def test_read_never_exposes_secrets(self, gateway):
        data = PaymentGatewaySerializer(gateway).data
        serialized = str(data["config"])
        assert "sk_live_abcd1234" not in serialized
        assert "whsec_secret9999" not in serialized
        # identifiers stay readable so the operator can confirm the account
        assert data["config"]["publishableKey"] == "pk_live_readable"
        assert data["config"]["environment"] == "live"

    def test_resubmitting_a_read_preserves_secrets(self, gateway):
        read_back = PaymentGatewaySerializer(gateway).data
        serializer = PaymentGatewaySerializer(
            gateway, data={"config": read_back["config"]}, partial=True
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        gateway.refresh_from_db()
        assert gateway.config["secretKey"] == "sk_live_abcd1234"
        assert gateway.config["webhookSecret"] == "whsec_secret9999"

    def test_editing_one_secret_leaves_the_others(self, gateway):
        read_back = PaymentGatewaySerializer(gateway).data
        submitted = dict(read_back["config"], secretKey="sk_live_rotated5678")
        serializer = PaymentGatewaySerializer(
            gateway, data={"config": submitted}, partial=True
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        gateway.refresh_from_db()
        assert gateway.config["secretKey"] == "sk_live_rotated5678"
        assert gateway.config["webhookSecret"] == "whsec_secret9999"
        assert gateway.config["environment"] == "live"
