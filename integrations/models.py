from django.db import models
from django.contrib.auth import get_user_model
from companies.models import Company
from .encryption import encrypt_token, decrypt_token

User = get_user_model()


class IntegrationPlatform(models.TextChoices):
    """منصات التكامل المدعومة"""
    META = 'meta', 'Meta (Facebook/Instagram)'
    TIKTOK = 'tiktok', 'TikTok'
    WHATSAPP = 'whatsapp', 'WhatsApp Business'
    API = 'api', 'Lead API / Custom Form'
    MUJEB = 'mujeb', 'Mujeb'
    # Omni-Channel Inbox (Instagram DM + Messenger). Separate from META on purpose:
    # a different Meta app, and unique_together (company, platform, external_account_id)
    # keeps a tenant's Lead Ads connection independent of their inbox connection.
    META_INBOX = 'meta_inbox', 'Meta Inbox (Instagram DM / Messenger)'


class IntegrationAccount(models.Model):
    """
    نموذج لحسابات التكامل المتصلة.
    At most one account per (company, platform); different platforms are independent.
    """
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='integration_accounts',
        help_text="الشركة المالكة لهذا الحساب"
    )
    platform = models.CharField(
        max_length=50,
        choices=IntegrationPlatform.choices,
        help_text="نوع المنصة (Meta, TikTok, WhatsApp)"
    )
    name = models.CharField(
        max_length=255,
        help_text="اسم الحساب (مثل: صفحة الفيسبوك الرئيسية)"
    )
    
    # بيانات OAuth/API
    access_token = models.TextField(
        blank=True,
        null=True,
        help_text="Access Token للوصول إلى API المنصة"
    )
    refresh_token = models.TextField(
        blank=True,
        null=True,
        help_text="Refresh Token لتجديد Access Token"
    )
    token_expires_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="تاريخ انتهاء صلاحية Token"
    )
    
    # بيانات إضافية حسب المنصة
    external_account_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="معرف الحساب في المنصة الخارجية"
    )
    external_account_name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="اسم الحساب في المنصة الخارجية"
    )
    # بيانات إضافية (JSON) لتخزين معلومات إضافية حسب المنصة
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="معلومات إضافية خاصة بالمنصة (JSON)"
    )
    
    # حالة الحساب (يكون disconnected حتى يكتمل OAuth بنجاح)
    status = models.CharField(
        max_length=20,
        choices=[
            ('connected', 'Connected'),
            ('disconnected', 'Disconnected'),
            ('error', 'Error'),
            ('expired', 'Token Expired'),
        ],
        default='disconnected',
        help_text="حالة الاتصال"
    )
    
    # معلومات إضافية
    is_active = models.BooleanField(
        default=True,
        help_text="هل الحساب نشط؟"
    )
    last_sync_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="آخر مرة تم فيها مزامنة البيانات"
    )
    error_message = models.TextField(
        blank=True,
        null=True,
        help_text="رسالة الخطأ إن وجدت"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_integration_accounts',
        help_text="المستخدم الذي أنشأ هذا الحساب"
    )
    
    class Meta:
        db_table = 'integration_accounts'
        verbose_name = 'Integration Account'
        verbose_name_plural = 'Integration Accounts'
        ordering = ['-created_at']
        # منع تكرار الحساب نفسه للشركة نفسها
        unique_together = [
            ['company', 'platform', 'external_account_id'],
        ]
        indexes = [
            models.Index(fields=['company', 'platform']),
            models.Index(fields=['status']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return f"{self.company.name} - {self.get_platform_display()} - {self.name}"
    
    def is_token_expired(self):
        """التحقق من انتهاء صلاحية Token"""
        if not self.token_expires_at:
            return False
        from django.utils import timezone
        return timezone.now() >= self.token_expires_at
    
    def refresh_access_token_if_needed(self):
        """تجديد Access Token إذا انتهت صلاحيته (Meta/WhatsApp: fb_exchange_token)."""
        if not self.is_token_expired():
            return False
        from .services.token_lifecycle import refresh_account_token

        refresh_account_token(self)
        return True
    
    def get_access_token(self):
        """الحصول على Access Token (مفكوك التشفير)"""
        if not self.access_token:
            return None
        return decrypt_token(self.access_token)
    
    def set_access_token(self, token):
        """حفظ Access Token (مشفر)"""
        if token:
            self.access_token = encrypt_token(token)
        else:
            self.access_token = None
    
    def get_refresh_token(self):
        """الحصول على Refresh Token (مفكوك التشفير)"""
        if not self.refresh_token:
            return None
        return decrypt_token(self.refresh_token)
    
    def set_refresh_token(self, token):
        """حفظ Refresh Token (مشفر)"""
        if token:
            self.refresh_token = encrypt_token(token)
        else:
            self.refresh_token = None


class IntegrationLog(models.Model):
    """
    سجل لعمليات التكامل (للتحليل والتصحيح)
    """
    account = models.ForeignKey(
        IntegrationAccount,
        on_delete=models.CASCADE,
        related_name='logs'
    )
    action = models.CharField(
        max_length=100,
        help_text="نوع العملية (sync, post, fetch, error)"
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ('success', 'Success'),
            ('error', 'Error'),
            ('pending', 'Pending'),
        ]
    )
    message = models.TextField(blank=True, null=True)
    response_data = models.JSONField(default=dict, blank=True)
    error_details = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'integration_logs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['account', 'action']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"{self.account} - {self.action} - {self.status}"


class CompanyLeadApiKey(models.Model):
    """API key for external apps to submit leads via POST /integrations/leads/inbound/."""

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="lead_api_keys",
    )
    name = models.CharField(
        max_length=128,
        help_text="Label for this key (e.g. Website form, Mobile app)",
    )
    key_prefix = models.CharField(
        max_length=16,
        help_text="First characters of the key for display in the UI",
    )
    key_suffix = models.CharField(
        max_length=8,
        blank=True,
        default="",
        help_text="Last characters of the key for display in the UI",
    )
    key_hash = models.CharField(
        max_length=64,
        db_index=True,
        help_text="SHA-256 hash of the full API key",
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_lead_api_keys",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "company_lead_api_keys"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "is_active"]),
        ]

    def __str__(self):
        return f"{self.company_id} - {self.name} ({self.key_prefix}…)"


