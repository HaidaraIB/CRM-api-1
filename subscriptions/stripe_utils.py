"""
Stripe Payment Gateway Integration Utilities
"""

import stripe
import json
import logging
from django.conf import settings
from .models import PaymentGateway, PaymentGatewayStatus

logger = logging.getLogger(__name__)


def get_stripe_gateway():
    """Get active Stripe payment gateway"""
    try:
        gateway = PaymentGateway.objects.filter(
            name__icontains="stripe",
            status=PaymentGatewayStatus.ACTIVE.value,
            enabled=True,
        ).first()
        return gateway
    except Exception:
        return None


def create_stripe_payment_session(
    amount: float,
    customer_email: str,
    customer_name: str,
    subscription_id: int,
    return_url: str,
    success_url: str,
    cancel_url: str,
):
    """
    Create a Stripe Checkout Session for payment

    Args:
        amount: Payment amount (in smallest currency unit, e.g., cents for USD)
        customer_email: Customer email
        customer_name: Customer name
        subscription_id: Unique subscription ID
        return_url: URL to redirect customer after payment (deprecated, use success_url)
        success_url: URL to redirect customer after successful payment
        cancel_url: URL to redirect customer if payment is cancelled

    Returns:
        dict: Response containing session_id and url
    """
    stripe_gateway = get_stripe_gateway()

    if not stripe_gateway:
        raise ValueError("Stripe payment gateway not found or not active")

    config = stripe_gateway.config or {}
    secret_key = config.get("secretKey")
    publishable_key = config.get("publishableKey")
    currency = config.get("currency", "usd").lower()

    if not secret_key:
        raise ValueError("Stripe secret key not configured")

    # Set Stripe API key
    stripe.api_key = secret_key.strip()

    # Convert amount to smallest currency unit (cents for USD, fils for IQD, etc.)
    # Stripe expects amounts in the smallest currency unit
    # For USD: 1 USD = 100 cents
    # For IQD: 1 IQD = 1000 fils (but Stripe might not support IQD, so we'll use USD)
    
    # Determine currency unit multiplier
    currency_multipliers = {
        "usd": 100,  # 1 USD = 100 cents
        "eur": 100,  # 1 EUR = 100 cents
        "gbp": 100,  # 1 GBP = 100 pence
        "iqd": 1000,  # 1 IQD = 1000 fils (if supported)
    }
    
    multiplier = currency_multipliers.get(currency, 100)  # Default to 100
    amount_in_cents = int(amount * multiplier)

    # Create Stripe Checkout Session
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": currency,
                        "product_data": {
                            "name": f"Subscription Payment - Subscription {subscription_id}",
                            "description": f"Payment for subscription {subscription_id}",
                        },
                        "unit_amount": amount_in_cents,
                    },
                    "quantity": 1,
                }
            ],
            mode="payment",
            customer_email=customer_email,
            metadata={
                "subscription_id": str(subscription_id),
                "customer_name": customer_name,
            },
            success_url=success_url,
            cancel_url=cancel_url,
        )

        logger.info(
            f"Created Stripe checkout session {session.id} for subscription {subscription_id}"
        )

        return {
            "session_id": session.id,
            "url": session.url,
            "payment_intent": session.payment_intent,
        }
    except stripe.error.StripeError as e:
        logger.error(f"Stripe API error: {str(e)}")
        raise Exception(f"Stripe API error: {str(e)}")
    except Exception as e:
        logger.error(f"Error creating Stripe session: {str(e)}")
        raise Exception(f"Error creating Stripe session: {str(e)}")


