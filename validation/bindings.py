"""Attach catalog form ids to write serializers."""

from validation.drf import SERIALIZER_FORMS, CatalogValidatedSerializerMixin


def _bind(cls, form_id: str) -> None:
    if CatalogValidatedSerializerMixin not in cls.__mro__:
        cls.__bases__ = (CatalogValidatedSerializerMixin,) + tuple(cls.__bases__)
    SERIALIZER_FORMS[cls] = form_id
    cls.validation_form = form_id


def install() -> None:
    if getattr(install, "_done", False):
        return
    install._done = True

    from accounts.serializers import (
        ChangePasswordSerializer,
        CreateLimitedAdminSerializer,
        CreateSupervisorSerializer,
        ForgotPasswordSerializer,
        RegisterCompanySerializer,
        ResetPasswordSerializer,
        UserSerializer,
    )
    from companies.serializers import CompanySerializer
    from crm.deals.serializers import DealWriteSerializer
    from crm.serializers import (
        CampaignSerializer,
        ClientCallSerializer,
        ClientFieldVisitSerializer,
        ClientSerializer,
        ClientTaskSerializer,
        ClientVisitSerializer,
        TaskSerializer,
    )
    from demo_bookings.serializers import DemoBookingCreateSerializer
    from integrations.serializers import (
        MessageTemplateSerializer,
        OpenAISettingsSerializer,
        TwilioSettingsSerializer,
    )
    from platform_content.serializers import (
        GuideArticleSerializer,
        GuideCategorySerializer,
        NewsPostSerializer,
        PageHelpVideoSerializer,
    )
    from products.serializers import ProductCategorySerializer, ProductSerializer, SupplierSerializer
    from real_estate.serializers import DeveloperSerializer, OwnerSerializer, ProjectSerializer, UnitSerializer
    from services.serializers import ServicePackageSerializer, ServiceProviderSerializer, ServiceSerializer
    from settings.serializers import (
        BillingSettingsSerializer,
        CallMethodSerializer,
        ChannelSerializer,
        DealLostReasonSerializer,
        DealPipelineSerializer,
        DealStageSerializer,
        LeadStageSerializer,
        LeadStatusSerializer,
        SystemSettingsSerializer,
        TagSerializer,
        VisitTypeSerializer,
    )
    from subscriptions.serializers import (
        BroadcastSerializer,
        PaymentGatewaySerializer,
        PlanSerializer,
        TrialCodeBatchGenerateSerializer,
        TrialCodeCreateSerializer,
    )
    from support.serializers import SupportTicketSerializer

    pairs = [
        (UserSerializer, "user.upsert"),
        (RegisterCompanySerializer, "auth.register"),
        (ForgotPasswordSerializer, "auth.forgot_password"),
        (ResetPasswordSerializer, "auth.reset_password"),
        (ChangePasswordSerializer, "auth.change_password"),
        (CreateLimitedAdminSerializer, "limited_admin.create"),
        (CreateSupervisorSerializer, "supervisor.create"),
        (CompanySerializer, "tenant.upsert"),
        (ClientSerializer, "lead.upsert"),
        (DealWriteSerializer, "deal.upsert"),
        (TaskSerializer, "task.upsert"),
        (CampaignSerializer, "campaign.upsert"),
        (ClientTaskSerializer, "lead_action.create"),
        (ClientCallSerializer, "lead_call.create"),
        (ClientVisitSerializer, "lead_visit.create"),
        (ClientFieldVisitSerializer, "field_visit.create"),
        (ChannelSerializer, "channel.upsert"),
        (LeadStageSerializer, "stage.upsert"),
        (LeadStatusSerializer, "status.upsert"),
        (CallMethodSerializer, "call_method.upsert"),
        (VisitTypeSerializer, "visit_type.upsert"),
        (TagSerializer, "tag.upsert"),
        (DealPipelineSerializer, "deal_pipeline.upsert"),
        (DealStageSerializer, "deal_stage.upsert"),
        (DealLostReasonSerializer, "deal_lost_reason.upsert"),
        (BillingSettingsSerializer, "billing_settings.update"),
        (SystemSettingsSerializer, "system_settings.update"),
        (ProductCategorySerializer, "product_category.upsert"),
        (ProductSerializer, "product.upsert"),
        (SupplierSerializer, "supplier.upsert"),
        (ServiceSerializer, "service.upsert"),
        (ServicePackageSerializer, "service_package.upsert"),
        (ServiceProviderSerializer, "service_provider.upsert"),
        (DeveloperSerializer, "developer.upsert"),
        (ProjectSerializer, "project.upsert"),
        (UnitSerializer, "unit.upsert"),
        (OwnerSerializer, "owner.upsert"),
        (SupportTicketSerializer, "support_ticket.create"),
        (GuideCategorySerializer, "guide_category.upsert"),
        (GuideArticleSerializer, "guide_article.upsert"),
        (NewsPostSerializer, "news_post.upsert"),
        (PageHelpVideoSerializer, "page_help_video.upsert"),
        (PlanSerializer, "plan.upsert"),
        (PaymentGatewaySerializer, "payment_gateway.create"),
        (TrialCodeCreateSerializer, "trial_code.create"),
        (TrialCodeBatchGenerateSerializer, "trial_code.batch"),
        (BroadcastSerializer, "broadcast.create"),
        (TwilioSettingsSerializer, "twilio_settings.update"),
        (MessageTemplateSerializer, "whatsapp_template.upsert"),
        (OpenAISettingsSerializer, "openai_settings.update"),
        (DemoBookingCreateSerializer, "demo_booking.create"),
    ]
    for cls, form_id in pairs:
        _bind(cls, form_id)