class OAuthState(models.Model):
    """
    تخزين state لـ OAuth callback حتى يعمل مع عدة workers (مشترك عبر قاعدة البيانات).
    يُحذف بعد الاستخدام أو بعد انتهاء الصلاحية.
    """
    state = models.CharField(max_length=64, unique=True, db_index=True)
    account_id = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'integration_oauth_states'
        ordering = ['-created_at']

    def __str__(self):
        return f"state={self.state[:8]}... account={self.account_id}"


class WhatsAppAccount(models.Model):
    """
    جدول حسابات واتساب (Embedded Signup Flow).
    كل صف = رقم واتساب واحد مرتبط بـ tenant (company).
    يُستخدم للويب هوك (استخراج tenant من phone_number_id) ولإرسال الرسائل.
    """
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='whatsapp_accounts',
        help_text="الشركة (tenant) المالكة لهذا الرقم",
    )
    waba_id = models.CharField(
        max_length=64,
        help_text="WhatsApp Business Account ID من Meta",
    )
    phone_number_id = models.CharField(
        max_length=64,
        unique=True,
        help_text="Phone Number ID من Meta (يُستخدم في الويب هوك والإرسال)",
    )
    access_token = models.TextField(
        blank=True,
        null=True,
        help_text="Permanent Access Token (مخزن مشفراً)",
    )
    business_id = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text="معرف Business في Meta إن وُجد",
    )
    display_phone_number = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="رقم الهاتف المعروض للمستخدم",
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ('connected', 'Connected'),
            ('disconnected', 'Disconnected'),
            ('error', 'Error'),
        ],
        default='connected',
    )
    integration_account = models.ForeignKey(
        IntegrationAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='whatsapp_accounts',
        help_text="حساب التكامل المرتبط (من OAuth)",
    )
    calling_enabled = models.BooleanField(
        default=False,
        help_text="Whether WhatsApp Cloud Calling is enabled for this phone number",
    )
    call_hours_enabled = models.BooleanField(
        default=False,
        help_text="When true, enforce weekly call hours (synced to Meta call_hours when possible).",
    )
    call_hours_timezone = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="IANA timezone for call hours (e.g. Asia/Baghdad).",
    )
    call_hours_weekly = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-day schedule: {monday: {closed, open, close}, ...} times HH:MM.",
    )
    out_of_hours_message = models.TextField(
        blank=True,
        default="",
        help_text="Text sent to the customer when an inbound call arrives outside call hours.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'whatsapp_accounts'
        verbose_name = 'WhatsApp Account'
        verbose_name_plural = 'WhatsApp Accounts'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['phone_number_id']),
        ]

    def __str__(self):
        return f"{self.company.name} - {self.display_phone_number or self.phone_number_id}"

    def get_access_token(self):
        """الحصول على Access Token (مفكوك التشفير)"""
        if not self.access_token:
            return None
        return decrypt_token(self.access_token)

    def set_access_token(self, token):
        """حفظ Access Token (مشفر)"""
        if token:
            self.access_token = encrypt_token(token)
        else:
            self.access_token = None


class SmsProvider(models.TextChoices):
    TWILIO = 'twilio', 'Twilio'
    OTPIQ = 'otpiq', 'OTPIQ'


class TwilioSettings(models.Model):
    """
    Per-company SMS settings (Twilio or OTPIQ).
    """
    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name='twilio_settings',
        help_text="الشركة المالكة للإعدادات",
    )
    account_sid = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text="Twilio Account SID",
    )
    twilio_number = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="رقم الإرسال (Twilio Number)",
    )
    auth_token = models.TextField(
        blank=True,
        null=True,
        help_text="Auth Token (مخزن مشفراً)",
    )
    sender_id = models.CharField(
        max_length=11,
        blank=True,
        null=True,
        help_text="اسم المرسل (Sender ID) - اختياري",
    )
    provider = models.CharField(
        max_length=16,
        choices=SmsProvider.choices,
        default=SmsProvider.TWILIO,
        help_text="Active SMS provider for this company",
    )
    otpiq_api_key = models.TextField(
        blank=True,
        null=True,
        help_text="OTPIQ API key (stored encrypted)",
    )
    otpiq_route_provider = models.CharField(
        max_length=32,
        default='sms',
        help_text="OTPIQ provider route (e.g. sms, whatsapp-sms)",
    )
    is_enabled = models.BooleanField(
        default=False,
        help_text="الربط مفعل",
    )
    lead_created_sms_enabled = models.BooleanField(
        default=False,
        help_text="Send an automated SMS when a new lead (Client) is created.",
    )
    lead_created_sms_template = models.TextField(
        blank=True,
        default="Hello [first_name], we'll contact you soon!",
        help_text=(
            "SMS body template for new leads. Placeholders: [name], [first_name], [phone], "
            "[lead_company_name], [profession], [status], [company_name], [budget], [priority], [type], [source]."
        ),
    )
    lead_created_whatsapp_enabled = models.BooleanField(
        default=False,
        help_text="Send an automated WhatsApp template when a new lead (Client) is created.",
    )
    lead_created_whatsapp_template = models.ForeignKey(
        "MessageTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Approved WhatsApp MessageTemplate used for new-lead auto welcome.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'twilio_settings'
        verbose_name = 'Twilio SMS Settings'
        verbose_name_plural = 'Twilio SMS Settings'

    def __str__(self):
        return f"{self.company.name} - Twilio SMS"

    def get_auth_token(self):
        if not self.auth_token:
            return None
        return decrypt_token(self.auth_token)

    def set_auth_token(self, token):
        if token:
            self.auth_token = encrypt_token(token)
        else:
            self.auth_token = None

    def get_otpiq_api_key(self):
        if not self.otpiq_api_key:
            return None
        return decrypt_token(self.otpiq_api_key)

    def set_otpiq_api_key(self, token):
        if token:
            self.otpiq_api_key = encrypt_token(token)
        else:
            self.otpiq_api_key = None


class OpenAISettings(models.Model):
    """Per-company OpenAI (ChatGPT) BYOK settings for AI lead analysis."""

    DEFAULT_MODEL = "gpt-4o-mini"
    DEFAULT_MAX_LEADS_PER_RUN = 20

    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="openai_settings",
        help_text="Company that owns these OpenAI settings",
    )
    api_key = models.TextField(
        blank=True,
        null=True,
        help_text="OpenAI API key (stored encrypted)",
    )
    is_enabled = models.BooleanField(
        default=False,
        help_text="Whether AI lead analysis is enabled",
    )
    model = models.CharField(
        max_length=64,
        default=DEFAULT_MODEL,
        help_text="OpenAI model id (e.g. gpt-4o-mini)",
    )
    auto_analyze_enabled = models.BooleanField(
        default=True,
        help_text="Run analysis on scheduled cron when enabled",
    )
    max_leads_per_run = models.PositiveIntegerField(
        default=DEFAULT_MAX_LEADS_PER_RUN,
        help_text="Maximum leads analyzed per run (cost guard)",
    )
    last_analysis_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "openai_settings"
        verbose_name = "OpenAI Settings"
        verbose_name_plural = "OpenAI Settings"

    def __str__(self):
        return f"{self.company.name} - OpenAI"

    def get_api_key(self):
        if not self.api_key:
            return None
        return decrypt_token(self.api_key)

    def set_api_key(self, key):
        if key:
            self.api_key = encrypt_token(key)
        else:
            self.api_key = None


class AIInsightStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    DISMISSED = "dismissed", "Dismissed"
    EXPIRED = "expired", "Expired"


class AIInsightPriorityLevel(models.TextChoices):
    HIGH = "high", "High"
    MEDIUM = "medium", "Medium"
    LOW = "low", "Low"


class ClientAIInsight(models.Model):
    """AI-generated insight and smart reminder suggestion for a lead."""

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="client_ai_insights",
    )
    client = models.ForeignKey(
        "crm.Client",
        on_delete=models.CASCADE,
        related_name="ai_insights",
    )
    ai_score = models.PositiveSmallIntegerField(
        default=0,
        help_text="AI priority score 0-100",
    )
    priority_level = models.CharField(
        max_length=10,
        choices=AIInsightPriorityLevel.choices,
        default=AIInsightPriorityLevel.MEDIUM,
    )
    summary = models.TextField(blank=True, default="")
    summary_en = models.TextField(blank=True, default="")
    summary_ar = models.TextField(blank=True, default="")
    reasoning = models.TextField(blank=True, null=True)
    reasoning_en = models.TextField(blank=True, null=True)
    reasoning_ar = models.TextField(blank=True, null=True)
    suggested_reminder_date = models.DateTimeField(blank=True, null=True)
    suggested_task_notes = models.TextField(blank=True, null=True)
    suggested_task_notes_en = models.TextField(blank=True, null=True)
    suggested_task_notes_ar = models.TextField(blank=True, null=True)
    source_snapshot_hash = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=AIInsightStatus.choices,
        default=AIInsightStatus.PENDING,
    )
    approved_at = models.DateTimeField(blank=True, null=True)
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="approved_ai_insights",
    )
    created_client_task = models.ForeignKey(
        "crm.ClientTask",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="source_ai_insight",
    )
    analyzed_at = models.DateTimeField(auto_now_add=True)
    model_used = models.CharField(max_length=64, blank=True, default="")
    tokens_used = models.PositiveIntegerField(blank=True, null=True)

    class Meta:
        db_table = "client_ai_insight"
        ordering = ["-ai_score", "-analyzed_at"]
        indexes = [
            models.Index(fields=["company", "status", "-ai_score"]),
            models.Index(fields=["client", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["client"],
                condition=models.Q(status=AIInsightStatus.PENDING),
                name="unique_pending_ai_insight_per_client",
            ),
        ]

    def __str__(self):
        return f"AI insight #{self.pk} client={self.client_id} ({self.status})"


class AIManagementReport(models.Model):
    """Latest AI-generated management dashboard report for a company (owner view)."""

    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="ai_management_report",
    )
    generated_at = models.DateTimeField(auto_now=True)
    payload = models.JSONField(default=dict, blank=True)
    model_used = models.CharField(max_length=64, blank=True, default="")
    tokens_used = models.PositiveIntegerField(blank=True, null=True)

    class Meta:
        db_table = "ai_management_report"

    def __str__(self):
        return f"AI management report company={self.company_id}"


class MessageSendSource(models.TextChoices):
    MANUAL = "manual", "Manual"
    CAMPAIGN = "campaign", "Campaign"
    AUTO_WELCOME = "auto_welcome", "Auto welcome"


class CampaignBatchStatus(models.TextChoices):
    """Lifecycle of a MessageCampaignBatch.

    SENT is the default for owner/supervisor instant-send batches (unchanged
    legacy behavior - the client-side loop sends immediately, then logs the
    batch). The rest of the states belong to the restricted-staff
    submit-for-approval flow.
    """
    SENT = "sent", "Sent"
    PENDING_APPROVAL = "pending_approval", "Pending approval"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    SENDING = "sending", "Sending"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class MessageCampaignBatch(models.Model):
    """One bulk send action from Messaging Center → Message Campaign."""

    CHANNEL_SMS = "sms"
    CHANNEL_WHATSAPP = "whatsapp"
    CHANNEL_CHOICES = [
        (CHANNEL_SMS, "SMS"),
        (CHANNEL_WHATSAPP, "WhatsApp"),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="message_campaign_batches",
    )
    channel = models.CharField(max_length=16, choices=CHANNEL_CHOICES)
    message_preview = models.TextField(blank=True, default="")
    recipient_count = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="message_campaign_batches",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # --- Approval workflow (restricted staff submit-for-approval flow) ---
    status = models.CharField(
        max_length=20,
        choices=CampaignBatchStatus.choices,
        default=CampaignBatchStatus.SENT,
    )
    requires_approval = models.BooleanField(
        default=False,
        help_text="True only for the restricted-staff submit-for-approval flow.",
    )
    requested_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_campaign_batches",
    )
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_campaign_batches",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")
    submitted_at = models.DateTimeField(null=True, blank=True)
    resubmitted_at = models.DateTimeField(null=True, blank=True)
    audience_snapshot = models.JSONField(
        default=list,
        blank=True,
        help_text="[{client_id, phone_number, name}] frozen at submit/resubmit time.",
    )
    message_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="SMS: {body}. WhatsApp: {template_id, phone_number_id, body_parameters}.",
    )

    class Meta:
        db_table = "message_campaign_batches"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "created_at"]),
            models.Index(fields=["company", "status", "-created_at"]),
        ]

    def __str__(self):
        return f"Campaign batch {self.id} ({self.channel}) company={self.company_id}"