def verify_stripe_payment(session_id: str):
    """
    Verify a Stripe payment by checking the session status

    Args:
        session_id: Stripe Checkout Session ID

    Returns:
        dict: Payment verification response
    """
    logger.info(f"Verifying Stripe payment with session_id: {session_id}")
    
    stripe_gateway = get_stripe_gateway()

    if not stripe_gateway:
        logger.error("Stripe gateway not found or not active")
        raise ValueError("Stripe payment gateway not found or not active")

    config = stripe_gateway.config or {}
    secret_key = config.get("secretKey")

    if not secret_key:
        logger.error("Stripe secret key not configured in gateway config")
        raise ValueError("Stripe secret key not configured")

    # Set Stripe API key
    stripe.api_key = secret_key.strip()
    logger.info(f"Using Stripe API key (first 10 chars): {secret_key[:10]}...")

    try:
        # Retrieve the session
        logger.info(f"Retrieving Stripe checkout session: {session_id}")
        session = stripe.checkout.Session.retrieve(session_id)
        logger.info(f"Stripe session retrieved: id={session.id}, payment_status={session.payment_status}, "
                   f"amount_total={session.amount_total}, currency={session.currency}")

        # Get payment intent details if available
        payment_intent = None
        if session.payment_intent:
            logger.info(f"Retrieving payment intent: {session.payment_intent}")
            payment_intent = stripe.PaymentIntent.retrieve(session.payment_intent)
            logger.info(f"Payment intent retrieved: id={payment_intent.id}, status={payment_intent.status}")

        # Extract subscription ID from metadata
        subscription_id = session.metadata.get("subscription_id") if session.metadata else None
        logger.info(f"Subscription ID from metadata: {subscription_id}")
        logger.info(f"Session metadata: {session.metadata}")

        # Determine payment status
        payment_status = "pending"
        if session.payment_status == "paid":
            payment_status = "completed"
        elif session.payment_status == "unpaid":
            payment_status = "pending"
        elif session.payment_status == "no_payment_required":
            payment_status = "completed"
        
        logger.info(f"Determined payment_status: {payment_status} (from stripe_payment_status: {session.payment_status})")

        result = {
            "session_id": session.id,
            "payment_status": payment_status,
            "stripe_payment_status": session.payment_status,
            "amount_total": session.amount_total / 100.0 if session.amount_total else 0,  # Convert from cents
            "currency": session.currency,
            "customer_email": session.customer_email,
            "subscription_id": subscription_id,
            "payment_intent": {
                "id": payment_intent.id if payment_intent else None,
                "status": payment_intent.status if payment_intent else None,
            } if payment_intent else None,
        }
        
        logger.info(f"Stripe verification successful. Result: {json.dumps(result, indent=2, default=str)}")
        return result
    except stripe.error.StripeError as e:
        logger.error(f"Stripe verification error: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error code: {getattr(e, 'code', 'N/A')}")
        raise Exception(f"Stripe verification error: {str(e)}")
    except Exception as e:
        logger.error(f"Error verifying Stripe payment: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        raise Exception(f"Error verifying Stripe payment: {str(e)}")


def test_stripe_credentials(secret_key: str, publishable_key: str = ""):
    """
    Test Stripe credentials by making a simple API call

    Args:
        secret_key: Stripe secret key to test
        publishable_key: Stripe publishable key (optional, for validation)

    Returns:
        dict: Test result with success status and message
    """
    try:
        if not secret_key or not secret_key.strip():
            return {
                "success": False,
                "message": "Secret key is required"
            }

        secret_key = secret_key.strip()
        
        # Basic validation: Stripe keys should start with sk_ (secret) or sk_test_/sk_live_
        if not secret_key.startswith("sk_"):
            return {
                "success": False,
                "message": "Invalid secret key format. Stripe secret keys must start with 'sk_'"
            }

        # Set Stripe API key
        stripe.api_key = secret_key

        # Make a simple API call to test credentials
        # We'll try to retrieve account information (lightweight operation)
        try:
            # Try to retrieve account information (lightweight operation)
            # This will fail if the key is invalid
            account = stripe.Account.retrieve()
            
            # Verify we got a valid account response
            if not account or not account.get('id'):
                return {
                    "success": False,
                    "message": "Invalid response from Stripe API"
                }
            
            # Get account type and status
            account_id = account.get('id', 'N/A')
            account_type = account.get('type', 'N/A')
            charges_enabled = account.get('charges_enabled', False)
            details_submitted = account.get('details_submitted', False)
            
            message = f"Credentials are valid. Account ID: {account_id}"
            if account_type:
                message += f", Type: {account_type}"
            if not charges_enabled:
                message += " (Note: Charges are not enabled for this account)"
            if not details_submitted:
                message += " (Note: Account details not fully submitted)"
            
            return {
                "success": True,
                "message": message
            }
        except stripe.error.AuthenticationError as e:
            logger.error(f"Stripe authentication error: {str(e)}")
            return {
                "success": False,
                "message": f"Invalid secret key - authentication failed: {str(e)}"
            }
        except stripe.error.InvalidRequestError as e:
            logger.error(f"Stripe invalid request error: {str(e)}")
            # This might happen if the key format is wrong
            return {
                "success": False,
                "message": f"Invalid request: {str(e)}"
            }
        except stripe.error.PermissionError as e:
            logger.error(f"Stripe permission error: {str(e)}")
            return {
                "success": False,
                "message": f"Secret key does not have required permissions: {str(e)}"
            }
        except stripe.error.APIConnectionError as e:
            logger.error(f"Stripe API connection error: {str(e)}")
            return {
                "success": False,
                "message": f"Cannot connect to Stripe API: {str(e)}"
            }
        except stripe.error.StripeError as e:
            logger.error(f"Stripe API error: {str(e)}")
            return {
                "success": False,
                "message": f"Stripe API error: {str(e)}"
            }
    except Exception as e:
        logger.error(f"Unexpected error testing Stripe credentials: {str(e)}", exc_info=True)
        return {
            "success": False,
            "message": f"Test failed: {str(e)}"
        }

