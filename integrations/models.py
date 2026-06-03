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
    # يمكن إضافة المزيد لاحقاً
    # GOOGLE_ADS = 'google_ads', 'Google Ads'
    # LINKEDIN = 'linkedin', 'LinkedIn'


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
        """تجديد Access Token إذا انتهت صلاحيته"""
        if self.is_token_expired() and self.refresh_token:
            # TODO: تنفيذ منطق تجديد Token حسب المنصة
            pass
    
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
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_whatsapp_messages',
        help_text="المستخدم الذي أرسل الرسالة (للصادرة)",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lead_whatsapp_messages'
        verbose_name = 'Lead WhatsApp Message'
        verbose_name_plural = 'Lead WhatsApp Messages'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['client', 'created_at']),
        ]

    def __str__(self):
        return f"WhatsApp to {self.phone_number} @ {self.created_at}"


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

