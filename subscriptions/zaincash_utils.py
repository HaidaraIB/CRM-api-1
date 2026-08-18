"""
Zain Cash Payment Gateway Integration Utilities (Payment Gateway v2)

Zain Cash's v2 gateway is OAuth2-fronted: every call except the token
endpoint itself needs a bearer token obtained via a client_credentials grant
(client_id/client_secret), not the self-signed merchant JWT the old v1 API
used. The live base URL is issued per-merchant during onboarding - there is
no fixed "https://api.zaincash.iq" to guess, unlike the UAT sandbox which is
fixed at https://pg-api-uat.zaincash.iq.
"""

import logging
import uuid
from urllib.parse import urlencode

import jwt
import requests
from django.core.cache import cache
from django.db.models import Q

from .models import PaymentGateway, PaymentGatewayStatus
from .services.fx import usd_to_iqd

logger = logging.getLogger(__name__)

UAT_BASE_URL = "https://pg-api-uat.zaincash.iq"
_TOKEN_SCOPE = "payment:read payment:write reverse:write"
_TOKEN_CACHE_PREFIX = "zaincash_access_token:"


def get_zaincash_gateway():
    """Get active Zain Cash payment gateway"""
    try:
        # Search for various name patterns: "zaincash", "zain cash", "zain-cash"
        gateway = PaymentGateway.objects.filter(
            Q(name__icontains="zaincash") | Q(name__icontains="zain cash") | Q(name__icontains="zain-cash"),
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        ).first()
        return gateway
    except Exception:
        return None


def _base_url(config: dict) -> str:
    """Resolve the API base URL for this gateway's configured environment."""
    environment = config.get("environment", "test")
    configured = (config.get("baseUrl") or "").strip()
    if environment == "live":
        if not configured:
            raise ValueError(
                "Zain Cash live base URL not configured - it is issued per-merchant "
                "during onboarding and must be set in the gateway config"
            )
        return configured.rstrip("/")
    return (configured or UAT_BASE_URL).rstrip("/")


def _get_access_token(config: dict) -> str:
    """
    OAuth2 client_credentials access token, cached until shortly before expiry.

    Every authenticated v2 call needs this bearer token; requesting a fresh one
    per call would work but is unnecessary load, so it's cached per
    (base_url, client_id) for `expires_in` minus a safety margin.
    """
    client_id = (config.get("clientId") or "").strip()
    client_secret = (config.get("clientSecret") or "").strip()
    if not client_id or not client_secret:
        raise ValueError("Zain Cash credentials not configured")

    base_url = _base_url(config)
    cache_key = f"{_TOKEN_CACHE_PREFIX}{base_url}:{client_id}"
    token = cache.get(cache_key)
    if token:
        return token

    try:
        response = requests.post(
            f"{base_url}/oauth2/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": _TOKEN_SCOPE,
                }
            ),
            timeout=30,
        )
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        error_detail = str(e)
        try:
            error_data = e.response.json()
            error_detail = (
                error_data.get("error_description")
                or error_data.get("error")
                or error_data.get("message")
                or str(e)
            )
        except (ValueError, AttributeError, TypeError):
            logger.debug("Zain Cash token error body was not JSON", exc_info=True)
        raise Exception(f"Zain Cash authentication error: {error_detail}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Zain Cash authentication error: {str(e)}")

    result = response.json()
    token = result.get("access_token")
    if not token:
        raise ValueError(f"Zain Cash did not return an access token. Response: {result}")

    expires_in = int(result.get("expires_in") or 3600)
    cache.set(cache_key, token, timeout=max(expires_in - 60, 60))
    return token


