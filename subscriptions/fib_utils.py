"""
FIB (First Iraqi Bank) Web Payments Gateway Integration Utilities
Documentation: https://fib.iq/integrations/web-payments/
"""

import logging
import requests
from urllib.parse import urlencode
from django.conf import settings
from .models import PaymentGateway, PaymentGatewayStatus
from settings.models import SystemSettings

logger = logging.getLogger(__name__)

# FIB Sandbox (stage) and production base URLs
FIB_STAGE_BASE = "https://fib.stage.fib.iq"
FIB_AUTH_PATH = "/auth/realms/fib-online-shop/protocol/openid-connect/token"
FIB_PAYMENTS_PATH = "/protected/v1/payments"


def get_fib_gateway():
    """Get active FIB payment gateway"""
    try:
        from django.db.models import Q
        gateway = PaymentGateway.objects.filter(
            Q(name__icontains="fib") | Q(name__icontains="first iraqi"),
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        ).first()
        return gateway
    except Exception:
        return None


def get_fib_access_token(config: dict) -> str:
    """
    Get OAuth2 access token using client_credentials grant.
    Token is short-lived (e.g. 60s); call this before each protected request if needed.
    """
    client_id = (config.get("clientId") or "").strip()
    client_secret = (config.get("clientSecret") or "").strip()
    if not client_id or not client_secret:
        raise ValueError("FIB client_id and client_secret are required")

    environment = config.get("environment", "test")
    base_url = FIB_STAGE_BASE if environment == "test" else config.get("baseUrl", "https://fib.fib.iq").rstrip("/")

    token_url = f"{base_url}{FIB_AUTH_PATH}"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    response = requests.post(
        token_url,
        data=urlencode(data),
        headers=headers,
        timeout=15,
    )
    response.raise_for_status()
    result = response.json()
    access_token = result.get("access_token")
    if not access_token:
        raise ValueError("FIB did not return access_token")
    return access_token


def create_fib_payment_session(
    amount: float,
    customer_email: str,
    customer_name: str,
    subscription_id: str,
    callback_url: str,
    description: str = "",
):
    """
    Create a FIB payment and get QR code and app links.

    Args:
        amount: Payment amount (in USD; will be converted to IQD using SystemSettings)
        customer_email: Customer email
        customer_name: Customer name
        subscription_id: Subscription ID for reference
        callback_url: URL FIB will POST to when payment status changes (id, status)
        description: Optional description (max 50 chars)

    Returns:
        dict: paymentId, qrCode, readableCode, businessAppLink, corporateAppLink,
              personalAppLink, validUntil
    """
    fib_gateway = get_fib_gateway()
    if not fib_gateway:
        raise ValueError("FIB payment gateway not found or not active")

    config = fib_gateway.config or {}
    environment = config.get("environment", "test")
    base_url = FIB_STAGE_BASE if environment == "test" else config.get("baseUrl", "https://fib.fib.iq").rstrip("/")

    # Convert USD to IQD if amount looks like USD
    try:
        system_settings = SystemSettings.get_settings()
        usd_to_iqd = float(system_settings.usd_to_iqd_rate)
    except Exception as e:
        logger.warning("Failed to get usd_to_iqd_rate, using 1300: %s", e)
        usd_to_iqd = 1300.0

    if amount < 1000:
        amount_iqd = amount * usd_to_iqd
        logger.info("FIB: converting USD %s to IQD %s (rate: %s)", amount, amount_iqd, usd_to_iqd)
    else:
        amount_iqd = amount

    amount_iqd = round(float(amount_iqd), 2)
    amount_str = f"{amount_iqd:.2f}"

    token = get_fib_access_token(config)
    payments_url = f"{base_url}{FIB_PAYMENTS_PATH}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "monetaryValue": {
            "amount": amount_str,
            "currency": "IQD",
        },
        "statusCallbackUrl": callback_url,
        "description": (description or f"Subscription {subscription_id}")[:50],
    }

    response = requests.post(
        payments_url,
        json=body,
        headers=headers,
        timeout=30,
    )

    if response.status_code == 406:
        try:
            err = response.json()
            raise ValueError(err.get("message", "Payment not accepted (406)"))
        except ValueError:
            raise
    response.raise_for_status()

    if response.status_code != 202:
        raise ValueError(f"FIB create payment unexpected status: {response.status_code}")

    result = response.json()
    payment_id = result.get("paymentId")
    if not payment_id:
        raise ValueError(f"FIB did not return paymentId: {result}")

    logger.info("FIB payment created: paymentId=%s, subscription_id=%s", payment_id, subscription_id)
    return {
        "paymentId": payment_id,
        "qrCode": result.get("qrCode"),
        "readableCode": result.get("readableCode"),
        "businessAppLink": result.get("businessAppLink"),
        "corporateAppLink": result.get("corporateAppLink"),
        "personalAppLink": result.get("personalAppLink"),
        "validUntil": result.get("validUntil"),
    }


def check_fib_payment_status(payment_id: str) -> dict:
    """
    Check FIB payment status by payment ID.
    Returns: status (PAID | UNPAID | DECLINED), paidAt, amount, decliningReason, etc.
    """
    fib_gateway = get_fib_gateway()
    if not fib_gateway:
        raise ValueError("FIB payment gateway not found or not active")

    config = fib_gateway.config or {}
    environment = config.get("environment", "test")
    base_url = FIB_STAGE_BASE if environment == "test" else config.get("baseUrl", "https://fib.fib.iq").rstrip("/")

    token = get_fib_access_token(config)
    url = f"{base_url}{FIB_PAYMENTS_PATH}/{payment_id}/status"
    headers = {"Authorization": f"Bearer {token}"}

    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    return response.json()


def test_fib_credentials(client_id: str, client_secret: str, environment: str = "test"):
    """
    Test FIB credentials by obtaining an access token.
    """
    try:
        client_id = (client_id or "").strip()
        client_secret = (client_secret or "").strip()
        if not client_id or not client_secret:
            return {"success": False, "message": "Client ID and Client Secret are required"}

        base_url = FIB_STAGE_BASE if environment == "test" else "https://fib.fib.iq"
        token_url = f"{base_url.rstrip('/')}{FIB_AUTH_PATH}"
        data = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        response = requests.post(
            token_url,
            data=urlencode(data),
            headers=headers,
            timeout=10,
        )

        if response.status_code == 200:
            return {"success": True, "message": "Credentials are valid and connection successful"}
        try:
            err = response.json()
            msg = err.get("error_description") or err.get("error") or response.text
        except Exception:
            msg = response.text or f"HTTP {response.status_code}"
        return {"success": False, "message": f"Authentication failed: {msg}"}
    except requests.exceptions.Timeout:
        return {"success": False, "message": "Connection timeout"}
    except requests.exceptions.RequestException as e:
        return {"success": False, "message": f"Connection error: {str(e)}"}
    except Exception as e:
        return {"success": False, "message": str(e)}
