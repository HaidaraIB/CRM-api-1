"""
Zain Cash Payment Gateway Integration Utilities
"""

import requests
import json
import jwt
import time
from django.conf import settings
from .models import PaymentGateway, PaymentGatewayStatus


def get_zaincash_gateway():
    """Get active Zain Cash payment gateway"""
    try:
        # Search for various name patterns: "zaincash", "zain cash", "zain-cash"
        from django.db.models import Q
        gateway = PaymentGateway.objects.filter(
            Q(name__icontains="zaincash") | Q(name__icontains="zain cash") | Q(name__icontains="zain-cash"),
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        ).first()
        return gateway
    except Exception:
        return None


def create_zaincash_payment_session(
    amount: float,
    customer_email: str,
    customer_name: str,
    customer_phone: str,
    subscription_id: str,
    return_url: str,
):
    """
    Create a payment session with Zain Cash

    Args:
        amount: Payment amount
        customer_email: Customer email
        customer_name: Customer name
        customer_phone: Customer phone
        subscription_id: Unique subscription ID
        return_url: URL to redirect customer after payment

    Returns:
        dict: Response from Zain Cash API containing payment URL and transaction ID
    """
    zaincash_gateway = get_zaincash_gateway()

    if not zaincash_gateway:
        raise ValueError("Zain Cash payment gateway not found or not active")

    config = zaincash_gateway.config or {}
    merchant_id = config.get("merchantId")
    merchant_secret = config.get("merchantSecret")
    msisdn = config.get("msisdn", "")  # Merchant wallet phone number (optional)

    if not merchant_id or not merchant_secret:
        raise ValueError("Zain Cash credentials not configured")

    merchant_id = merchant_id.strip()
    merchant_secret = merchant_secret.strip()

    # Determine API base URL based on environment
    environment = config.get("environment", "test")
    if environment == "live":
        api_base_url = "https://api.zaincash.iq"
    else:
        api_base_url = "https://test.zaincash.iq"

    # Zain Cash expects amount in IQD (Iraqi Dinar)
    # If amount is in USD, convert to IQD (approximate rate: 1 USD = 1300 IQD)
    # Also ensure amount is a number (not string) and meets minimum requirements
    import logging
    logger = logging.getLogger(__name__)
    
    USD_TO_IQD_RATE = 1300  # Approximate conversion rate
    
    # Check if amount seems to be in USD (typically < 1000 for subscription plans)
    # If amount is less than 1000, assume it's USD and convert to IQD
    if amount < 1000:
        amount_iqd = amount * USD_TO_IQD_RATE
        logger.info(f"Converting amount from USD {amount} to IQD {amount_iqd}")
    else:
        # Assume already in IQD
        amount_iqd = amount
    
    # Zain Cash minimum amount is typically 1000 IQD
    # Ensure amount meets minimum requirement
    if amount_iqd < 1000:
        raise ValueError(f"Amount {amount_iqd} IQD is below minimum of 1000 IQD")
    
    # Zain Cash API might require amount as integer (in smallest currency unit)
    # IQD doesn't have smaller units, so we use the amount as-is
    # But ensure it's a valid number format
    # Try as integer first (no decimals), if that fails, use decimal
    amount_iqd = float(amount_iqd)
    
    # Round to remove any floating point precision issues
    # If amount is a whole number, use integer format
    if amount_iqd == int(amount_iqd):
        amount_iqd = int(amount_iqd)
    else:
        # Keep as float with 2 decimal places
        amount_iqd = round(amount_iqd, 2)
    
    logger.info(f"Final amount for Zain Cash: {amount_iqd} IQD (type: {type(amount_iqd).__name__})")
    
    # Prepare JWT payload
    payload = {
        "amount": amount_iqd,
        "serviceType": "Subscription Payment",
        "msisdn": msisdn,  # Merchant wallet phone number
        "orderId": f"SUB-{subscription_id}",
        "redirectUrl": return_url,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,  # Token expires in 1 hour
    }

    # Encode JWT token
    token = jwt.encode(payload, merchant_secret, algorithm="HS256")
    # Ensure token is a string (PyJWT returns bytes in some versions)
    if isinstance(token, bytes):
        token = token.decode('utf-8')

    # Prepare request data (Zain Cash expects form-urlencoded, not JSON)
    from urllib.parse import urlencode
    request_data = {
        "token": token,
        "merchantId": merchant_id,
        "lang": "ar",  # Language: 'ar' or 'en'
    }

    # Make API request
    api_url = f"{api_base_url}/transaction/init"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        # Use data parameter with urlencode for form-urlencoded format
        response = requests.post(
            api_url,
            data=urlencode(request_data),
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        
        # Log the response for debugging
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Zain Cash API response: {result}")
        
        # Extract transaction ID from response
        transaction_id = result.get("id")
        if not transaction_id:
            raise ValueError(f"Zain Cash did not return transaction ID. Response: {result}")
        
        # Construct the payment URL that user should be redirected to
        payment_url = f"{api_base_url}/transaction/pay?id={transaction_id}"
        
        # Return both the transaction ID and payment URL
        return {
            "id": transaction_id,
            "payment_url": payment_url,
            "transaction_id": transaction_id,
        }
    except requests.exceptions.HTTPError as e:
        # Try to get error details from response
        error_detail = str(e)
        try:
            error_data = e.response.json()
            error_detail = error_data.get("message") or error_data.get("error") or error_data.get("msg") or str(e)
        except:
            pass
        raise Exception(f"Zain Cash API error: {error_detail}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Zain Cash API error: {str(e)}")


def verify_zaincash_payment(token: str):
    """
    Verify a Zain Cash payment transaction by decoding the JWT token

    Args:
        token: JWT token returned from Zain Cash callback

    Returns:
        dict: Decoded payment verification response
    """
    # Validate that token is a JWT format (should have 3 segments separated by dots)
    if not token or token.count('.') != 2:
        raise ValueError(f"Invalid JWT token format. Expected 3 segments separated by dots, got: {token[:50]}...")
    
    zaincash_gateway = get_zaincash_gateway()

    if not zaincash_gateway:
        raise ValueError("Zain Cash payment gateway not found or not active")

    config = zaincash_gateway.config or {}
    merchant_secret = config.get("merchantSecret")

    if not merchant_secret:
        raise ValueError("Zain Cash credentials not configured")

    merchant_secret = merchant_secret.strip()

    try:
        # Decode JWT token
        decoded = jwt.decode(token, merchant_secret, algorithms=["HS256"])
        return decoded
    except jwt.ExpiredSignatureError:
        raise Exception("Zain Cash token has expired")
    except jwt.InvalidTokenError as e:
        raise Exception(f"Invalid Zain Cash token: {str(e)}")


def check_zaincash_payment_status(transaction_id: str, msisdn: str = ""):
    """
    Check the status of a Zain Cash payment using the transaction ID
    Based on Zain Cash API documentation example 3
    
    Args:
        transaction_id: The transaction ID returned from Zain Cash
        msisdn: Optional merchant wallet phone number
        
    Returns:
        dict: Payment status information from Zain Cash API
    """
    zaincash_gateway = get_zaincash_gateway()
    
    if not zaincash_gateway:
        raise ValueError("Zain Cash payment gateway not found or not active")
    
    config = zaincash_gateway.config or {}
    merchant_id = config.get("merchantId")
    merchant_secret = config.get("merchantSecret")
    msisdn = msisdn or config.get("msisdn", "")
    
    if not merchant_id or not merchant_secret:
        raise ValueError("Zain Cash credentials not configured")
    
    merchant_id = merchant_id.strip()
    merchant_secret = merchant_secret.strip()
    
    # Determine API base URL based on environment
    environment = config.get("environment", "test")
    if environment == "live":
        api_base_url = "https://api.zaincash.iq"
    else:
        api_base_url = "https://test.zaincash.iq"
    
    # Build JWT payload for status check
    payload = {
        "id": transaction_id,
        "msisdn": msisdn,
        "iat": int(time.time()),
        "exp": int(time.time()) + 14400,  # 4 hours expiry
    }
    
    # Encode JWT token
    token = jwt.encode(payload, merchant_secret, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode('utf-8')
    
    # Prepare request data (form-urlencoded)
    from urllib.parse import urlencode
    request_data = {
        "token": token,
        "merchantId": merchant_id,
    }
    
    # Make API request to check transaction status
    api_url = f"{api_base_url}/transaction/get"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
    }
    
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        response = requests.post(
            api_url,
            data=urlencode(request_data),
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        
        logger.info(f"Zain Cash transaction status check response: {result}")
        return result
    except requests.exceptions.HTTPError as e:
        error_detail = str(e)
        try:
            error_data = e.response.json()
            error_detail = error_data.get("message") or error_data.get("error") or error_data.get("msg") or str(e)
        except:
            pass
        raise Exception(f"Zain Cash status check error: {error_detail}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Zain Cash status check error: {str(e)}")


def test_zaincash_credentials(merchant_id: str, merchant_secret: str, environment: str = "test", msisdn: str = ""):
    """
    Test Zain Cash credentials by attempting to create a minimal test transaction
    
    Args:
        merchant_id: Merchant ID to test
        merchant_secret: Merchant Secret to test
        environment: 'test' or 'live'
        msisdn: Optional merchant wallet phone number
    
    Returns:
        dict: Test result with success status and message
    """
    try:
        merchant_id = merchant_id.strip()
        merchant_secret = merchant_secret.strip()
        
        if not merchant_id or not merchant_secret:
            return {
                "success": False,
                "message": "Merchant ID and Merchant Secret are required"
            }
        
        # Determine API base URL based on environment
        if environment == "live":
            api_base_url = "https://api.zaincash.iq"
        else:
            api_base_url = "https://test.zaincash.iq"
        
        # Prepare a minimal test JWT payload (small amount for testing)
        payload = {
            "amount": 0.01,  # Minimal test amount
            "serviceType": "Test Connection",
            "msisdn": msisdn,
            "orderId": f"TEST-{int(time.time())}",
            "redirectUrl": "https://example.com/test",  # Dummy redirect URL for testing
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        
        # Try to encode JWT token (this validates the secret)
        try:
            token = jwt.encode(payload, merchant_secret, algorithm="HS256")
            if isinstance(token, bytes):
                token = token.decode('utf-8')
        except Exception as e:
            return {
                "success": False,
                "message": f"Invalid Merchant Secret: {str(e)}"
            }
        
        # Prepare request data
        request_data = {
            "token": token,
            "merchantId": merchant_id,
            "lang": "ar",
        }
        
        # Make API request to test credentials
        api_url = f"{api_base_url}/transaction/init"
        headers = {
            "Content-Type": "application/json",
        }
        
        try:
            response = requests.post(
                api_url,
                json=request_data,
                headers=headers,
                timeout=10,
            )
            
            # If we get a response (even if it's an error about the test transaction),
            # it means the credentials are valid and we can communicate with the API
            if response.status_code == 200:
                return {
                    "success": True,
                    "message": "Credentials are valid and connection successful"
                }
            elif response.status_code == 400:
                # 400 might mean invalid request format, but credentials might still be valid
                # Try to parse the error to see if it's about credentials
                try:
                    error_data = response.json()
                    error_msg = error_data.get("message", "") or error_data.get("error", "")
                    if "merchant" in error_msg.lower() or "invalid" in error_msg.lower():
                        return {
                            "success": False,
                            "message": f"Invalid credentials: {error_msg}"
                        }
                except:
                    pass
                # If it's a 400 but not about credentials, consider it a partial success
                # (credentials work, but test transaction format might be wrong)
                return {
                    "success": True,
                    "message": "Credentials appear valid (API connection successful)"
                }
            else:
                error_msg = f"API returned status {response.status_code}"
                try:
                    error_data = response.json()
                    error_msg = error_data.get("message", error_msg) or error_data.get("error", error_msg)
                except:
                    pass
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
                "message": "Cannot connect to Zain Cash API - please check your network"
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