class MessageCampaignFailure(models.Model):
    """Recipient that failed during a campaign send (no SMS/WA message row created)."""

    batch = models.ForeignKey(
        MessageCampaignBatch,
        on_delete=models.CASCADE,
        related_name="failures",
    )
    client = models.ForeignKey(
        "crm.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaign_send_failures",
    )
    phone_number = models.CharField(max_length=20, blank=True, default="")
    error = models.CharField(max_length=512, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "message_campaign_failures"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["batch", "created_at"]),
        ]


class LeadSMSMessage(models.Model):
    """
    رسالة SMS مرسلة إلى عميل محتمل (Lead).
    تُخزّن جميع الرسائل المرسلة عبر Twilio وتُعرض في تايملاين الليد.
    """
    DIRECTION_OUTBOUND = 'outbound'
    DIRECTION_INBOUND = 'inbound'

    client = models.ForeignKey(
        'crm.Client',
        on_delete=models.CASCADE,
        related_name='sms_messages',
        help_text="العميل المحتمل (الليد)",
    )
    phone_number = models.CharField(
        max_length=20,
        help_text="رقم الهاتف الذي أُرسلت إليه الرسالة",
    )
    body = models.TextField(
        help_text="نص الرسالة",
    )
    direction = models.CharField(
        max_length=10,
        choices=[
            (DIRECTION_OUTBOUND, 'Outbound'),
            (DIRECTION_INBOUND, 'Inbound'),
        ],
        default=DIRECTION_OUTBOUND,
    )
    provider = models.CharField(
        max_length=16,
        choices=SmsProvider.choices,
        blank=True,
        null=True,
        help_text="SMS provider used to send this message",
    )
    external_message_id = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text="Provider message id (Twilio SID or OTPIQ smsId)",
    )
    twilio_sid = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text="معرف الرسالة من Twilio",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_sms_messages',
        help_text="المستخدم الذي أرسل الرسالة",
    )
    send_source = models.CharField(
        max_length=20,
        choices=MessageSendSource.choices,
        default=MessageSendSource.MANUAL,
    )
    campaign_batch = models.ForeignKey(
        MessageCampaignBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sms_messages",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lead_sms_messages'
        verbose_name = 'Lead SMS Message'
        verbose_name_plural = 'Lead SMS Messages'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['client', 'created_at']),
        ]

    def __str__(self):
        return f"SMS to {self.phone_number} @ {self.created_at}"


class LeadWhatsAppMessage(models.Model):
    """
    رسالة واتساب مرتبطة بعميل (Lead).
    تُخزّن الرسائل الواردة والصادرة عبر WhatsApp Business API وتُعرض في تايملاين الليد ومركز المراسلات.
    """
    DIRECTION_OUTBOUND = 'outbound'
    DIRECTION_INBOUND = 'inbound'

    client = models.ForeignKey(
        'crm.Client',
        on_delete=models.CASCADE,
        related_name='whatsapp_messages',
        help_text="العميل المحتمل (الليد)",
    )
    phone_number = models.CharField(
        max_length=20,
        help_text="رقم الهاتف في المحادثة",
    )
    body = models.TextField(
        help_text="نص الرسالة",
    )
    direction = models.CharField(
        max_length=10,
        choices=[
            (DIRECTION_OUTBOUND, 'Outbound'),
            (DIRECTION_INBOUND, 'Inbound'),
        ],
        default=DIRECTION_OUTBOUND,
    )
    whatsapp_message_id = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        help_text="معرف الرسالة من WhatsApp Cloud API",
    )
    phone_number_id = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text="Meta business phone_number_id that received/sent this message (for CS-window routing).",
    )
    delivery_status = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="Meta delivery status: sent, delivered, read, failed",
    )
    delivery_error = models.CharField(
        max_length=512,
        blank=True,
        null=True,
        help_text="Meta error message when delivery_status=failed",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_whatsapp_messages',
        help_text="المستخدم الذي أرسل الرسالة (للصادرة)",
    )
    send_source = models.CharField(
        max_length=20,
        choices=MessageSendSource.choices,
        default=MessageSendSource.MANUAL,
    )
    campaign_batch = models.ForeignKey(
        MessageCampaignBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_messages",
    )
    # Agent-side read state for inbound messages (CRM UI unread badge).
    # Not Meta delivery_status=read (that is the customer's receipt on outbound).
    is_read = models.BooleanField(
        default=True,
        help_text="False for unread inbound messages until an agent opens the thread.",
    )
    # Session media (image/video/audio/document) — mirrors tenant_chat.ChatMessage attachment fields.
    class AttachmentKind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        DOCUMENT = "document", "Document"
        LOCATION = "location", "Location"

    attachment = models.FileField(
        upload_to="whatsapp_chat/%Y/%m/%d/",
        max_length=500,
        null=True,
        blank=True,
    )
    attachment_kind = models.CharField(
        max_length=16,
        choices=AttachmentKind.choices,
        null=True,
        blank=True,
    )
    attachment_mime = models.CharField(max_length=128, blank=True, default="")
    attachment_size = models.PositiveIntegerField(null=True, blank=True)
    attachment_width = models.PositiveIntegerField(null=True, blank=True)
    attachment_height = models.PositiveIntegerField(null=True, blank=True)
    original_filename = models.CharField(max_length=255, blank=True, default="")
    attachment_object_key = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Optional remote object path when using object storage.",
    )
    is_voice_note = models.BooleanField(
        default=False,
        help_text="True when outbound audio was sent as a WhatsApp voice note (voice:true).",
    )
    meta_media_id = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        help_text="Meta media id used for Cloud API send/download.",
    )
    location_latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
        help_text="WhatsApp location message latitude.",
    )
    location_longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
        help_text="WhatsApp location message longitude.",
    )
    location_name = models.CharField(max_length=255, blank=True, default="")
    location_address = models.CharField(max_length=512, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lead_whatsapp_messages'
        verbose_name = 'Lead WhatsApp Message'
        verbose_name_plural = 'Lead WhatsApp Messages'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['client', 'created_at']),
            models.Index(
                fields=['client', 'is_read'],
                name='lead_wa_client_is_read_idx',
            ),
        ]

    def __str__(self):
        return f"WhatsApp to {self.phone_number} @ {self.created_at}"


class WhatsAppConversationStatus(models.TextChoices):
    OPEN = "open", "Open"
    PENDING = "pending", "Pending"
    SPAM = "spam", "Spam"
    INVALID = "invalid", "Invalid"
    DONE = "done", "Done"
    SNOOZED = "snoozed", "Snoozed"


