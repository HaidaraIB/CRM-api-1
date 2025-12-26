"""
QiCard Payment Gateway Integration Utilities
"""

import requests
import json
import base64
import uuid
import time
from django.conf import settings
from .models import PaymentGateway, PaymentGatewayStatus
from settings.models import SystemSettings


def get_qicard_gateway():
    """Get active QiCard payment gateway"""
    try:
        from django.db.models import Q
        gateway = PaymentGateway.objects.filter(
            Q(name__icontains="qicard") | Q(name__icontains="qi card") | Q(name__icontains="qi-card"),
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        ).first()
        return gateway
    except Exception:
        return None


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

    Args:
        amount: Payment amount
        customer_email: Customer email
        customer_name: Customer name
        customer_phone: Customer phone
        subscription_id: Unique subscription ID
        return_url: URL to redirect customer after payment
        notification_url: Webhook URL for payment notifications

    Returns:
        dict: Response from QiCard API containing payment URL and payment ID
    """
    qicard_gateway = get_qicard_gateway()

    if not qicard_gateway:
        raise ValueError("QiCard payment gateway not found or not active")

    config = qicard_gateway.config or {}
    terminal_id = config.get("terminalId")
    username = config.get("username")
    password = config.get("password")
    environment = config.get("environment", "test")

    if not terminal_id or not username or not password:
        raise ValueError("QiCard credentials not configured")

    terminal_id = terminal_id.strip()
    username = username.strip()
    password = password.strip()

    # Determine API base URL based on environment
    if environment == "live":
        api_base_url = "https://api.qi.iq"  # Update with actual live URL when available
    else:
        api_base_url = "https://uat-sandbox-3ds-api.qi.iq"

    # Get USD to IQD rate from database settings
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        system_settings = SystemSettings.get_settings()
        USD_TO_IQD_RATE = float(system_settings.usd_to_iqd_rate)
    except Exception as e:
        logger.warning(f"Failed to get USD to IQD rate from database, using default 1300: {e}")
        USD_TO_IQD_RATE = 1300  # Fallback to default rate
    
    # Check if amount seems to be in USD (typically < 1000 for subscription plans)
    # If amount is less than 1000, assume it's USD and convert to IQD
    if amount < 1000:
        amount_iqd = amount * USD_TO_IQD_RATE
        logger.info(f"Converting amount from USD {amount} to IQD {amount_iqd} (rate: {USD_TO_IQD_RATE})")
    else:
        # Assume already in IQD
        amount_iqd = amount
    
    # Generate unique request ID
    request_id = str(uuid.uuid4())

    # Prepare Basic Auth header
    credentials = f"{username}:{password}"
    encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')

    # Prepare request body
    request_body = {
        "requestId": request_id,
        "amount": amount_iqd,
        "currency": "IQD",
        "locale": "ar_IQ",  # Arabic locale for Iraq
        "finishPaymentUrl": return_url,
        "notificationUrl": notification_url,
        "customerInfo": {
            "firstName": customer_name.split()[0] if customer_name.split() else customer_name,
            "lastName": " ".join(customer_name.split()[1:]) if len(customer_name.split()) > 1 else "",
            "phone": customer_phone,
            "email": customer_email,
        }
    }

    # Make API request
    api_url = f"{api_base_url}/api/v1/payment"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {encoded_credentials}",
        "X-Terminal-Id": terminal_id,
    }

    try:
        response = requests.post(
            api_url,
            json=request_body,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        
        logger.info(f"QiCard API response: {result}")
        
        # Extract payment ID and form URL from response
        payment_id = result.get("paymentId")
        form_url = result.get("formUrl")
        
        if not payment_id or not form_url:
            raise ValueError(f"QiCard did not return payment ID or form URL. Response: {result}")
        
        # Return both the payment ID and payment URL
        return {
            "payment_id": payment_id,
            "form_url": form_url,
            "request_id": request_id,
        }
    except requests.exceptions.HTTPError as e:
        # Try to get error details from response
        error_detail = str(e)
        try:
            error_data = e.response.json()
            error_obj = error_data.get("error", {})
            error_detail = error_obj.get("description") or error_obj.get("message") or str(e)
        except:
            pass
        raise Exception(f"QiCard API error: {error_detail}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"QiCard API error: {str(e)}")


def verify_qicard_payment(payment_id: str):
    """
    Verify a QiCard payment transaction by checking payment status

    Args:
        payment_id: Payment ID from QiCard

    Returns:
        dict: Payment verification response with status
    """
    qicard_gateway = get_qicard_gateway()

    if not qicard_gateway:
        raise ValueError("QiCard payment gateway not found or not active")

    config = qicard_gateway.config or {}
    terminal_id = config.get("terminalId")
    username = config.get("username")
    password = config.get("password")
    environment = config.get("environment", "test")

    if not terminal_id or not username or not password:
        raise ValueError("QiCard credentials not configured")

    terminal_id = terminal_id.strip()
    username = username.strip()
    password = password.strip()

    # Determine API base URL based on environment
    if environment == "live":
        api_base_url = "https://api.qi.iq"  # Update with actual live URL when available
    else:
        api_base_url = "https://uat-sandbox-3ds-api.qi.iq"

    # Prepare Basic Auth header
    credentials = f"{username}:{password}"
    encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')

    # Make API request to get payment status
    api_url = f"{api_base_url}/api/v1/payment/{payment_id}/status"
    headers = {
        "Authorization": f"Basic {encoded_credentials}",
        "X-Terminal-Id": terminal_id,
    }

    import logging
    logger = logging.getLogger(__name__)

    try:
        response = requests.get(
            api_url,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        
        logger.info(f"QiCard payment status check response: {result}")
        return result
    except requests.exceptions.HTTPError as e:
        error_detail = str(e)
        try:
            error_data = e.response.json()
            error_obj = error_data.get("error", {})
            error_detail = error_obj.get("description") or error_obj.get("message") or str(e)
        except:
            pass
        raise Exception(f"QiCard status check error: {error_detail}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"QiCard status check error: {str(e)}")


def test_qicard_credentials(terminal_id: str, username: str, password: str, environment: str = "test"):
    """
    Test QiCard credentials by attempting to create a minimal test payment request
    
    Args:
        terminal_id: Terminal ID to test
        username: Username to test
        password: Password to test
        environment: 'test' or 'live'
    
    Returns:
        dict: Test result with success status and message
    """
    try:
        terminal_id = terminal_id.strip()
        username = username.strip()
        password = password.strip()
        
        if not terminal_id or not username or not password:
            return {
                "success": False,
                "message": "Terminal ID, Username, and Password are required"
            }
        
        # Determine API base URL based on environment
        if environment == "live":
            api_base_url = "https://api.qi.iq"  # Update with actual live URL when available
        else:
            api_base_url = "https://uat-sandbox-3ds-api.qi.iq"
        
        # Prepare Basic Auth header
        credentials = f"{username}:{password}"
        encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
        
        # Try to create a minimal test payment request to validate credentials
        # We'll create a very small test payment (0.01 IQD) that we can cancel
        test_request_id = str(uuid.uuid4())
        test_payload = {
            "requestId": test_request_id,
            "amount": 0.01,
            "currency": "IQD",
            "locale": "ar_IQ",
            "finishPaymentUrl": "https://example.com/test",
            "notificationUrl": "https://example.com/test",
        }
        
        api_url = f"{api_base_url}/api/v1/payment"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {encoded_credentials}",
            "X-Terminal-Id": terminal_id,
        }
        
        try:
            # Try to create a test payment - if credentials are valid, we'll get a response
            # (even if it's an error about the test payment, it means auth worked)
            response = requests.post(
                api_url,
                json=test_payload,
                headers=headers,
                timeout=10,
            )
            
            # If we get 401, credentials are invalid
            if response.status_code == 401:
                try:
                    error_data = response.json()
                    error_obj = error_data.get("error", {})
                    error_msg = error_obj.get("description") or error_obj.get("message") or "Authentication failed"
                except:
                    error_msg = "Authentication failed"
                return {
                    "success": False,
                    "message": f"Invalid credentials: {error_msg}"
                }
            # If we get 200, credentials are valid and payment was created
            elif response.status_code == 200:
                return {
                    "success": True,
                    "message": "Credentials are valid and connection successful"
                }
            # If we get 400, it might be a validation error but credentials could still be valid
            elif response.status_code == 400:
                try:
                    error_data = response.json()
                    error_obj = error_data.get("error", {})
                    error_code = error_obj.get("code")
                    # If it's an authentication error (code 27), credentials are invalid
                    if error_code == 27:
                        error_msg = error_obj.get("description") or "Invalid credentials"
                        return {
                            "success": False,
                            "message": f"Invalid credentials: {error_msg}"
                        }
                    # Otherwise, credentials might be valid but request format is wrong
                    return {
                        "success": True,
                        "message": "Credentials appear valid (authentication successful)"
                    }
                except:
                    return {
                        "success": True,
                        "message": "Credentials appear valid (authentication successful)"
                    }
            else:
                # Try to parse error message
                try:
                    error_data = response.json()
                    error_obj = error_data.get("error", {})
                    error_msg = error_obj.get("description") or error_obj.get("message") or f"API returned status {response.status_code}"
                except:
                    error_msg = f"API returned status {response.status_code}"
                
                # If it's a 5xx error, it's a server issue, not credentials
                if 500 <= response.status_code < 600:
                    return {
                        "success": True,
                        "message": "Credentials appear valid (server error, not authentication)"
                    }
                else:
                    return {
                        "success": False,
                        "message": f"Connection failed: {error_msg}"
                    }
        except requests.exceptions.Timeout:
            return {
                "success": False,
                "message": "Connection timeout - please check your network connection"
            }
        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "message": "Cannot connect to QiCard API - please check your network"
            }
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "message": f"Connection error: {str(e)}"
            }
            
    except Exception as e:
        return {
            "success": False,
            "message": f"Test failed: {str(e)}"
        }

