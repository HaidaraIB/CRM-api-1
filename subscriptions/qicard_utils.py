"""
QiCard Payment Gateway Integration Utilities
"""

import base64
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

import requests

from .models import PaymentGateway, PaymentGatewayStatus
from settings.models import SystemSettings

logger = logging.getLogger(__name__)

DEFAULT_TEST_API_BASE = "https://uat-sandbox-3ds-api.qi.iq"
DEFAULT_LIVE_API_BASE = "https://api.qi.iq"


@dataclass(frozen=True)
class QicardClient:
    api_base_url: str
    terminal_id: str
    headers: dict


def get_qicard_gateway():
    """Get active QiCard payment gateway"""
    try:
        from django.db.models import Q

        gateway = PaymentGateway.objects.filter(
            Q(name__icontains="qicard")
            | Q(name__icontains="qi card")
            | Q(name__icontains="qi-card"),
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        ).first()
        return gateway
    except Exception:
        return None


def _api_base_url(config: dict) -> str:
    environment = config.get("environment", "test")
    if environment == "live":
        custom = (config.get("apiBaseUrl") or "").strip().rstrip("/")
        return custom or DEFAULT_LIVE_API_BASE
    return DEFAULT_TEST_API_BASE.rstrip("/")


def _round_amount(amount) -> float:
    return float(
        Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def _qicard_client() -> QicardClient:
    qicard_gateway = get_qicard_gateway()
    if not qicard_gateway:
        raise ValueError("QiCard payment gateway not found or not active")

    config = qicard_gateway.config or {}
    terminal_id = (config.get("terminalId") or "").strip()
    username = (config.get("username") or "").strip()
    password = (config.get("password") or "").strip()

    if not terminal_id or not username or not password:
        raise ValueError("QiCard credentials not configured")

    credentials = f"{username}:{password}"
    encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {encoded_credentials}",
        "X-Terminal-Id": terminal_id,
    }
    return QicardClient(
        api_base_url=_api_base_url(config),
        terminal_id=terminal_id,
        headers=headers,
    )


def _qicard_client_from_credentials(
    terminal_id: str, username: str, password: str, environment: str = "test", api_base_url: str = ""
) -> QicardClient:
    terminal_id = terminal_id.strip()
    username = username.strip()
    password = password.strip()
    config = {"environment": environment}
    if api_base_url:
        config["apiBaseUrl"] = api_base_url.strip().rstrip("/")
    credentials = f"{username}:{password}"
    encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {encoded_credentials}",
        "X-Terminal-Id": terminal_id,
    }
    return QicardClient(
        api_base_url=_api_base_url(config),
        terminal_id=terminal_id,
        headers=headers,
    )


def _parse_http_error(exc: requests.exceptions.HTTPError) -> str:
    error_detail = str(exc)
    try:
        error_data = exc.response.json()
        error_obj = error_data.get("error", {})
        error_detail = (
            error_obj.get("description") or error_obj.get("message") or str(exc)
        )
    except (ValueError, AttributeError, TypeError):
        logger.debug("QiCard error body was not JSON", exc_info=True)
    return error_detail


def _usd_to_iqd_amount(amount: float) -> float:
    try:
        system_settings = SystemSettings.get_settings()
        usd_to_iqd_rate = float(system_settings.usd_to_iqd_rate)
    except Exception as e:
        logger.warning(
            "Failed to get USD to IQD rate from database, using default 1300: %s", e
        )
        usd_to_iqd_rate = 1300.0

    if amount < 1000:
        amount_iqd = amount * usd_to_iqd_rate
        logger.info(
            "Converting amount from USD %s to IQD %s (rate: %s)",
            amount,
            amount_iqd,
            usd_to_iqd_rate,
        )
        return _round_amount(amount_iqd)
    return _round_amount(amount)


