from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import HasActiveSubscription
from crm_saas_api.responses import success_response
from integrations.services.message_logs import fetch_message_logs


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def message_logs_list(request):
    """
    Unified SMS + WhatsApp message log feed backing the Messaging Center's
    Message Logs tab. Company-wide for the owner/supervisor; restricted staff
    roles (requires_campaign_approval()) only see their own sends, consistent
    with the rest of the Messaging Center's own-leads-only scoping for them.
    GET /api/integrations/message-logs/?page=1&page_size=30&channel=sms|whatsapp|all&...
    """
    company = request.user.company
    if not company:
        return success_response(
            data={"count": 0, "page": 1, "page_size": 30, "summary": {}, "results": []}
        )
    restrict_to_user_id = request.user.id if request.user.requires_campaign_approval() else None
    data = fetch_message_logs(company, request.query_params, restrict_to_user_id=restrict_to_user_id)
    return success_response(data=data)
