"""
Paytabs Payment Gateway Integration Utilities
"""

import requests
import json
from django.conf import settings
from .models import PaymentGateway, PaymentGatewayStatus


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


def create_paytabs_payment_session(
    amount: float,
    customer_email: str,
    customer_name: str,
    customer_phone: str,
    subscription_id: str,
    callback_url: str,
    return_url: str,
):
    """
    Create a payment session with Paytabs

    Args:
        amount: Payment amount
        customer_email: Customer email
        customer_name: Customer name
        customer_phone: Customer phone
        subscription_id: Unique subscription ID
        callback_url: URL for Paytabs to send payment callback
        return_url: URL to redirect customer after payment

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

    # Prepare payment data
    payment_data = {
        "profile_id": profile_id,
        "tran_type": "sale",
        "tran_class": "ecom",
        "cart_id": f"SUB-{subscription_id}",
        "cart_currency": "IQD",
        "cart_amount": amount,
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
        "callback": callback_url,
        "return": return_url,
    }

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
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise Exception(f"Paytabs API error: {str(e)}")


def verify_paytabs_payment(transaction_ref: str):
    """
    Verify a Paytabs payment transaction

    Args:
        transaction_ref: Transaction reference from Paytabs
        gateway: PaymentGateway instance (optional)

    Returns:
        dict: Payment verification response
    """
    paytabs_gateway = get_paytabs_gateway()

    config = paytabs_gateway.config or {}
    profile_id = config.get("profileId")
    server_key = config.get("serverKey")

    if not profile_id or not server_key:
        raise ValueError("Paytabs credentials not configured")

    server_key = server_key.strip()
    profile_id = int(profile_id)

    api_url = f"{settings.PAYTABS_DOMAIN}/payment/query"
    query_data = {
        "profile_id": profile_id,
        "tran_ref": transaction_ref,
    }
    headers = {"authorization": server_key, "content-type": "application/octet-stream"}

    try:
        verify_response = requests.post(
            api_url,
            data=json.dumps(query_data),
            headers=headers,
            timeout=30,
        )
        verify_response.raise_for_status()
        result = verify_response.json()
        return result
    except requests.exceptions.RequestException as e:
        raise Exception(f"Paytabs verification error: {str(e)}")