class WhatsAppConversationState(models.Model):
    """
    Per-client WhatsApp inbox triage state (Mujeb-style).
    A missing row means open, not starred, not unsubscribed — queries tolerate NULL.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="whatsapp_conversation_states",
    )
    client = models.OneToOneField(
        "crm.Client",
        on_delete=models.CASCADE,
        related_name="whatsapp_state",
    )
    status = models.CharField(
        max_length=20,
        choices=WhatsAppConversationStatus.choices,
        default=WhatsAppConversationStatus.OPEN,
        db_index=True,
    )
    snoozed_until = models.DateTimeField(null=True, blank=True)
    is_starred = models.BooleanField(default=False)
    is_unsubscribed = models.BooleanField(default=False)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    status_changed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_conversation_status_changes",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "whatsapp_conversation_states"
        indexes = [
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "is_starred"]),
            models.Index(fields=["company", "snoozed_until"]),
        ]

    def __str__(self):
        return f"WA state {self.client_id} ({self.status})"


class MessageTemplate(models.Model):
    """
    قوالب رسائل للمراسلات (واتساب و SMS).
    تُستخدم في مركز المراسلات لرسائل سريعة وقابلة للتخصيص بمتغيرات.
    """
    CHANNEL_WHATSAPP_API = 'whatsapp_api'
    CHANNEL_SMS = 'sms'
    CHANNEL_CHOICES = [
        (CHANNEL_WHATSAPP_API, 'WhatsApp API'),
        (CHANNEL_SMS, 'SMS'),
    ]
    CATEGORY_AUTH = 'auth'
    CATEGORY_MARKETING = 'marketing'
    CATEGORY_UTILITY = 'utility'
    CATEGORY_CHOICES = [
        (CATEGORY_AUTH, 'Auth'),
        (CATEGORY_MARKETING, 'Marketing'),
        (CATEGORY_UTILITY, 'Utility'),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='message_templates',
        help_text="الشركة المالكة للقالب",
    )
    name = models.CharField(
        max_length=255,
        help_text="اسم القالب (مثل: تذكير بالفاتورة)",
    )
    channel_type = models.CharField(
        max_length=20,
        choices=CHANNEL_CHOICES,
        default=CHANNEL_WHATSAPP_API,
        help_text="نوع القناة (WhatsApp API أو SMS)",
    )
    content = models.TextField(
        help_text="محتوى الرسالة مع متغيرات مثل [اسم_العميل] أو [رقم_الفاتورة]",
    )
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default=CATEGORY_UTILITY,
        help_text="فئة القالب (Auth, Marketing, Utility)",
    )
    # حقول إضافية لقوالب واتساب (الواجهة الأمامية)
    language = models.CharField(
        max_length=20,
        blank=True,
        default='en_US',
        help_text="لغة القالب لواتساب (مثل en_US, ar)",
    )
    header_type = models.CharField(
        max_length=20,
        blank=True,
        default='none',
        help_text="نوع الرأس: none, text, image, video, document",
    )
    header_text = models.TextField(
        blank=True,
        default='',
        help_text="نص الرأس عند اختيار header_type=text",
    )
    footer = models.TextField(
        blank=True,
        default='',
        help_text="نص التذييل",
    )
    buttons = models.JSONField(
        default=list,
        blank=True,
        help_text="قائمة أزرار: [{type: phone|url|reply, button_text, phone?, url?}]",
    )
    # ربط القالب مع Meta (واتساب) بعد الإرسال للمراجعة
    meta_template_id = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        help_text="معرف القالب في Meta بعد الإرسال",
    )
    meta_status = models.CharField(
        max_length=32,
        blank=True,
        null=True,
        help_text="حالة القالب في Meta: PENDING, APPROVED, REJECTED",
    )
    meta_variable_map = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "ترتيب متغيرات Meta كما أُرسلت للمراجعة: "
            '{"body": ["employee_name", "customer_name"], "header": [...]} '
            "— يُحدد قيمة كل {{n}} عند الإرسال حتى لو تغيّر نص القالب لاحقاً"
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'message_templates'
        verbose_name = 'Message Template'
        verbose_name_plural = 'Message Templates'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['company']),
        ]

    def __str__(self):
        return f"{self.company.name} - {self.name}"


class PbxProvider(models.TextChoices):
    ZYCOO = "zycoo", "ZYCOO CooVox"


class PbxCallDirection(models.TextChoices):
    INBOUND = "inbound", "Inbound"
    OUTBOUND = "outbound", "Outbound"
    INTERNAL = "internal", "Internal"


class PbxCallDisposition(models.TextChoices):
    ANSWERED = "answered", "Answered"
    NO_ANSWER = "no_answer", "No Answer"
    BUSY = "busy", "Busy"
    FAILED = "failed", "Failed"
    UNKNOWN = "unknown", "Unknown"


class PbxEventType(models.TextChoices):
    RINGING = "ringing", "Ringing"
    ANSWERED = "answered", "Answered"
    HANGUP = "hangup", "Hangup"
    MISSED = "missed", "Missed"
    AGENT_LOGIN = "agent_login", "Agent Login"
    AGENT_LOGOFF = "agent_logoff", "Agent Logoff"
    OTHER = "other", "Other"


class PbxDialCommandStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class PbxRecordingStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


class PbxSettings(models.Model):
    """Per-company PBX integration (ZYCOO CooVox / Asterisk AMI)."""

    company = models.OneToOneField(
        Company,
        on_delete=models.CASCADE,
        related_name="pbx_settings",
    )
    provider = models.CharField(
        max_length=32,
        choices=PbxProvider.choices,
        default=PbxProvider.ZYCOO,
    )
    pbx_host = models.CharField(max_length=255, blank=True, default="")
    ami_port = models.PositiveIntegerField(default=5038)
    ami_username = models.CharField(max_length=128, blank=True, default="")
    ami_password = models.TextField(blank=True, null=True, help_text="Encrypted AMI password")
    webhook_token = models.CharField(max_length=64, unique=True, db_index=True)
    webhook_secret = models.CharField(max_length=128, blank=True, default="")
    connector_api_key = models.CharField(max_length=128, unique=True, db_index=True)
    connector_install_key = models.CharField(max_length=64, blank=True, default="")
    is_enabled = models.BooleanField(default=False)
    auto_log_calls = models.BooleanField(default=True)
    screen_pop_enabled = models.BooleanField(default=True)
    connector_last_seen_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "integrations_pbx_settings"

    def __str__(self):
        return f"PBX ({self.provider}) — {self.company.name}"


class UserPbxExtension(models.Model):
    """Maps a CRM user to a PBX extension for screen pop and click-to-dial."""

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="user_pbx_extensions",
    )
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="pbx_extension",
    )
    extension = models.CharField(max_length=32, help_text="PBX extension number, e.g. 101")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "integrations_user_pbx_extension"
        unique_together = [("company", "extension")]

    def __str__(self):
        return f"{self.user.username} → ext {self.extension}"


class PbxCallRecord(models.Model):
    """CDR / call events from the PBX."""

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="pbx_call_records",
    )
    uniqueid = models.CharField(max_length=128, db_index=True)
    linkedid = models.CharField(max_length=128, blank=True, default="", db_index=True)
    event_type = models.CharField(
        max_length=32,
        choices=PbxEventType.choices,
        default=PbxEventType.OTHER,
    )
    direction = models.CharField(
        max_length=16,
        choices=PbxCallDirection.choices,
        default=PbxCallDirection.INBOUND,
    )
    caller = models.CharField(max_length=32, blank=True, default="")
    callee = models.CharField(max_length=32, blank=True, default="")
    extension = models.CharField(max_length=32, blank=True, default="")
    disposition = models.CharField(
        max_length=16,
        choices=PbxCallDisposition.choices,
        default=PbxCallDisposition.UNKNOWN,
    )
    started_at = models.DateTimeField(blank=True, null=True)
    answered_at = models.DateTimeField(blank=True, null=True)
    ended_at = models.DateTimeField(blank=True, null=True)
    duration_sec = models.PositiveIntegerField(default=0)
    billsec = models.PositiveIntegerField(default=0)
    recording_url = models.URLField(blank=True, default="", max_length=500)
    recording_path = models.TextField(blank=True, default="")
    recording_storage_key = models.CharField(max_length=512, blank=True, default="", db_index=True)
    recording_uploaded = models.BooleanField(default=False)
    recording_status = models.CharField(
        max_length=16,
        choices=PbxRecordingStatus.choices,
        default=PbxRecordingStatus.SKIPPED,
        db_index=True,
    )
    client = models.ForeignKey(
        "crm.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pbx_call_records",
    )
    agent = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pbx_call_records",
    )
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "integrations_pbx_call_record"
        ordering = ["-started_at", "-created_at"]
        indexes = [
            models.Index(fields=["company", "started_at"]),
            models.Index(fields=["company", "extension"]),
            models.Index(fields=["company", "linkedid"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "uniqueid", "event_type"],
                name="uniq_pbx_call_company_uniqueid_event",
            ),
        ]

    def __str__(self):
        return f"{self.uniqueid} ({self.event_type})"


class PbxDialCommand(models.Model):
    """Queued click-to-dial command for the LAN connector."""

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="pbx_dial_commands",
    )
    requested_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pbx_dial_commands",
    )
    client = models.ForeignKey(
        "crm.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pbx_dial_commands",
    )
    phone_number = models.CharField(max_length=32)
    extension = models.CharField(max_length=32)
    status = models.CharField(
        max_length=16,
        choices=PbxDialCommandStatus.choices,
        default=PbxDialCommandStatus.PENDING,
    )
    result_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "integrations_pbx_dial_command"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["company", "status", "created_at"]),
        ]

    def __str__(self):
        return f"Dial {self.phone_number} via {self.extension} ({self.status})"


class WhatsAppCallDirection(models.TextChoices):
    INBOUND = "inbound", "Inbound"
    OUTBOUND = "outbound", "Outbound"


class WhatsAppCallStatus(models.TextChoices):
    RINGING = "ringing", "Ringing"
    ANSWERED = "answered", "Answered"
    MISSED = "missed", "Missed"
    REJECTED = "rejected", "Rejected"
    NO_ANSWER = "no_answer", "No Answer"
    ENDED = "ended", "Ended"
    FAILED = "failed", "Failed"


class WhatsAppCallRecordingStatus(models.TextChoices):
    NONE = "none", "None"
    PENDING = "pending", "Pending"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class WhatsAppCall(models.Model):
    """WhatsApp Cloud API voice call (inbound or business-initiated)."""

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="whatsapp_calls",
    )
    whatsapp_account = models.ForeignKey(
        WhatsAppAccount,
        on_delete=models.CASCADE,
        related_name="calls",
    )
    meta_call_id = models.CharField(max_length=256, db_index=True)
    direction = models.CharField(
        max_length=16,
        choices=WhatsAppCallDirection.choices,
        default=WhatsAppCallDirection.INBOUND,
    )
    status = models.CharField(
        max_length=16,
        choices=WhatsAppCallStatus.choices,
        default=WhatsAppCallStatus.RINGING,
        db_index=True,
    )
    peer_phone = models.CharField(max_length=32, blank=True, default="")
    peer_name = models.CharField(max_length=255, blank=True, default="")
    client = models.ForeignKey(
        "crm.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_calls",
    )
    agent = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_calls",
    )
    offer_sdp = models.TextField(blank=True, default="")
    answer_sdp = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(blank=True, null=True)
    answered_at = models.DateTimeField(blank=True, null=True)
    ended_at = models.DateTimeField(blank=True, null=True)
    duration_sec = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True, default="")
    recording_storage_key = models.CharField(max_length=512, blank=True, default="")
    recording_status = models.CharField(
        max_length=16,
        choices=WhatsAppCallRecordingStatus.choices,
        default=WhatsAppCallRecordingStatus.NONE,
        db_index=True,
    )
    client_call = models.ForeignKey(
        "crm.ClientCall",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_calls",
    )
    error_message = models.TextField(blank=True, default="")
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "integrations_whatsapp_call"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "-created_at"]),
            models.Index(fields=["company", "status"]),
            models.Index(fields=["whatsapp_account", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["whatsapp_account", "meta_call_id"],
                name="uniq_wa_call_account_meta_id",
            ),
        ]

    def __str__(self):
        return f"{self.meta_call_id} ({self.status})"


class WhatsAppCallErrorSource(models.TextChoices):
    INITIATE = "initiate", "Initiate"
    PERMISSION_REQUEST = "permission_request", "Permission request"
    ACCEPT = "accept", "Accept"
    MIC = "mic", "Microphone"
    WEBHOOK = "webhook", "Webhook"
    OUT_OF_HOURS = "out_of_hours", "Out of hours"
    WEBRTC = "webrtc", "WebRTC"


class WhatsAppCallErrorLog(models.Model):
    """Owner-facing audit of WhatsApp Cloud Calling failures (Meta, mic, OOH, etc.)."""

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="whatsapp_call_error_logs",
    )
    whatsapp_account = models.ForeignKey(
        WhatsAppAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="call_error_logs",
    )
    whatsapp_call = models.ForeignKey(
        WhatsAppCall,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="error_logs",
    )
    agent = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_call_error_logs",
    )
    client = models.ForeignKey(
        "crm.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="whatsapp_call_error_logs",
    )
    peer_phone = models.CharField(max_length=32, blank=True, default="")
    source = models.CharField(
        max_length=32,
        choices=WhatsAppCallErrorSource.choices,
        db_index=True,
    )
    error_code = models.CharField(max_length=128, blank=True, default="", db_index=True)
    error_message = models.TextField(blank=True, default="")
    meta_details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "integrations_whatsapp_call_error_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "-created_at"]),
            models.Index(fields=["company", "source"]),
            models.Index(fields=["company", "error_code"]),
        ]

    def __str__(self):
        return f"{self.source}:{self.error_code or self.error_message[:40]}"


# ============================================================================
# Omni-Channel Inbox — Instagram Direct + Facebook Messenger
#
# Deliberately separate from the WhatsApp stack above. There a conversation *is*
# a crm.Client (ensure_client_for_whatsapp_phone auto-creates one per phone), which
# works because a phone number is already a CRM identity. An Instagram DM has no
# phone, and most DMs are never worth a lead row, so conversations here stay
# lead-less until an agent explicitly converts one.
# ============================================================================


class SocialChannel(models.TextChoices):
    INSTAGRAM = 'instagram', 'Instagram Direct'
    MESSENGER = 'messenger', 'Facebook Messenger'


class MetaInboxConnection(models.Model):
    """
    One connected Facebook Page (plus its linked Instagram professional account).

    page_id and ig_user_id are globally unique for the same reason
    WhatsAppAccount.phone_number_id is: an inbound webhook carries no tenant hint
    other than entry[].id, so that id must resolve to exactly one company.

    Messages for BOTH channels are sent through the Page endpoint
    (POST /{page_id}/messages) using page_access_token, so a connection without a
    Page is useless — hence the Page asset is marked required in the Facebook
    Login for Business configuration. ig_user_id stays nullable so a tenant with
    a Page but no linked Instagram account can still run Messenger only.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='meta_inbox_connections',
        help_text="الشركة (tenant) المالكة لهذا الاتصال",
    )
    integration_account = models.ForeignKey(
        IntegrationAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='meta_inbox_connections',
        help_text="حساب التكامل المرتبط (من OAuth)",
    )

    page_id = models.CharField(
        max_length=64,
        unique=True,
        help_text="Facebook Page ID — tenant key for object=page webhooks and the send endpoint",
    )
    page_name = models.CharField(max_length=255, blank=True, default="")

    ig_user_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        unique=True,
        help_text="Instagram professional account ID (IGID) — tenant key for object=instagram webhooks",
    )
    ig_username = models.CharField(max_length=255, blank=True, default="")

    page_access_token = models.TextField(
        blank=True,
        null=True,
        help_text="Page Access Token (مخزن مشفراً)",
    )
    token_expires_at = models.DateTimeField(null=True, blank=True)

    subscribed_fields = models.JSONField(
        default=list,
        blank=True,
        help_text="Fields confirmed by POST /{page-id}/subscribed_apps",
    )
    instagram_subscribed = models.BooleanField(default=False)
    messenger_subscribed = models.BooleanField(default=False)

    status = models.CharField(
        max_length=20,
        choices=[
            ('connected', 'Connected'),
            ('disconnected', 'Disconnected'),
            ('error', 'Error'),
        ],
        default='connected',
    )
    error_message = models.TextField(blank=True, null=True)
    last_webhook_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'meta_inbox_connections'
        verbose_name = 'Meta Inbox Connection'
        verbose_name_plural = 'Meta Inbox Connections'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['page_id']),
            models.Index(fields=['ig_user_id']),
        ]

    def __str__(self):
        label = self.ig_username or self.page_name or self.page_id
        return f"{self.company.name} - {label}"

    def get_page_access_token(self):
        """الحصول على Page Access Token (مفكوك التشفير)"""
        if not self.page_access_token:
            return None
        return decrypt_token(self.page_access_token)

    def set_page_access_token(self, token):
        """حفظ Page Access Token (مشفر)"""
        if token:
            self.page_access_token = encrypt_token(token)
        else:
            self.page_access_token = None


