from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import HasActiveSubscription, IsAdmin
from crm_saas_api.responses import success_response
from integrations.services.message_logs import fetch_message_logs


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription, IsAdmin])
def message_logs_list(request):
    """
    Unified SMS + WhatsApp message log feed. Company-wide, so owner-only —
    it backs the Messaging Center's Message Logs tab, which staff cannot open.
    GET /api/integrations/message-logs/?page=1&page_size=30&channel=sms|whatsapp|all&...
    """
    company = request.user.company
    if not company:
        return success_response(
            data={"count": 0, "page": 1, "page_size": 30, "summary": {}, "results": []}
        )
    data = fetch_message_logs(company, request.query_params)
    return success_response(data=data)
