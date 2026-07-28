"""
Paytabs Payment Gateway Integration Utilities
Plans are priced in USD; PayTabs uses IQD, so we convert using SystemSettings.usd_to_iqd_rate.
"""

import logging
import requests
import json
from urllib.parse import urlparse
from django.conf import settings
from .models import PaymentGateway, PaymentGatewayStatus
from settings.models import SystemSettings

logger = logging.getLogger(__name__)


def get_paytabs_gateway():
    """Get active Paytabs payment gateway"""
    try:
        gateway = PaymentGateway.objects.filter(
            name__icontains="paytabs",
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        ).first()
        return gateway
    except Exception:
        return None


def _is_paytabs_public_callback_url(url: str | None) -> bool:
    """
    PayTabs callback must be public and reachable:
    - no localhost / private hosts
    - no port numbers
    - https preferred (required for reliable IPN in practice)
    """
    if not url or not str(url).strip():
        return False
    try:
        parsed = urlparse(str(url).strip())
    except Exception:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host or "." not in host:
        return False
    if host in ("localhost", "127.0.0.1") or host.endswith(".local"):
        return False
    if parsed.port is not None:
        return False
    return True


def create_paytabs_payment_session(
    amount: float,
    customer_email: str,
    customer_name: str,
    customer_phone: str,
    subscription_id: str,
    return_url: str,
    callback_url: str | None = None,
):
    """
    Create a payment session with Paytabs.
    Plan prices are in USD; PayTabs expects IQD. We convert using SystemSettings.usd_to_iqd_rate.
    Other gateways are unaffected (they use their own currency logic).

    Args:
        amount: Payment amount in USD (from plan price)
        customer_email: Customer email
        customer_name: Customer name
        customer_phone: Customer phone
        subscription_id: Unique subscription ID
        return_url: URL to redirect customer after payment
        callback_url: Server-to-server IPN/callback URL (optional; omitted if not publicly reachable)

    Returns:
        dict: Response from Paytabs API containing payment URL
    """
    paytabs_gateway = get_paytabs_gateway()

    config = paytabs_gateway.config or {}
    profile_id = config.get("profileId")
    server_key = config.get("serverKey")

    if not profile_id or not server_key:
        raise ValueError("Paytabs credentials not configured")

    server_key = server_key.strip()
    profile_id = int(profile_id)

    # Convert USD (plan price) to IQD using super admin exchange rate. PayTabs only — other gateways unchanged.
    try:
        system_settings = SystemSettings.get_settings()
        usd_to_iqd_rate = float(system_settings.usd_to_iqd_rate)
    except Exception as e:
        logger.warning("Failed to get usd_to_iqd_rate from SystemSettings, using default 1300: %s", e)
        usd_to_iqd_rate = 1300.0
    amount_iqd = round(amount * usd_to_iqd_rate, 2)
    logger.info("PayTabs: converting USD %s to IQD %s (rate: %s)", amount, amount_iqd, usd_to_iqd_rate)

    # Prepare payment data (cart_amount in IQD)
    payment_data = {
        "profile_id": profile_id,
        "tran_type": "sale",
        "tran_class": "ecom",
        "cart_id": f"SUB-{subscription_id}",
        "cart_currency": "IQD",
        "cart_amount": amount_iqd,
        "cart_description": f"Subscription payment - Subscription {subscription_id}",
        "customer_details": {
            "name": customer_name,
            "email": customer_email,
            "phone": customer_phone,
            "street1": "",
            "city": "",
            "state": "",
            "country": "IQ",
        },
        "shipping_details": {
            "name": customer_name,
            "email": customer_email,
            "phone": customer_phone,
            "street1": "",
            "city": "",
            "state": "",
            "country": "IQ",
        },
        "return": return_url,
    }
    if _is_paytabs_public_callback_url(callback_url):
        payment_data["callback"] = callback_url
    elif callback_url:
        logger.warning(
            "PayTabs callback omitted (must be public HTTPS without port); got %s",
            callback_url,
        )

    # Make API request
    api_url = f"{settings.PAYTABS_DOMAIN}/payment/request"
    headers = {
        "authorization": server_key,  # lowercase as per Paytabs docs
        "content-type": "application/octet-stream",
    }
    try:
        response = requests.post(
            api_url,
            data=json.dumps(payment_data),
            headers=headers,
            timeout=30,
        )
        if not response.ok:
            detail = response.text
            try:
                detail = response.json()
            except Exception:
                pass
            logger.error(
                "PayTabs payment/request failed status=%s detail=%s",
                response.status_code,
                detail,
            )
            raise Exception(
                f"Paytabs API error: {response.status_code} {detail}"
            )
        return response.json()
    except requests.exceptions.RequestException as e:
        body = ""
        if getattr(e, "response", None) is not None:
            body = e.response.text
            logger.error("PayTabs request exception body=%s", body)
        raise Exception(f"Paytabs API error: {str(e)}" + (f" | {body}" if body else ""))


def verify_paytabs_payment(transaction_ref: str):
    """
    Verify a Paytabs payment transaction

    Args:
        transaction_ref: Transaction reference from Paytabs
        gateway: PaymentGateway instance (optional)

    Returns:
        dict: Payment verification response
    """
    logger.info("Verifying PayTabs payment with transaction_ref: %s", transaction_ref)

    paytabs_gateway = get_paytabs_gateway()

    if not paytabs_gateway:
        logger.error("PayTabs gateway not found or not active")
        raise ValueError("Paytabs gateway not found or not active")

    config = paytabs_gateway.config or {}
    profile_id = config.get("profileId")
    server_key = config.get("serverKey")

    if not profile_id or not server_key:
        logger.error("PayTabs credentials not configured in gateway config")
        raise ValueError("Paytabs credentials not configured")

    server_key = server_key.strip()
    profile_id = int(profile_id)

    logger.info("Using PayTabs profile_id: %s", profile_id)

    api_url = f"{settings.PAYTABS_DOMAIN}/payment/query"
    query_data = {
        "profile_id": profile_id,
        "tran_ref": transaction_ref,
    }
    headers = {"authorization": server_key, "content-type": "application/octet-stream"}

    logger.info("Sending verification request to: %s", api_url)

    try:
        verify_response = requests.post(
            api_url,
            data=json.dumps(query_data),
            headers=headers,
            timeout=30,
        )
        logger.info("PayTabs API response status: %s", verify_response.status_code)

        verify_response.raise_for_status()
        result = verify_response.json()
        return result
    except requests.exceptions.RequestException as e:
        logger.error("PayTabs verification error: %s", e)
        if getattr(e, "response", None) is not None:
            logger.error("Response body: %s", e.response.text)
        raise Exception(f"Paytabs verification error: {str(e)}")