class SocialContact(models.Model):
    """
    A person who messaged the business on Instagram or Messenger.

    external_id is an IGSID (Instagram) or PSID (Messenger). Both are scoped to
    the Meta APP, not to the person — the same human has a different id under a
    different app, so this is never a stable cross-app identity and profile data
    must be re-fetched rather than assumed.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='social_contacts',
    )
    connection = models.ForeignKey(
        MetaInboxConnection,
        on_delete=models.CASCADE,
        related_name='contacts',
    )
    channel = models.CharField(max_length=16, choices=SocialChannel.choices)
    external_id = models.CharField(
        max_length=64,
        help_text="IGSID (Instagram) or PSID (Messenger) — app-scoped",
    )

    name = models.CharField(max_length=255, blank=True, default="")
    username = models.CharField(max_length=255, blank=True, default="")
    profile_pic_url = models.TextField(
        blank=True,
        default="",
        help_text="Meta CDN URL. Stored as a URL only — never mirrored into our storage.",
    )
    profile_fetched_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'social_contacts'
        verbose_name = 'Social Contact'
        verbose_name_plural = 'Social Contacts'
        constraints = [
            models.UniqueConstraint(
                fields=['company', 'channel', 'external_id'],
                name='uniq_social_contact_company_channel_ext',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'channel']),
            models.Index(fields=['connection', 'external_id']),
        ]

    def __str__(self):
        return self.username or self.name or self.external_id

    @property
    def display_name(self):
        return self.name or self.username or f"{self.get_channel_display()} {self.external_id[-6:]}"


class SocialConversation(models.Model):
    """
    One thread between a SocialContact and a connected Page/IG account.

    `client` is NULL until an agent converts the conversation into a CRM lead.
    That is the core difference from WhatsApp, where the lead row IS the
    conversation: most DMs never deserve a lead, and auto-creating one would
    burn the plan's max_clients quota on spam.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='social_conversations',
    )
    connection = models.ForeignKey(
        MetaInboxConnection,
        on_delete=models.CASCADE,
        related_name='conversations',
    )
    contact = models.ForeignKey(
        SocialContact,
        on_delete=models.CASCADE,
        related_name='conversations',
    )
    channel = models.CharField(
        max_length=16,
        choices=SocialChannel.choices,
        help_text="Denormalized from contact for list filtering.",
    )

    # Triage — same vocabulary as WhatsAppConversationState so the frontend's
    # status colors/labels work unchanged across both inboxes.
    status = models.CharField(
        max_length=20,
        choices=WhatsAppConversationStatus.choices,
        default=WhatsAppConversationStatus.OPEN,
        db_index=True,
    )
    snoozed_until = models.DateTimeField(null=True, blank=True)
    is_starred = models.BooleanField(default=False)
    is_unsubscribed = models.BooleanField(default=False)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    status_changed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='social_status_changes',
    )

    assigned_to = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_social_conversations',
    )
    client = models.ForeignKey(
        'crm.Client',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='social_conversations',
        help_text="Set only once an agent converts this conversation into a lead.",
    )
    converted_at = models.DateTimeField(null=True, blank=True)
    converted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='converted_social_conversations',
    )

    # Denormalized list-row fields. Unlike the WhatsApp list (which subqueries
    # LeadWhatsAppMessage per client) the conversation IS the row here, so these
    # keep the list query flat.
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_message_direction = models.CharField(max_length=10, blank=True, default="")
    last_message_preview = models.CharField(max_length=280, blank=True, default="")
    last_inbound_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Anchor for the 24-hour reply window.",
    )
    unread_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'social_conversations'
        verbose_name = 'Social Conversation'
        verbose_name_plural = 'Social Conversations'
        ordering = ['-last_message_at']
        constraints = [
            models.UniqueConstraint(
                fields=['connection', 'contact'],
                name='uniq_social_conv_connection_contact',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'status', '-last_message_at'], name='social_conv_co_stat_idx'),
            models.Index(fields=['company', '-last_message_at'], name='social_conv_co_last_idx'),
            models.Index(fields=['company', 'assigned_to'], name='social_conv_co_assign_idx'),
            models.Index(fields=['company', 'client'], name='social_conv_co_client_idx'),
            models.Index(fields=['company', 'snoozed_until'], name='social_conv_co_snooze_idx'),
        ]

    def __str__(self):
        return f"{self.get_channel_display()} · {self.contact_id}"


