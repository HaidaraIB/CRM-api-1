"""Company quick replies for inbox and chat composers."""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import HasActiveSubscription
from crm_saas_api.responses import error_response, success_response, validation_error_response
from integrations.models import QuickReply


def _serialize(row: QuickReply) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "body": row.body,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def quick_replies(request):
    company = request.user.company
    if request.method == "GET":
        rows = QuickReply.objects.filter(company=company)
        return success_response([_serialize(row) for row in rows])
    if not request.user.is_admin():
        return error_response("Only the account owner can manage quick replies.", status_code=403)
    title = (request.data.get("title") or "").strip()
    body = (request.data.get("body") or "").strip()
    if not title or not body:
        return validation_error_response(
            {key: ["Required"] for key, value in (("title", title), ("body", body)) if not value}
        )
    row = QuickReply.objects.create(
        company=company, title=title[:80], body=body, created_by=request.user
    )
    return success_response(_serialize(row), status_code=201)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def quick_reply_detail(request, pk: int):
    if not request.user.is_admin():
        return error_response("Only the account owner can manage quick replies.", status_code=403)
    row = QuickReply.objects.filter(company=request.user.company, pk=pk).first()
    if not row:
        return error_response("Not found", status_code=404)
    if request.method == "DELETE":
        row.delete()
        return success_response({"deleted": True})
    title = (request.data.get("title") or row.title).strip()
    body = (request.data.get("body") if "body" in request.data else row.body).strip()
    if not title or not body:
        return validation_error_response({"title": ["Required"], "body": ["Required"]})
    row.title = title[:80]
    row.body = body
    row.save(update_fields=["title", "body", "updated_at"])
    return success_response(_serialize(row))
