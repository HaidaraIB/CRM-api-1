"""
OpenAI integration settings and AI lead insights (v1: /api/v1/integrations/...).
"""
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import HasActiveSubscription
from crm.models import ClientTask
from crm_saas_api.responses import error_response, success_response, validation_error_response
from integrations.models import AIInsightStatus, ClientAIInsight, OpenAISettings
from integrations.serializers import ClientAIInsightSerializer, OpenAISettingsSerializer
from integrations.services.ai_lead_analysis import run_company_analysis, test_openai_connection
from integrations.services.ai_management_report import (
    generate_management_report,
    get_management_report_for_company,
)
from integrations.ai_insight_i18n import normalize_insight_language, pick_insight_text
from integrations.views.twilio_sms import _integration_gate

AI_RUN_CACHE_KEY = "ai_analysis_run:{company_id}"
AI_REPORT_CACHE_KEY = "ai_management_report:{company_id}"
AI_RUN_COOLDOWN_SECONDS = 300
AI_REPORT_COOLDOWN_SECONDS = 900


def _insight_serializer_context(request):
    lang = request.query_params.get("lang") or getattr(request.user, "language", None)
    return {"language": normalize_insight_language(lang)}


def _user_is_owner(user) -> bool:
    return user.is_admin()


def _user_can_manage_ai_insight(user, insight: ClientAIInsight) -> bool:
    if user.is_admin() or user.is_supervisor():
        return True
    return insight.client.assigned_to_id == user.id


def _user_can_run_analysis(user) -> bool:
    return user.is_admin() or user.is_supervisor()