def create_qicard_payment_session(
    amount: float,
    customer_email: str,
    customer_name: str,
    customer_phone: str,
    subscription_id: str,
    return_url: str,
    notification_url: str,
):
    """
    Create a payment session with QiCard

    Returns:
        dict: payment_id, form_url, request_id
    """
    client = _qicard_client()
    amount_iqd = _usd_to_iqd_amount(amount)
    request_id = str(uuid.uuid4())

    request_body = {
        "requestId": request_id,
        "amount": amount_iqd,
        "currency": "IQD",
        "locale": "ar_IQ",
        "finishPaymentUrl": return_url,
        "notificationUrl": notification_url,
        "customerInfo": {
            "firstName": customer_name.split()[0]
            if customer_name.split()
            else customer_name,
            "lastName": " ".join(customer_name.split()[1:])
            if len(customer_name.split()) > 1
            else "",
            "phone": customer_phone,
            "email": customer_email,
        },
    }

    api_url = f"{client.api_base_url}/api/v1/payment"
    try:
        response = requests.post(
            api_url,
            json=request_body,
            headers=client.headers,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        logger.info("QiCard API response: %s", result)

        payment_id = result.get("paymentId")
        form_url = result.get("formUrl")
        if not payment_id or not form_url:
            raise ValueError(
                f"QiCard did not return payment ID or form URL. Response: {result}"
            )
        return {
            "payment_id": payment_id,
            "form_url": form_url,
            "request_id": request_id,
        }
    except requests.exceptions.HTTPError as e:
        raise Exception(f"QiCard API error: {_parse_http_error(e)}") from e
    except requests.exceptions.RequestException as e:
        raise Exception(f"QiCard API error: {str(e)}") from e


def qicard_refund_confirmed(payload: dict | None) -> bool:
    if not payload:
        return False
    if (payload.get("status") or "").upper() == "SUCCESS":
        return True
    details = payload.get("details") or {}
    if isinstance(details, dict):
        code = str(details.get("resultCode", ""))
        if code in ("00", "0"):
            return True
    return False


def apply_qicard_refund_terminal_status(
    payment,
    error_message: str,
    gateway_payload: dict | None = None,
) -> str:
    """
    After QiCard refuses a refund, move the local row off COMPLETED so admin
    cannot retry indefinitely (e.g. never-paid on gateway vs already refunded).
    """
    from .models import PaymentStatus

    msg = (error_message or "").lower()
    raw: dict = {}
    if gateway_payload:
        raw = dict(gateway_payload)
    elif getattr(payment, "tran_ref", None):
        try:
            raw = verify_qicard_payment(payment.tran_ref) or {}
        except Exception:
            raw = {}

    refunds = raw.get("refunds") or []
    has_successful_refund = any(
        isinstance(entry, dict) and entry.get("successfully") for entry in refunds
    )
    gw_status = (raw.get("status") or "").upper()

    if has_successful_refund:
        new_status = PaymentStatus.REFUNDED.value
    elif "unsuccessful" in msg:
        new_status = PaymentStatus.FAILED.value
    elif "exceed" in msg:
        new_status = PaymentStatus.REFUNDED.value
    elif gw_status and gw_status != "SUCCESS":
        new_status = PaymentStatus.FAILED.value
    else:
        new_status = PaymentStatus.FAILED.value

    meta = dict(payment.session_meta or {})
    meta["qicard_refund_blocked"] = {
        "message": error_message,
        "gateway_status": gw_status or None,
        "gateway": raw,
    }
    payment.payment_status = new_status
    payment.session_meta = meta
    payment.save(update_fields=["payment_status", "session_meta", "updated_at"])
    return new_status


def qicard_refund_amount_iqd(payment) -> float:
    """
    Refund must use the same IQD amount QiCard captured, not Payment.amount (USD).
    """
    meta = payment.session_meta or {}
    stored = meta.get("qicard_amount_iqd")
    if stored is not None:
        return _round_amount(stored)

    tran_ref = getattr(payment, "tran_ref", None)
    if tran_ref:
        try:
            status = verify_qicard_payment(tran_ref) or {}
        except Exception:
            status = {}
        else:
            if status.get("confirmedAmount") is not None:
                return _round_amount(status["confirmedAmount"])
            if (status.get("status") or "").upper() == "SUCCESS" and status.get("amount") is not None:
                return _round_amount(status["amount"])

    base = payment.amount_usd if payment.amount_usd is not None else payment.amount
    return _usd_to_iqd_amount(float(base))


def qicard_cancel_confirmed(payload: dict | None) -> bool:
    """
    QiCard cancel API often returns the payment object with status FORM_SHOWED
    (not SUCCESS) while ``canceled`` is true and ``cancels[].successfully`` is set.
    """
    if not payload:
        return False
    if payload.get("canceled") is True:
        return True
    for entry in payload.get("cancels") or []:
        if isinstance(entry, dict) and entry.get("successfully"):
            return True
    return False


def verify_qicard_payment(payment_id: str):
    """Verify a QiCard payment transaction by checking payment status"""
    client = _qicard_client()
    api_url = f"{client.api_base_url}/api/v1/payment/{payment_id}/status"
    headers = {k: v for k, v in client.headers.items() if k != "Content-Type"}

    try:
        response = requests.get(api_url, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()
        logger.info("QiCard payment status check response: %s", result)
        return result
    except requests.exceptions.HTTPError as e:
        raise Exception(f"QiCard status check error: {_parse_http_error(e)}") from e
    except requests.exceptions.RequestException as e:
        raise Exception(f"QiCard status check error: {str(e)}") from e


def refund_qicard_payment(payment_id: str, amount: float, message: str = ""):
    """Full or partial refund via QiCard API."""
    client = _qicard_client()
    request_id = str(uuid.uuid4())
    body = {
        "requestId": request_id,
        "amount": _round_amount(amount),
    }
    if message:
        body["message"] = message

    api_url = f"{client.api_base_url}/api/v1/payment/{payment_id}/refund"
    try:
        response = requests.post(
            api_url,
            json=body,
            headers=client.headers,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        logger.info("QiCard refund response payment_id=%s", payment_id)
        return result
    except requests.exceptions.HTTPError as e:
        raise Exception(f"QiCard refund error: {_parse_http_error(e)}") from e
    except requests.exceptions.RequestException as e:
        raise Exception(f"QiCard refund error: {str(e)}") from e


def cancel_qicard_payment(payment_id: str, amount: float | None = None):
    """Cancel a QiCard payment (full amount when amount omitted)."""
    client = _qicard_client()
    request_id = str(uuid.uuid4())
    body = {"requestId": request_id}
    if amount is not None:
        body["amount"] = _round_amount(amount)

    api_url = f"{client.api_base_url}/api/v1/payment/{payment_id}/cancel"
    try:
        response = requests.post(
            api_url,
            json=body,
            headers=client.headers,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        logger.info("QiCard cancel response payment_id=%s", payment_id)
        return result
    except requests.exceptions.HTTPError as e:
        raise Exception(f"QiCard cancel error: {_parse_http_error(e)}") from e
    except requests.exceptions.RequestException as e:
        raise Exception(f"QiCard cancel error: {str(e)}") from e


def test_qicard_credentials(
    terminal_id: str, username: str, password: str, environment: str = "test"
):
    """
    Test QiCard credentials by attempting to create a minimal test payment request
    """
    try:
        terminal_id = terminal_id.strip()
        username = username.strip()
        password = password.strip()

        if not terminal_id or not username or not password:
            return {
                "success": False,
                "message": "Terminal ID, Username, and Password are required",
            }

        client = _qicard_client_from_credentials(
            terminal_id, username, password, environment
        )

        test_request_id = str(uuid.uuid4())
        test_payload = {
            "requestId": test_request_id,
            "amount": 0.01,
            "currency": "IQD",
            "locale": "ar_IQ",
            "finishPaymentUrl": "https://example.com/test",
            "notificationUrl": "https://example.com/test",
        }

        api_url = f"{client.api_base_url}/api/v1/payment"

        try:
            response = requests.post(
                api_url,
                json=test_payload,
                headers=client.headers,
                timeout=10,
            )

            if response.status_code == 401:
                try:
                    error_data = response.json()
                    error_obj = error_data.get("error", {})
                    error_msg = (
                        error_obj.get("description")
                        or error_obj.get("message")
                        or "Authentication failed"
                    )
                except (ValueError, AttributeError, TypeError):
                    error_msg = "Authentication failed"
                return {
                    "success": False,
                    "message": f"Invalid credentials: {error_msg}",
                }
            elif response.status_code == 200:
                return {
                    "success": True,
                    "message": "Credentials are valid and connection successful",
                }
            elif response.status_code == 400:
                try:
                    error_data = response.json()
                    error_obj = error_data.get("error", {})
                    error_code = error_obj.get("code")
                    if error_code == 27:
                        error_msg = error_obj.get("description") or "Invalid credentials"
                        return {
                            "success": False,
                            "message": f"Invalid credentials: {error_msg}",
                        }
                    return {
                        "success": True,
                        "message": "Credentials appear valid (authentication successful)",
                    }
                except (ValueError, AttributeError, TypeError):
                    return {
                        "success": True,
                        "message": "Credentials appear valid (authentication successful)",
                    }
            else:
                try:
                    error_data = response.json()
                    error_obj = error_data.get("error", {})
                    error_msg = (
                        error_obj.get("description")
                        or error_obj.get("message")
                        or f"API returned status {response.status_code}"
                    )
                except (ValueError, AttributeError, TypeError):
                    error_msg = f"API returned status {response.status_code}"

                if 500 <= response.status_code < 600:
                    return {
                        "success": True,
                        "message": "Credentials appear valid (server error, not authentication)",
                    }
                return {
                    "success": False,
                    "message": f"Connection failed: {error_msg}",
                }
        except requests.exceptions.Timeout:
            return {
                "success": False,
                "message": "Connection timeout - please check your network connection",
            }
        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "message": "Cannot connect to QiCard API - please check your network",
            }
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "message": f"Connection error: {str(e)}",
            }

    except Exception as e:
        return {
            "success": False,
            "message": f"Test failed: {str(e)}",
        }
