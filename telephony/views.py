"""
Telephony APIs.

* ``/telephony/…``           tenant admins: status, extensions, trunks, numbers, routes
* ``/admin/telephony/…``     platform operator: nodes, tenants, provisioning, verification
* ``/internal/telephony/…``  PBX nodes only (node token, private link; contract §5)
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import CanManageTelephony, HasActiveSubscription
from companies.models import Company
from integrations.encryption import encrypt_token
from integrations.policy import is_integration_allowed
from subscriptions.entitlements import require_quota

from . import contract
from .authentication import NodeTokenAuthentication
from .models import (
    PbxArtifact,
    PbxBusinessHours,
    PbxCall,
    PbxCallEvent,
    PbxExtension,
    PbxFraudAlert,
    PbxGatewayPeer,
    PbxInboundRoute,
    PbxIvrMenu,
    PbxNode,
    PbxNumber,
    PbxOutboundRoute,
    PbxQueue,
    PbxRecording,
    PbxSound,
    PbxTenant,
    PbxTrunk,
)
from .permissions import CanManageTelephonySettings, HasTelephonyFeature, IsPbxNode
from .serializers import (
    AdminFraudAlertSerializer,
    AdminNumberSerializer,
    AdminTenantSerializer,
    AdminTenantUpdateSerializer,
    ApplyResultSerializer,
    PbxExtensionSerializer,
    PbxCallDetailSerializer,
    PbxCallEventSerializer,
    PbxCallSerializer,
    PbxBusinessHoursSerializer,
    PbxGatewayCreateSerializer,
    PbxGatewaySerializer,
    PbxInboundRouteSerializer,
    PbxIvrMenuSerializer,
    PbxJobSerializer,
    PbxQueueSerializer,
    QueueMembersSerializer,
    PbxNodeSerializer,
    PbxNumberSerializer,
    PbxOutboundRouteSerializer,
    PbxRecordingSerializer,
    PbxSoundSerializer,
    PbxSoundUploadSerializer,
    PbxTrunkSerializer,
    TelephonySettingsSerializer,
    TelephonyStatusSerializer,
)
from .services import calls as calls_service
from .services import events as events_service
from .services import gateways as gateways_service
from .services import provisioning
from .services import recordings as recordings_service
from .services.tenancy import assert_same_company

TENANT_PERMISSIONS = [IsAuthenticated, HasActiveSubscription, HasTelephonyFeature, CanManageTelephonySettings]


# ---------------------------------------------------------------------------
# Tenant API
# ---------------------------------------------------------------------------


class CompanyScopedViewSetMixin:
    """
    Every telephony viewset: queryset limited to the caller's company, rows created
    for the caller's company only, and a recompile requested after any change.
    The tenant never comes from the request body (spec §3).
    """

    def get_queryset(self):
        return super().get_queryset().filter(company_id=self.request.user.company_id)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["tenant"] = provisioning.tenant_for_company(self.request.user.company)
        return ctx

    def _related(self, instance):
        return []

    def perform_create(self, serializer):
        with transaction.atomic():
            instance = serializer.save(company=self.request.user.company)
            assert_same_company(instance.company_id, *self._related(instance))
            provisioning.request_recompile_for_company(self.request.user.company, user=self.request.user)

    def perform_update(self, serializer):
        with transaction.atomic():
            instance = serializer.save()
            assert_same_company(instance.company_id, *self._related(instance))
            provisioning.request_recompile_for_company(self.request.user.company, user=self.request.user)

    def perform_destroy(self, instance):
        with transaction.atomic():
            instance.delete()
            provisioning.request_recompile_for_company(self.request.user.company, user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        from django.db.models import ProtectedError

        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            # A route, menu or queue still sends calls here (destinations PROTECT).
            return Response(
                {"code": "in_use", "message": "Something still sends calls here. Change it first."},
                status=status.HTTP_409_CONFLICT,
            )


class TelephonyStatusView(APIView):
    permission_classes = TENANT_PERMISSIONS

    def get(self, request):
        tenant = provisioning.tenant_for_company(request.user.company)
        return Response({
            "provisioned": tenant is not None,
            "tenant": TelephonyStatusSerializer(tenant).data if tenant else None,
        })


class TelephonySettingsView(APIView):
    """GET/PATCH /telephony/settings/ : tenant-wide PBX settings (call recording)."""

    permission_classes = TENANT_PERMISSIONS

    def _tenant(self, request):
        tenant = provisioning.tenant_for_company(request.user.company)
        if tenant is None or tenant.status == PbxTenant.Status.DEPROVISIONED:
            from django.http import Http404

            raise Http404
        return tenant

    def get(self, request):
        return Response(TelephonySettingsSerializer(self._tenant(request)).data)

    def patch(self, request):
        from settings.services import log_system_action

        tenant = self._tenant(request)
        fields = ("recording_mode", "recording_notice", "recording_retention_days")
        before = {f: getattr(tenant, f) for f in fields}
        serializer = TelephonySettingsSerializer(tenant, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            tenant = serializer.save()
            after = {f: getattr(tenant, f) for f in fields}
            if after != before:
                # Retention is applied by the CRM's purge job; only the dialplan settings need a compile.
                if any(after[f] != before[f] for f in ("recording_mode", "recording_notice")):
                    provisioning.request_recompile_for_company(request.user.company, user=request.user)
                log_system_action(
                    "telephony.recording_settings_changed",
                    user=request.user,
                    message=f"Call recording {before['recording_mode']} -> {after['recording_mode']}",
                    metadata={"company_id": tenant.company_id, "before": before, "after": after},
                    ip_address=request.META.get("REMOTE_ADDR"),
                )
        return Response(TelephonySettingsSerializer(tenant).data)


class PbxExtensionViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = PbxExtension.objects.select_related("user")
    serializer_class = PbxExtensionSerializer
    permission_classes = TENANT_PERMISSIONS

    def _related(self, instance):
        return [instance.user] if instance.user_id else []

    def perform_create(self, serializer):
        company = self.request.user.company
        require_quota(
            company,
            "max_pbx_extensions",
            PbxExtension.objects.filter(company=company).count(),
            message="Your plan's extension limit has been reached.",
            error_key="max_pbx_extensions",
        )
        secret = contract.generate_sip_secret()
        with transaction.atomic():
            instance = serializer.save(company=company, sip_secret_encrypted=encrypt_token(secret))
            assert_same_company(instance.company_id, *self._related(instance))
            provisioning.request_recompile_for_company(company, user=self.request.user)
        self._one_time = (instance.pk, secret)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        pk, secret = self._one_time
        data = PbxExtensionSerializer(
            serializer.instance,
            context={**self.get_serializer_context(), "one_time_secret_for": pk, "one_time_secret": secret},
        ).data
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="credentials/rotate")
    def rotate_credentials(self, request, pk=None):
        """New SIP secret; the old one stops working once the node applies the new config."""
        ext = self.get_object()
        secret = contract.generate_sip_secret()
        with transaction.atomic():
            ext.sip_secret_encrypted = encrypt_token(secret)
            ext.save(update_fields=["sip_secret_encrypted", "updated_at"])
            provisioning.request_recompile_for_company(request.user.company, user=request.user)
        data = PbxExtensionSerializer(
            ext, context={**self.get_serializer_context(), "one_time_secret_for": ext.pk, "one_time_secret": secret}
        ).data
        return Response(data)


class PbxTrunkViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = PbxTrunk.objects.all()
    serializer_class = PbxTrunkSerializer
    permission_classes = TENANT_PERMISSIONS

    def perform_create(self, serializer):
        company = self.request.user.company
        require_quota(
            company,
            "max_pbx_trunks",
            PbxTrunk.objects.filter(company=company).count(),
            message="Your plan's trunk limit has been reached.",
            error_key="max_pbx_trunks",
        )
        super().perform_create(serializer)

    def destroy(self, request, *args, **kwargs):
        trunk = self.get_object()
        if trunk.numbers.exists() or trunk.outbound_routes.exists():
            return Response(
                {"code": "in_use", "message": "Remove this trunk's numbers and outbound routes first."},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    TEST_INTERVAL_S = 5

    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        """POST /trunks/{id}/test/ (spec section 6): OPTIONS from the node, plus the trunk's routing."""
        from django.core.cache import cache

        from .services import trunk_test

        trunk = self.get_object()
        # Each test sends real SIP traffic from the node: at most one per trunk every few seconds.
        if not cache.add(f"telephony:trunk-test:{trunk.pk}", 1, timeout=self.TEST_INTERVAL_S):
            return Response({"code": "too_many_tests", "message": "Wait a few seconds before testing again."},
                            status=status.HTTP_429_TOO_MANY_REQUESTS)
        try:
            return Response(trunk_test.run(trunk))
        except calls_service.CallRefused as exc:
            return Response({"code": exc.code, "message": exc.message}, status=exc.status)


class PbxNumberViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = PbxNumber.objects.select_related("trunk")
    serializer_class = PbxNumberSerializer
    permission_classes = TENANT_PERMISSIONS

    def _related(self, instance):
        return [instance.trunk]

    def destroy(self, request, *args, **kwargs):
        number = self.get_object()
        if PbxOutboundRoute.objects.filter(caller_id=number).exists():
            return Response(
                {"code": "in_use", "message": "This number is the caller ID of an outbound route."},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)


def _targets(instance, *prefixes):
    out = []
    for prefix in prefixes:
        for kind in ("extension", "ivr", "queue"):
            target = getattr(instance, f"{prefix}_{kind}" if prefix else kind, None)
            if target is not None:
                out.append(target)
    return out


class PbxInboundRouteViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = PbxInboundRoute.objects.select_related("number", "extension").order_by("number__e164")
    serializer_class = PbxInboundRouteSerializer
    permission_classes = TENANT_PERMISSIONS

    def _related(self, instance):
        return [instance.number, *_targets(instance, "", "closed"),
                *([instance.business_hours] if instance.business_hours_id else [])]


class PbxBusinessHoursViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = PbxBusinessHours.objects.all()
    serializer_class = PbxBusinessHoursSerializer
    permission_classes = TENANT_PERMISSIONS


class PbxIvrMenuViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = PbxIvrMenu.objects.prefetch_related("options")
    serializer_class = PbxIvrMenuSerializer
    permission_classes = TENANT_PERMISSIONS

    def _related(self, instance):
        out = _targets(instance, "timeout") + ([instance.greeting] if instance.greeting_id else [])
        for option in instance.options.all():
            out += _targets(option, "")
        return out


class PbxQueueViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = PbxQueue.objects.prefetch_related("members__extension")
    serializer_class = PbxQueueSerializer
    permission_classes = TENANT_PERMISSIONS

    def _related(self, instance):
        return [*_targets(instance, "overflow"), *(m.extension for m in instance.members.all())]

    @action(detail=True, methods=["put"])
    def members(self, request, pk=None):
        """PUT /queues/{id}/members/ {members: [{extension, penalty}]}: replace the membership (spec section 6)."""
        from .serializers import save_members

        queue = self.get_object()
        serializer = QueueMembersSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            save_members(queue, serializer.validated_data["members"])
            assert_same_company(queue.company_id, *(m.extension for m in queue.members.all()))
            provisioning.request_recompile_for_company(request.user.company, user=request.user)
        return Response(PbxQueueSerializer(queue, context=self.get_serializer_context()).data)


class PbxSoundViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    """
    /telephony/sounds/: the tenant's own greetings. Upload is multipart (``name``,
    ``file``: WAV 16-bit PCM mono 8 kHz, up to 60 s). A greeting reaches the node
    only once a menu plays it, so uploading or renaming one recompiles nothing;
    deleting one a menu still plays is 409 ``in_use``.
    """

    queryset = PbxSound.objects.defer("audio")
    permission_classes = TENANT_PERMISSIONS
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_parsers(self):
        from rest_framework.parsers import FormParser, JSONParser, MultiPartParser

        return [MultiPartParser(), FormParser(), JSONParser()]

    def get_serializer_class(self):
        return PbxSoundUploadSerializer if self.action == "create" else PbxSoundSerializer

    def create(self, request, *args, **kwargs):
        from .services.sounds import MAX_SOUNDS_PER_TENANT

        company = request.user.company
        if PbxSound.objects.filter(company=company).count() >= MAX_SOUNDS_PER_TENANT:
            return Response(
                {"code": "too_many_sounds", "message": f"A company can keep up to {MAX_SOUNDS_PER_TENANT} greetings."},
                status=status.HTTP_409_CONFLICT,
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sound = serializer.save(company=company, created_by=request.user)
        return Response(PbxSoundSerializer(sound).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        serializer.save()  # the name never reaches the node's config

    def perform_destroy(self, instance):
        instance.delete()  # PROTECT: a greeting a menu plays raises ProtectedError (409 in_use)

    @action(detail=True, methods=["get"])
    def audio(self, request, pk=None):
        """The greeting as a PCM WAV for the admin to listen to (small: at most 60 s)."""
        from .services.sounds import to_wav

        sound = get_object_or_404(PbxSound, pk=self.get_object().pk)
        resp = HttpResponse(to_wav(bytes(sound.audio)), content_type="audio/wav")
        resp["Cache-Control"] = "private, no-store"
        return resp


class PbxOutboundRouteViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = PbxOutboundRoute.objects.select_related("trunk", "caller_id")
    serializer_class = PbxOutboundRouteSerializer
    permission_classes = TENANT_PERMISSIONS

    def _related(self, instance):
        return [instance.trunk, instance.caller_id]


class PbxGatewayViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    /telephony/gateways/ (spec section 6): GSM gateway onboarding. Create returns
    the one-time enrollment token; verify marks the gateway's trunk READY once
    the node reports it reachable.
    """

    serializer_class = PbxGatewaySerializer
    permission_classes = TENANT_PERMISSIONS

    def get_queryset(self):
        return PbxGatewayPeer.objects.filter(company_id=self.request.user.company_id).select_related("trunk", "node")

    def _refused(self, exc):
        return Response({"code": exc.code, "message": exc.message}, status=exc.status)

    def _with_token(self, request, peer, token, code=status.HTTP_200_OK):
        data = PbxGatewaySerializer(peer).data
        data["enrollment_token"] = token  # shown once
        data["enrollment_url"] = request.build_absolute_uri(reverse("pbx-gateway-enroll"))
        return Response(data, status=code)

    def create(self, request, *args, **kwargs):
        from .services import gateways

        serializer = PbxGatewayCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            peer, token = gateways.create_gateway(request.user.company, name=serializer.validated_data["name"])
        except calls_service.CallRefused as exc:
            return self._refused(exc)
        provisioning.request_recompile_for_company(request.user.company, user=request.user)
        return self._with_token(request, peer, token, status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        peer = self.get_object()
        trunk = peer.trunk
        if trunk.numbers.exists() or trunk.outbound_routes.exists():
            return Response(
                {"code": "in_use", "message": "Remove this gateway's numbers and outbound routes first."},
                status=status.HTTP_409_CONFLICT,
            )
        from .services.provisioning import nudge_node

        node_id = peer.node_id
        with transaction.atomic():
            trunk.delete()  # the peer goes with it; the node drops it on its next pull
            provisioning.request_recompile_for_company(request.user.company, user=request.user)
        transaction.on_commit(lambda: nudge_node(node_id))
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        from .services import gateways

        try:
            peer = gateways.verify(self.get_object())
        except calls_service.CallRefused as exc:
            return self._refused(exc)
        return Response(PbxGatewaySerializer(peer).data)

    @action(detail=True, methods=["post"], url_path="enrollment/reset")
    def reset_enrollment(self, request, pk=None):
        from .services import gateways

        peer = self.get_object()
        token = gateways.reset_enrollment(peer)
        provisioning.request_recompile_for_company(request.user.company, user=request.user)
        peer.refresh_from_db()
        return self._with_token(request, peer, token)


class GatewayEnrollView(APIView):
    """
    POST /api/v1/public/telephony/gateways/enroll/ {token, public_key}: the on-site
    installer's only call. The one-time token is the authorization; it is spent
    on success. Throttled like a login.
    """

    authentication_classes = []
    permission_classes = []
    throttle_scope = "auth"

    def get_throttles(self):
        from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle

        return [AnonRateThrottle(), ScopedRateThrottle()]

    def post(self, request):
        from .services import gateways

        try:
            peer, config = gateways.enroll(request.data.get("token"), request.data.get("public_key"))
        except calls_service.CallRefused as exc:
            return Response({"code": exc.code, "message": exc.message}, status=exc.status)
        return Response({"gateway": {"id": str(peer.pk), "name": peer.trunk.name}, **config})


# ---------------------------------------------------------------------------
# Platform admin API
# ---------------------------------------------------------------------------

ADMIN_PERMISSIONS = [IsAuthenticated, CanManageTelephony]


class AdminPbxNodeViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = PbxNodeSerializer
    permission_classes = ADMIN_PERMISSIONS

    def get_queryset(self):
        # Explicit order_by: Django drops Meta.ordering on aggregated querysets.
        return PbxNode.objects.annotate(
            tenant_count=Count("tenants", filter=~Q(tenants__status=PbxTenant.Status.DEPROVISIONED))
        ).order_by("name")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        node, node_token, crm_token = provisioning.register_node(**serializer.validated_data)
        data = PbxNodeSerializer(self.get_queryset().get(pk=node.pk)).data
        # Shown once. node_token goes to the node (applier/worker); crm_token is the
        # node's copy of what the CRM sends to its worker API.
        data.update({"node_token": node_token, "crm_token": crm_token})
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="rotate-tokens")
    def rotate_tokens(self, request, pk=None):
        node = self.get_object()
        node_token, crm_token = provisioning.rotate_node_tokens(node)
        return Response({"node_token": node_token, "crm_token": crm_token})


class AdminPbxTenantViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Tenants by company id (the admin panel works in companies)."""

    serializer_class = AdminTenantSerializer
    permission_classes = ADMIN_PERMISSIONS
    lookup_field = "company"

    def get_queryset(self):
        qs = PbxTenant.objects.select_related("company", "home_node", "draining_node").order_by("company__name")
        search = (self.request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(company__name__icontains=search)
        return qs

    def partial_update(self, request, company=None):
        """PATCH /admin/telephony/tenants/<company>/ {fraud_limits?, recording_legal_hold?}."""
        from settings.services import log_system_action

        tenant = self.get_object()
        before = {"fraud_limits": tenant.fraud_limits, "recording_legal_hold": tenant.recording_legal_hold}
        serializer = AdminTenantUpdateSerializer(tenant, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            tenant = serializer.save()
            after = {"fraud_limits": tenant.fraud_limits, "recording_legal_hold": tenant.recording_legal_hold}
            if after["fraud_limits"] != before["fraud_limits"]:
                provisioning.request_recompile_for_company(tenant.company, user=request.user)  # the manifest carries them
            if after != before:
                log_system_action("telephony.tenant_settings_changed", user=request.user,
                                  message="Phone system tenant settings changed",
                                  metadata={"company_id": tenant.company_id, "before": before, "after": after},
                                  ip_address=request.META.get("REMOTE_ADDR"))
        tenant.refresh_from_db()
        return Response(AdminTenantSerializer(tenant).data)

    @action(detail=True, methods=["post"], url_path="migrate-node")
    def migrate_node(self, request, company=None):
        """POST {node}: move the tenant to another node (spec section 6)."""
        from .services.migration import migrate_tenant

        tenant = self.get_object()
        node = PbxNode.objects.filter(pk=request.data.get("node")).first() if request.data.get("node") else None
        if node is None:
            return Response({"code": "invalid_node", "message": "Choose the node to move to."},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            job, report = migrate_tenant(tenant, node, user=request.user)
        except calls_service.CallRefused as exc:
            return Response({"code": exc.code, "message": exc.message}, status=exc.status)
        job.refresh_from_db()
        tenant.refresh_from_db()
        return Response({"tenant": AdminTenantSerializer(tenant).data, "job": PbxJobSerializer(job).data, **report},
                        status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"])
    def repush(self, request, company=None):
        """Re-send paused agents and the outbound block to the home node (e.g. after a node rebuild)."""
        from .services.migration import repush_node_state

        return Response(repush_node_state(self.get_object()))

    def _job_response(self, tenant, kind, request, code=status.HTTP_202_ACCEPTED):
        job = provisioning.request_job(tenant, kind, user=request.user)
        job.refresh_from_db()
        tenant.refresh_from_db()
        return Response({"tenant": AdminTenantSerializer(tenant).data, "job": PbxJobSerializer(job).data}, status=code)

    @action(detail=False, methods=["get"])
    def candidates(self, request):
        """Pilot-approved companies with no tenant yet: what a first provision can target.

        Telephony is globally locked off, so only explicit company exceptions can pass
        the gate. Each row says whether ``provision`` would accept it now (it also needs
        an active subscription), using the same gate as ``provision``.
        """
        from integrations.policy import get_company_integration_access, telephony_exception_company_ids

        companies = Company.objects.filter(pk__in=telephony_exception_company_ids(), pbx_tenant__isnull=True).order_by("name")
        rows = []
        for company in companies:
            access = get_company_integration_access(company, "telephony")
            rows.append({"company": company.id, "company_name": company.name,
                         "eligible": bool(access["enabled"]), "message": "" if access["enabled"] else access["message"]})
        return Response(rows)

    @action(detail=False, methods=["post"], url_path=r"(?P<company_id>\d+)/provision")
    def provision(self, request, company_id=None):
        company = get_object_or_404(Company, pk=company_id)
        if not is_integration_allowed(company, "telephony"):
            return Response(
                {
                    "code": "telephony_not_approved",
                    "message": "Approve this company as the PBX pilot before provisioning it.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        tenant = provisioning.ensure_tenant(company)
        return self._job_response(tenant, provisioning.Job.Kind.PROVISION, request)

    @action(detail=True, methods=["post"])
    def suspend(self, request, company=None):
        """Block outbound calls: at once on the node (best effort), durably by a recompile."""
        from .services.fraud import block_on_node

        tenant = self.get_object()
        reason = str(request.data.get("reason", ""))[:200]
        PbxTenant.objects.filter(pk=tenant.pk).update(suspended_reason=reason)
        block_on_node(tenant)
        return self._job_response(tenant, provisioning.Job.Kind.SUSPEND, request)

    @action(detail=True, methods=["post"])
    def resume(self, request, company=None):
        """The node's own block (fraud detector or suspend) must be lifted first, or calls stay blocked."""
        from .services import pbx_worker_client
        from .services.fraud import unblock_on_node

        tenant = self.get_object()
        try:
            unblock_on_node(tenant)
        except pbx_worker_client.WorkerError:
            return Response({"code": "pbx_unavailable",
                             "message": "The phone system node could not lift its outbound block. Try again."},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return self._job_response(tenant, provisioning.Job.Kind.RESUME, request)

    @action(detail=True, methods=["get"])
    def jobs(self, request, company=None):
        tenant = self.get_object()
        return Response(PbxJobSerializer(tenant.jobs.all()[:50], many=True).data)


class AdminPbxNumberViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """GET /admin/telephony/numbers/?verified=false&search= : the number verification queue (spec section 5)."""

    serializer_class = AdminNumberSerializer
    permission_classes = ADMIN_PERMISSIONS

    def get_queryset(self):
        qs = PbxNumber.objects.select_related("company", "trunk").order_by("verified_at", "-created_at")
        params = self.request.query_params
        if params.get("verified") in ("true", "false"):
            qs = qs.filter(verified_at__isnull=params["verified"] == "false")
        search = (params.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(e164__icontains=search.replace(" ", "")) | Q(company__name__icontains=search))
        return qs


class AdminFraudAlertViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """GET /admin/telephony/fraud-alerts/?open=true (spec section 6); POST …/<id>/resolve/ {note}."""

    serializer_class = AdminFraudAlertSerializer
    permission_classes = ADMIN_PERMISSIONS

    def get_queryset(self):
        qs = PbxFraudAlert.objects.select_related("company", "tenant", "resolved_by")
        if self.request.query_params.get("open") == "true":
            qs = qs.filter(resolved_at__isnull=True)
        return qs

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        from .services.fraud import resolve_alert

        alert = get_object_or_404(PbxFraudAlert, pk=pk)
        resolve_alert(alert, user=request.user, note=str(request.data.get("note") or ""))
        return Response(AdminFraudAlertSerializer(alert).data)


class AdminTelephonyHealthView(APIView):
    """GET /admin/telephony/health/ (spec section 6): each node's worker health and load."""

    permission_classes = ADMIN_PERMISSIONS

    def get(self, request):
        from .services import pbx_worker_client

        out = []
        nodes = PbxNode.objects.annotate(
            tenant_count=Count("tenants", filter=~Q(tenants__status=PbxTenant.Status.DEPROVISIONED))
        ).order_by("name")
        for node in nodes:
            out.append({
                "id": str(node.pk), "name": node.name, "status": node.status, "tenant_count": node.tenant_count,
                "max_tenants": node.max_tenants, "last_seen_at": node.last_seen_at,
                "asterisk_version": node.asterisk_version,
                # Short timeout: one slow node must not hold the console.
                "worker": pbx_worker_client.health(node, timeout=1.5) if node.status != PbxNode.Status.OFFLINE else None,
            })
        return Response({
            "nodes": out,
            "open_fraud_alerts": PbxFraudAlert.objects.filter(resolved_at__isnull=True).count(),
            "numbers_to_verify": PbxNumber.objects.filter(verified_at__isnull=True).count(),
            "failed_tenants": PbxTenant.objects.filter(status=PbxTenant.Status.FAILED).count(),
        })


class AdminVerifyNumberView(APIView):
    """Platform operator confirms a tenant owns a DID (spec §5)."""

    permission_classes = ADMIN_PERMISSIONS

    def post(self, request, pk):
        number = get_object_or_404(PbxNumber, pk=pk)
        verified = bool(request.data.get("verified", True))
        with transaction.atomic():
            number.verified_at = timezone.now() if verified else None
            number.save(update_fields=["verified_at", "updated_at"])
            provisioning.request_recompile_for_company(number.company, user=request.user)
        return Response({"id": str(number.pk), "e164": number.e164, "verified_at": number.verified_at})


# ---------------------------------------------------------------------------
# Internal API (PBX nodes)
# ---------------------------------------------------------------------------


class _IgnoreClientContentNegotiation(BaseContentNegotiation):
    """
    The internal API has fixed response types (JSON, or a gzip artifact returned as
    a plain HttpResponse). Negotiating on the node's Accept header would answer
    ``Accept: application/gzip`` with 406 before the view runs.
    """

    def select_parser(self, request, parsers):
        return parsers[0]

    def select_renderer(self, request, renderers, format_suffix=None):
        return renderers[0], renderers[0].media_type


class _InternalNodeView(APIView):
    """
    Node-token auth, plain JSON (the contract's shapes, not the CRM envelope), no
    throttling (nodes poll on a fixed cadence), and the URL's node id must be the
    authenticated node — a node can never read another node's state.
    """

    authentication_classes = [NodeTokenAuthentication]
    permission_classes = [IsPbxNode]
    renderer_classes = [JSONRenderer]
    content_negotiation_class = _IgnoreClientContentNegotiation
    throttle_classes = []

    def node_or_404(self, request, node_id) -> PbxNode:
        node = request.auth
        if str(node.pk) != str(node_id):
            from django.http import Http404

            raise Http404
        return node


class InternalDesiredStateView(_InternalNodeView):
    def get(self, request, node_id):
        node = self.node_or_404(request, node_id)
        PbxNode.objects.filter(pk=node.pk).update(last_seen_at=timezone.now())
        return Response({
            "node_id": str(node.pk),
            "generated_at": timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tenants": provisioning.desired_state(node),
            "gateway_peers": gateways_service.desired_peers(node),
        })


class InternalArtifactView(_InternalNodeView):
    def get(self, request, node_id, tag, version):
        node = self.node_or_404(request, node_id)
        try:
            rk = contract.realm_key_from_tag(tag)
        except contract.NamingError:
            return Response(status=status.HTTP_404_NOT_FOUND)
        # The home node's artifacts, or the remove artifact of a tenant draining from this node.
        artifact = get_object_or_404(
            PbxArtifact.objects.filter(
                Q(tenant__home_node=node) | Q(tenant__draining_node=node, tenant__draining_version=int(version))
            ),
            tenant__realm_key=rk, version=int(version),
        )
        resp = HttpResponse(provisioning.artifact_blob(artifact), content_type="application/gzip")
        resp["X-Artifact-Sha256"] = artifact.sha256
        resp["Cache-Control"] = "no-store"
        return resp


class InternalApplyResultView(_InternalNodeView):
    def post(self, request, node_id):
        node = self.node_or_404(request, node_id)
        serializer = ApplyResultSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            outcome = provisioning.record_apply_result(node, request.data)
        except provisioning.NotOnNode:
            return Response({"code": "tenant_not_on_node"}, status=status.HTTP_404_NOT_FOUND)
        return Response({"status": outcome})


# ---------------------------------------------------------------------------
# Calls (Phase 2)
# ---------------------------------------------------------------------------

CALL_READ_PERMISSIONS = [IsAuthenticated, HasActiveSubscription, HasTelephonyFeature]


def visible_calls(user):
    """
    Admins and settings supervisors see every call of the company; everyone else
    sees calls they were on (any leg extension, or the originating or answering
    extension) and calls on leads assigned to them.
    """
    qs = PbxCall.objects.filter(company_id=user.company_id)
    if user.is_admin() or user.supervisor_has_permission("manage_settings"):
        return qs
    return qs.filter(
        Q(legs__extension__user=user)
        | Q(extension__user=user)
        | Q(answered_by__user=user)
        | Q(crm_contact__assigned_to=user)
    ).distinct()


class CallCursorPagination(CursorPagination):
    ordering = "-created_at"
    page_size = 50
    page_size_query_param = "limit"
    max_page_size = 200


class PbxCallViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = CALL_READ_PERMISSIONS
    pagination_class = CallCursorPagination

    def get_serializer_class(self):
        return PbxCallDetailSerializer if self.action == "retrieve" else PbxCallSerializer

    def get_queryset(self):
        qs = visible_calls(self.request.user).select_related("extension", "answered_by", "crm_contact")
        params = self.request.query_params
        if params.get("direction"):
            qs = qs.filter(direction=params["direction"])
        if params.get("status"):
            # One status or a comma-separated list (e.g. the three missed outcomes).
            qs = qs.filter(status__in=[s for s in params["status"].split(",") if s][:10])
        if params.get("client", "").isdigit():
            qs = qs.filter(crm_contact_id=int(params["client"]))
        if params.get("extension"):
            qs = qs.filter(Q(extension__number=params["extension"]) | Q(answered_by__number=params["extension"]))
        if self.action == "retrieve":
            qs = qs.prefetch_related("legs__extension", "recordings")
        return qs

    @action(detail=True, methods=["get"])
    def recordings(self, request, pk=None):
        call = self.get_object()
        return Response(PbxRecordingSerializer(call.recordings.all(), many=True).data)

    def create(self, request, *args, **kwargs):
        """
        POST /telephony/calls/ {destination, crm_contact_id?} : click-to-call.
        202 with the queued call; the agent's own phone rings first. An
        ``Idempotency-Key`` header makes retries return the same call.
        """
        from django.core.cache import cache

        idem = (request.headers.get("Idempotency-Key") or "").strip()[:100]
        idem_key = f"telephony:idem:call:{request.user.pk}:{idem}" if idem else None
        if idem_key and (existing := cache.get(idem_key)):
            call = visible_calls(request.user).filter(pk=existing).first()
            if call is not None:
                return Response(PbxCallSerializer(call).data, status=status.HTTP_202_ACCEPTED)
        try:
            call = calls_service.place_call(
                request.user,
                destination=request.data.get("destination"),
                crm_contact_id=request.data.get("crm_contact_id"),
            )
        except calls_service.CallRefused as exc:
            return Response({"code": exc.code, "message": exc.message}, status=exc.status)
        if idem_key:
            cache.set(idem_key, str(call.pk), timeout=600)
        call = PbxCall.objects.select_related("extension", "answered_by", "crm_contact").get(pk=call.pk)
        return Response(PbxCallSerializer(call).data, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"])
    def hangup(self, request, pk=None):
        call = self.get_object()
        try:
            calls_service.hang_up(request.user, call)
        except calls_service.CallRefused as exc:
            return Response({"code": exc.code, "message": exc.message}, status=exc.status)
        return Response({"id": str(call.pk), "status": "hanging_up"}, status=status.HTTP_202_ACCEPTED)


class TelephonyMeView(APIView):
    """GET /telephony/me/ : the caller's own extension and what their UI may offer."""

    permission_classes = CALL_READ_PERMISSIONS

    def get(self, request):
        tenant = provisioning.tenant_for_company(request.user.company)
        ext = calls_service.user_extension(request.user)
        live = tenant is not None and tenant.status in (PbxTenant.Status.ACTIVE, PbxTenant.Status.SUSPENDED)
        return Response({
            "provisioned": live,
            "outbound_suspended": bool(tenant and (tenant.outbound_suspended or tenant.status == PbxTenant.Status.SUSPENDED)),
            "extension": {
                "id": str(ext.pk),
                "number": ext.number,
                "device_type": ext.device_type,
                "registered": ext.registered,
                "agent_status": ext.agent_status,
            } if ext else None,
            "can_click_to_call": bool(live and ext and not (tenant.outbound_suspended)),
            "webrtc": bool(live and ext and ext.device_type == "webrtc"),
            "recording_mode": tenant.recording_mode if tenant else "off",
        })


class AgentPresenceView(APIView):
    """GET /telephony/agents/status/ : the company's extensions with status, registration and on-call (spec section 6)."""

    permission_classes = CALL_READ_PERMISSIONS

    def get(self, request):
        from .services.agents import presence

        return Response({"agents": presence(request.user.company_id)})


class WallboardView(APIView):
    """
    GET /telephony/wallboard/ : live calls, queue waits and today's queue stats,
    agents. Re-read when the ``telephony`` sync slice moves. Live calls are the
    ones the caller may see (``visible_calls``); queue counts are company-wide.
    """

    permission_classes = CALL_READ_PERMISSIONS

    def get(self, request):
        from .services.wallboard import wallboard

        resp = Response(wallboard(request.user, visible_calls(request.user)))
        resp["Cache-Control"] = "private, no-store"
        return resp


class MyAgentStatusView(APIView):
    """GET/PUT /telephony/agents/me/status/ {status: available|paused}; only the caller's own extension."""

    permission_classes = CALL_READ_PERMISSIONS

    def get(self, request):
        ext = calls_service.user_extension(request.user)
        if ext is None:
            return Response({"code": "no_extension", "message": "You do not have a phone extension."},
                            status=status.HTTP_403_FORBIDDEN)
        return Response({"status": ext.agent_status, "changed_at": ext.agent_status_changed_at})

    def put(self, request):
        from .services.agents import set_my_status

        try:
            ext = set_my_status(request.user, request.data.get("status"))
        except calls_service.CallRefused as exc:
            return Response({"code": exc.code, "message": exc.message}, status=exc.status)
        return Response({"status": ext.agent_status, "changed_at": ext.agent_status_changed_at})


class WebRtcCredentialsView(APIView):
    """GET /telephony/me/webrtc-credentials/ (contract section 4); cached per user until refresh_after."""

    permission_classes = CALL_READ_PERMISSIONS

    def get(self, request):
        from .services.webrtc import CredentialsUnavailable, credentials_for

        try:
            data = credentials_for(request.user)
        except CredentialsUnavailable as exc:
            return Response({"code": exc.code, "message": exc.message}, status=exc.status)
        resp = Response(data)
        resp["Cache-Control"] = "private, no-store"
        return resp


class TelephonyUsageView(APIView):
    """GET /telephony/usage/ (spec section 6): this month's minutes, quotas in use, the last 30 days."""

    permission_classes = TENANT_PERMISSIONS

    def get(self, request):
        from .services.usage import usage_report

        return Response(usage_report(request.user.company))


class PbxRecordingDeleteView(APIView):
    """DELETE /telephony/recordings/<id>/ : admins remove a recording (object first, audited)."""

    permission_classes = TENANT_PERMISSIONS

    def delete(self, request, pk):
        recording = get_object_or_404(PbxRecording, pk=pk, company_id=request.user.company_id)
        tenant = recording.tenant
        if tenant.recording_legal_hold:
            return Response({"code": "legal_hold", "message": "Recordings are on legal hold and cannot be deleted."},
                            status=status.HTTP_409_CONFLICT)
        try:
            recordings_service.delete_recording(recording, user=request.user, reason="deleted by an admin")
        except recordings_service.StorageError:
            return Response({"code": "storage_unavailable", "message": "The recording could not be deleted. Try again."},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        recordings_service.enforce_quota(tenant)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PbxRecordingAccessView(APIView):
    """POST /telephony/recordings/<id>/access/ returns {url, expires_at} (spec section 6)."""

    permission_classes = CALL_READ_PERMISSIONS

    def post(self, request, pk):
        recording = get_object_or_404(PbxRecording, pk=pk, call__in=visible_calls(request.user).values("pk"))
        try:
            data = recordings_service.access_url(
                recording, user=request.user, build_absolute_uri=request.build_absolute_uri
            )
        except RuntimeError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(data)


def recording_stream(request, token):
    """
    Development playback (local recordings storage): the signed, expiring token is
    the authorization. With object storage the client gets a presigned URL and
    never reaches this view.
    """
    from django.core.exceptions import SuspiciousFileOperation
    from django.http import FileResponse, Http404

    from integrations.storage.recordings import storage

    recording = recordings_service.resolve_stream_token(token)
    if recording is None or recordings_service.is_object_storage():
        raise Http404
    store = storage()
    try:
        if not store.exists(recording.storage_key):
            raise Http404
        stream = store.open(recording.storage_key, "rb")
    except SuspiciousFileOperation:
        raise Http404
    resp = FileResponse(stream, content_type=recordings_service.content_type(recording))
    resp["Cache-Control"] = "private, no-store"
    return resp


class PbxEventCursorPagination(CursorPagination):
    ordering = "received_at"
    page_size = 100


class PbxEventReplayViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /telephony/events/?cursor= : durable event log for the company (admins)."""

    permission_classes = TENANT_PERMISSIONS
    serializer_class = PbxCallEventSerializer
    pagination_class = PbxEventCursorPagination

    def get_queryset(self):
        return PbxCallEvent.objects.filter(company_id=self.request.user.company_id)


class InternalEventsView(_InternalNodeView):
    """POST /internal/telephony/events : batched call-state changes (contract section 5)."""

    def post(self, request):
        try:
            result = events_service.ingest_batch(request.auth, request.data)
        except events_service.InvalidEvent as exc:
            return Response({"code": "invalid_request", "detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result.as_dict())


class InternalRecordingsView(_InternalNodeView):
    """POST /internal/telephony/recordings : metadata of uploaded recordings (contract section 7)."""

    def post(self, request):
        try:
            result = recordings_service.ingest_recordings(request.auth, request.data)
        except events_service.InvalidEvent as exc:
            return Response({"code": "invalid_request", "detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result.as_dict())