def _insights_queryset(user):
    company = user.company
    qs = (
        ClientAIInsight.objects.filter(company=company)
        .select_related("client", "client__assigned_to", "approved_by")
    )
    if user.is_admin() or user.is_supervisor():
        return qs
    return qs.filter(client__assigned_to=user)


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def openai_settings_view(request):
    company = request.user.company
    if request.method == "GET":
        try:
            settings = OpenAISettings.objects.get(company=company)
        except OpenAISettings.DoesNotExist:
            settings = None
        if settings is None:
            blocked = _integration_gate(company, "openai")
            if blocked is not None:
                return blocked
            return success_response(
                data={
                    "is_enabled": False,
                    "model": OpenAISettings.DEFAULT_MODEL,
                    "auto_analyze_enabled": True,
                    "max_leads_per_run": OpenAISettings.DEFAULT_MAX_LEADS_PER_RUN,
                    "api_key_masked": None,
                    "last_analysis_at": None,
                    "last_error": None,
                },
            )
        blocked = _integration_gate(company, "openai")
        if blocked is not None:
            return blocked
        return success_response(data=OpenAISettingsSerializer(settings).data)

    settings, _ = OpenAISettings.objects.get_or_create(
        company=company,
        defaults={"is_enabled": False},
    )
    blocked = _integration_gate(company, "openai")
    if blocked is not None:
        return blocked
    serializer = OpenAISettingsSerializer(settings, data=request.data, partial=True)
    if not serializer.is_valid():
        return validation_error_response(serializer.errors)
    serializer.save()
    return success_response(data=OpenAISettingsSerializer(settings).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def openai_settings_test_view(request):
    company = request.user.company
    blocked = _integration_gate(company, "openai")
    if blocked is not None:
        return blocked

    body = request.data if isinstance(request.data, dict) else {}
    draft_key = str(body.get("api_key") or body.get("apiKey") or "").strip()
    draft_model = str(body.get("model") or "").strip()

    if draft_key:
        model = draft_model or "gpt-4o-mini"
        ok, err = test_openai_connection(draft_key, model)
        if not ok:
            return error_response(
                err or "Connection test failed.",
                code="openai_test_failed",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        return success_response(data={"ok": True})

    try:
        settings = OpenAISettings.objects.get(company=company)
    except OpenAISettings.DoesNotExist:
        return error_response(
            "OpenAI is not configured. Save your API key first.",
            code="openai_not_configured",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    api_key = settings.get_api_key()
    if not api_key:
        return error_response(
            "API key is missing. Enter your key and save before testing.",
            code="openai_no_api_key",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    ok, err = test_openai_connection(api_key, settings.model)
    if not ok:
        return error_response(
            err or "Connection test failed.",
            code="openai_test_failed",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return success_response(data={"ok": True})


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def ai_insights_list_view(request):
    blocked = _integration_gate(request.user.company, "openai")
    if blocked is not None:
        return blocked
    qs = _insights_queryset(request.user)
    status_filter = request.query_params.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter)
    serializer = ClientAIInsightSerializer(
        qs[:100], many=True, context=_insight_serializer_context(request)
    )
    return success_response(data=serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def ai_insights_dashboard_view(request):
    blocked = _integration_gate(request.user.company, "openai")
    if blocked is not None:
        return blocked
    qs = _insights_queryset(request.user)
    pending = qs.filter(status=AIInsightStatus.PENDING).order_by("-ai_score")[:10]
    priority = (
        qs.filter(
            Q(status=AIInsightStatus.APPROVED) | Q(status=AIInsightStatus.PENDING, ai_score__gte=70)
        )
        .exclude(status=AIInsightStatus.DISMISSED)
        .order_by("-ai_score")[:6]
    )
    ctx = _insight_serializer_context(request)
    return success_response(
        data={
            "pending": ClientAIInsightSerializer(pending, many=True, context=ctx).data,
            "priority": ClientAIInsightSerializer(priority, many=True, context=ctx).data,
            "ai_enabled": OpenAISettings.objects.filter(
                company=request.user.company, is_enabled=True
            ).exists(),
        },
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def ai_insights_run_view(request):
    user = request.user
    company = user.company
    blocked = _integration_gate(company, "openai")
    if blocked is not None:
        return blocked
    if not _user_can_run_analysis(user):
        return error_response(
            "Only owners and supervisors can trigger AI analysis.",
            code="ai_run_forbidden",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    cache_key = AI_RUN_CACHE_KEY.format(company_id=company.id)
    if cache.get(cache_key):
        return error_response(
            "Analysis was run recently. Please wait a few minutes.",
            code="ai_run_rate_limited",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    result = run_company_analysis(company, force=bool(request.data.get("force")))
    cache.set(cache_key, True, AI_RUN_COOLDOWN_SECONDS)
    if result.get("error"):
        return error_response(
            result["error"],
            code="ai_analysis_failed",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    return success_response(data=result)


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def ai_insight_approve_view(request, pk):
    user = request.user
    company = user.company
    blocked = _integration_gate(company, "openai")
    if blocked is not None:
        return blocked
    try:
        insight = ClientAIInsight.objects.select_related("client", "client__status").get(
            pk=pk, company=company
        )
    except ClientAIInsight.DoesNotExist:
        return error_response(
            "Insight not found.",
            code="ai_insight_not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if insight.status != AIInsightStatus.PENDING:
        return error_response(
            "This insight is no longer pending approval.",
            code="ai_insight_not_pending",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not _user_can_manage_ai_insight(user, insight):
        return error_response(
            "You do not have permission to approve this insight.",
            code="ai_insight_forbidden",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    lang = normalize_insight_language(
        request.data.get("language") or getattr(user, "language", None)
    )
    notes = pick_insight_text(
        language=lang,
        text_en=insight.suggested_task_notes_en,
        text_ar=insight.suggested_task_notes_ar,
        legacy=insight.suggested_task_notes,
    ) or pick_insight_text(
        language=lang,
        text_en=insight.summary_en,
        text_ar=insight.summary_ar,
        legacy=insight.summary,
    )
    if notes and not notes.strip().startswith("🤖"):
        notes = f"🤖 AI: {notes}"

    stage = insight.client.status
    task = ClientTask.objects.create(
        client=insight.client,
        stage=stage,
        notes=notes,
        reminder_date=insight.suggested_reminder_date,
        created_by=user,
    )
    insight.status = AIInsightStatus.APPROVED
    insight.approved_at = timezone.now()
    insight.approved_by = user
    insight.created_client_task = task
    insight.save(
        update_fields=[
            "status",
            "approved_at",
            "approved_by",
            "created_client_task",
        ]
    )
    return success_response(
        data={
            "insight": ClientAIInsightSerializer(
                insight, context=_insight_serializer_context(request)
            ).data,
            "client_task_id": task.id,
        },
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def ai_insight_dismiss_view(request, pk):
    user = request.user
    company = user.company
    blocked = _integration_gate(company, "openai")
    if blocked is not None:
        return blocked
    try:
        insight = ClientAIInsight.objects.select_related("client").get(pk=pk, company=company)
    except ClientAIInsight.DoesNotExist:
        return error_response(
            "Insight not found.",
            code="ai_insight_not_found",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if insight.status != AIInsightStatus.PENDING:
        return error_response(
            "This insight is no longer pending.",
            code="ai_insight_not_pending",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not _user_can_manage_ai_insight(user, insight):
        return error_response(
            "You do not have permission to dismiss this insight.",
            code="ai_insight_forbidden",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    insight.status = AIInsightStatus.DISMISSED
    insight.save(update_fields=["status"])
    return success_response(
        data=ClientAIInsightSerializer(insight, context=_insight_serializer_context(request)).data
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def ai_management_report_view(request):
    company = request.user.company
    blocked = _integration_gate(company, "openai")
    if blocked is not None:
        return blocked
    if not _user_is_owner(request.user):
        return error_response(
            "Only company owners can view the management report.",
            code="ai_report_forbidden",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if not OpenAISettings.objects.filter(company=company, is_enabled=True).exists():
        return success_response(
            data={
                "ai_enabled": False,
                "employees": [],
                "hot_leads": [],
                "has_ai_summary": False,
            },
        )
    data = get_management_report_for_company(company)
    data["ai_enabled"] = True
    return success_response(data=data)


@api_view(["POST"])
@permission_classes([IsAuthenticated, HasActiveSubscription])
def ai_management_report_generate_view(request):
    user = request.user
    company = user.company
    blocked = _integration_gate(company, "openai")
    if blocked is not None:
        return blocked
    if not _user_is_owner(user):
        return error_response(
            "Only company owners can generate the management report.",
            code="ai_report_forbidden",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    cache_key = AI_REPORT_CACHE_KEY.format(company_id=company.id)
    if cache.get(cache_key):
        return error_response(
            "Report was generated recently. Please wait before refreshing.",
            code="ai_report_rate_limited",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    result = generate_management_report(company)
    if result.get("error"):
        return error_response(
            result["error"],
            code=result.get("code", "ai_report_failed"),
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    cache.set(cache_key, True, AI_REPORT_COOLDOWN_SECONDS)
    result["ai_enabled"] = True
    return success_response(data=result)
