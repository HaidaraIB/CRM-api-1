"""DRF response helpers for payment gateway connection tests."""
from crm_saas_api.responses import error_response, success_response


def payment_gateway_test_response(result):
    if result.get("success"):
        return success_response(data=result)
    return error_response(
        str(result.get("message") or "Connection test failed"),
        code="bad_request",
        details=result,
    )