def create_zaincash_payment_session(
    amount: float,
    customer_email: str,
    customer_name: str,
    customer_phone: str,
    subscription_id: str,
    return_url: str,
    success_url: str = "",
    failure_url: str = "",
):
    """
    Create a payment session with Zain Cash (v2: POST .../transaction/init)

    Args:
        amount: Payment amount
        customer_email: Customer email (unused by Zain Cash; kept for signature parity with other gateways)
        customer_name: Customer name (unused by Zain Cash; kept for signature parity with other gateways)
        customer_phone: Customer's wallet phone number (optional - Zain Cash
            prompts for it on the payment page when omitted)
        subscription_id: Unique subscription ID
        return_url: Fallback redirect URL used for both success and failure when
            success_url/failure_url are not given
        success_url: Where to send the customer after a successful payment
        failure_url: Where to send the customer after a failed/cancelled payment

    Returns:
        dict: Response from Zain Cash API containing payment URL and transaction ID
    """
    zaincash_gateway = get_zaincash_gateway()

    if not zaincash_gateway:
        raise ValueError("Zain Cash payment gateway not found or not active")

    config = zaincash_gateway.config or {}
    access_token = _get_access_token(config)
    base_url = _base_url(config)

    # Plans are priced in USD; Zain Cash settles in IQD, which has no minor unit
    # in practice, so the amount is whole dinars.
    amount_iqd = int(usd_to_iqd(amount))
    logger.info("Zain Cash: converting USD %s to IQD %s", amount, amount_iqd)

    # Zain Cash minimum amount is typically 1000 IQD
    if amount_iqd < 1000:
        raise ValueError(f"Amount {amount_iqd} IQD is below minimum of 1000 IQD")

    success_url = success_url or return_url
    failure_url = failure_url or return_url

    payload = {
        "language": "ar",
        "externalReferenceId": str(uuid.uuid4()),
        "orderId": f"SUB-{subscription_id}",
        "serviceType": config.get("serviceType", "Subscription"),
        "amount": {"value": amount_iqd, "currency": "IQD"},
        "redirectUrls": {
            "successUrl": success_url,
            "failureUrl": failure_url,
        },
    }
    phone = (customer_phone or "").strip()
    if phone:
        payload["customer"] = {"phone": phone}

    api_url = f"{base_url}/api/v2/payment-gateway/transaction/init"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    try:
        response = requests.post(api_url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()

        logger.info(f"Zain Cash API response: {result}")

        details = result.get("transactionDetails") or {}
        transaction_id = details.get("transactionId") or result.get("transactionId")
        if not transaction_id:
            raise ValueError(f"Zain Cash did not return transaction ID. Response: {result}")

        payment_url = result.get("redirectUrl")
        if not payment_url:
            raise ValueError(f"Zain Cash did not return a redirect URL. Response: {result}")

        return {
            "id": transaction_id,
            "payment_url": payment_url,
            "transaction_id": transaction_id,
        }
    except requests.exceptions.HTTPError as e:
        error_detail = str(e)
        try:
            error_data = e.response.json()
            error_detail = (
                error_data.get("message")
                or error_data.get("error")
                or error_data.get("errorCode")
                or str(e)
            )
        except (ValueError, AttributeError, TypeError):
            logger.debug("Zain Cash error body was not JSON", exc_info=True)
        raise Exception(f"Zain Cash API error: {error_detail}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Zain Cash API error: {str(e)}")


def verify_zaincash_payment(token: str):
    """
    Verify a Zain Cash redirect/webhook JWT by decoding it with the client secret.

    The v2 payload nests the transaction under "data" (eventType/data.transactionId/
    data.currentStatus). This is flattened to {"id", "status", ...} so callers
    (the gateway adapter, tests) keep working with a stable shape regardless of
    Zain Cash's wire format.

    Args:
        token: JWT token returned from Zain Cash callback

    Returns:
        dict: {"id": transaction_id, "status": lowercase_status, "raw": decoded}
    """
    if not token or token.count('.') != 2:
        raise ValueError(f"Invalid JWT token format. Expected 3 segments separated by dots, got: {token[:50]}...")

    zaincash_gateway = get_zaincash_gateway()

    if not zaincash_gateway:
        raise ValueError("Zain Cash payment gateway not found or not active")

    config = zaincash_gateway.config or {}
    client_secret = (config.get("clientSecret") or "").strip()

    if not client_secret:
        raise ValueError("Zain Cash credentials not configured")

    try:
        decoded = jwt.decode(token, client_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise Exception("Zain Cash token has expired")
    except jwt.InvalidTokenError as e:
        raise Exception(f"Invalid Zain Cash token: {str(e)}")

    data = decoded.get("data") if isinstance(decoded.get("data"), dict) else decoded
    transaction_id = data.get("transactionId") or decoded.get("id")
    status_value = (data.get("currentStatus") or decoded.get("status") or "").lower()

    return {"id": transaction_id, "status": status_value, "raw": decoded}


def check_zaincash_payment_status(transaction_id: str, msisdn: str = ""):
    """
    Check the status of a Zain Cash payment via the Inquiry API (v2).

    Args:
        transaction_id: The transaction ID returned from Zain Cash
        msisdn: Unused (kept for call-site compatibility; v2 inquiry needs
            only the transaction ID and a bearer token)

    Returns:
        dict: {"status": lowercase_status, "raw": full_response}
    """
    zaincash_gateway = get_zaincash_gateway()

    if not zaincash_gateway:
        raise ValueError("Zain Cash payment gateway not found or not active")

    config = zaincash_gateway.config or {}
    access_token = _get_access_token(config)
    base_url = _base_url(config)

    api_url = f"{base_url}/api/v2/payment-gateway/transaction/inquiry/{transaction_id}"
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        response = requests.get(api_url, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()

        logger.info(f"Zain Cash transaction status check response: {result}")
        status_value = (result.get("status") or "").lower()
        return {"status": status_value, "raw": result}
    except requests.exceptions.HTTPError as e:
        error_detail = str(e)
        try:
            error_data = e.response.json()
            error_detail = (
                error_data.get("message")
                or error_data.get("error")
                or error_data.get("errorCode")
                or str(e)
            )
        except (ValueError, AttributeError, TypeError):
            logger.debug("Zain Cash error body was not JSON", exc_info=True)
        raise Exception(f"Zain Cash status check error: {error_detail}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Zain Cash status check error: {str(e)}")


def test_zaincash_credentials(client_id: str, client_secret: str, environment: str = "test", base_url: str = ""):
    """
    Test Zain Cash credentials by actually requesting an OAuth2 access token.

    This exercises the same auth path production calls use, unlike the old
    v1 "send a throwaway transaction/init and treat any response as success"
    check, which could report success for credentials that don't work.

    Args:
        client_id: Client ID to test
        client_secret: Client Secret to test
        environment: 'test' or 'live'
        base_url: API base URL. Required for 'live' (issued during onboarding);
            defaults to the UAT sandbox for 'test'.

    Returns:
        dict: Test result with success status and message
    """
    try:
        client_id = (client_id or "").strip()
        client_secret = (client_secret or "").strip()

        if not client_id or not client_secret:
            return {
                "success": False,
                "message": "Client ID and Client Secret are required",
            }

        resolved_base = (base_url or "").strip()
        if environment == "live":
            if not resolved_base:
                return {
                    "success": False,
                    "message": "Live base URL is required (issued by Zain Cash during onboarding)",
                }
        else:
            resolved_base = resolved_base or UAT_BASE_URL
        resolved_base = resolved_base.rstrip("/")

        try:
            response = requests.post(
                f"{resolved_base}/oauth2/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=urlencode(
                    {
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "scope": _TOKEN_SCOPE,
                    }
                ),
                timeout=10,
            )
        except requests.exceptions.Timeout:
            return {
                "success": False,
                "message": "Connection timeout - please check your network connection",
            }
        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "message": "Cannot connect to Zain Cash API - please check your network",
            }
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "message": f"Connection error: {str(e)}",
            }

        if response.status_code == 200:
            result = response.json()
            if result.get("access_token"):
                return {
                    "success": True,
                    "message": "Credentials are valid and connection successful",
                }
            return {
                "success": False,
                "message": f"Unexpected response from Zain Cash: {result}",
            }

        error_msg = f"API returned status {response.status_code}"
        try:
            error_data = response.json()
            error_msg = (
                error_data.get("error_description")
                or error_data.get("error")
                or error_data.get("message")
                or error_msg
            )
        except (ValueError, AttributeError, TypeError):
            logger.debug("Zain Cash error body was not JSON", exc_info=True)
        return {
            "success": False,
            "message": f"Invalid credentials: {error_msg}",
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"Test failed: {str(e)}",
        }