class SocialMessage(models.Model):
    """
    One message in a SocialConversation.

    The attachment field set mirrors LeadWhatsAppMessage so the frontend's media
    components (ChatBlobMedia / ChatMediaViewer / ChatVoicePlayer) work unchanged.
    """

    DIRECTION_INBOUND = 'inbound'
    DIRECTION_OUTBOUND = 'outbound'
    DIRECTION_CHOICES = [
        (DIRECTION_INBOUND, 'Inbound'),
        (DIRECTION_OUTBOUND, 'Outbound'),
    ]

    conversation = models.ForeignKey(
        SocialConversation,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    direction = models.CharField(
        max_length=10,
        choices=DIRECTION_CHOICES,
        default=DIRECTION_OUTBOUND,
    )
    external_message_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Meta message id (mid). Meta redelivers aggressively — this dedupes.",
    )
    body = models.TextField(blank=True, default="")
    is_echo = models.BooleanField(
        default=False,
        help_text="True when the business replied from the native Instagram/Messenger app.",
    )
    reaction = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Emoji reaction the contact left on this message.",
    )
    is_read = models.BooleanField(
        default=True,
        help_text="False for inbound messages until an agent opens the thread.",
    )

    delivery_status = models.CharField(max_length=20, blank=True, null=True)
    delivery_error = models.CharField(max_length=512, blank=True, null=True)
    error_key = models.CharField(max_length=64, blank=True, default="")

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_social_messages',
    )
    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Meta's timestamp for the event (authoritative for the 24h window).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class AttachmentKind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        DOCUMENT = "document", "Document"
        LOCATION = "location", "Location"
        # Instagram-specific payload types.
        SHARE = "share", "Shared post"
        STORY_MENTION = "story_mention", "Story mention"
        REEL = "reel", "Reel"

    attachment = models.FileField(
        upload_to="social_chat/%Y/%m/%d/",
        max_length=500,
        null=True,
        blank=True,
    )
    attachment_kind = models.CharField(
        max_length=16,
        choices=AttachmentKind.choices,
        null=True,
        blank=True,
    )
    attachment_mime = models.CharField(max_length=128, blank=True, default="")
    attachment_size = models.PositiveIntegerField(null=True, blank=True)
    attachment_width = models.PositiveIntegerField(null=True, blank=True)
    attachment_height = models.PositiveIntegerField(null=True, blank=True)
    original_filename = models.CharField(max_length=255, blank=True, default="")
    attachment_object_key = models.CharField(max_length=512, blank=True, default="")
    is_voice_note = models.BooleanField(default=False)
    source_media_url = models.TextField(
        blank=True,
        default="",
        help_text="Meta CDN URL from the webhook. Expires within hours — kept for retry/debug.",
    )

    location_latitude = models.DecimalField(
        max_digits=9, decimal_places=6, blank=True, null=True
    )
    location_longitude = models.DecimalField(
        max_digits=9, decimal_places=6, blank=True, null=True
    )
    location_name = models.CharField(max_length=255, blank=True, default="")
    location_address = models.CharField(max_length=512, blank=True, default="")

    class Meta:
        db_table = 'social_messages'
        verbose_name = 'Social Message'
        verbose_name_plural = 'Social Messages'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['conversation', 'external_message_id'],
                condition=models.Q(external_message_id__gt=""),
                name='uniq_social_msg_conv_mid',
            ),
        ]
        indexes = [
            models.Index(fields=['conversation', 'created_at'], name='social_msg_conv_created_idx'),
            models.Index(fields=['conversation', 'is_read'], name='social_msg_conv_isread_idx'),
            models.Index(fields=['external_message_id'], name='social_msg_mid_idx'),
        ]

    def __str__(self):
        return f"{self.direction}: {(self.body or self.attachment_kind or '')[:40]}"

