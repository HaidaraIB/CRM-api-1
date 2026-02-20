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
    # يمكن إضافة المزيد لاحقاً
    # GOOGLE_ADS = 'google_ads', 'Google Ads'
    # LINKEDIN = 'linkedin', 'LinkedIn'


class IntegrationAccount(models.Model):
    """
    نموذج لحسابات التكامل المتصلة
    كل شركة يمكنها ربط حسابات متعددة من نفس المنصة أو منصات مختلفة
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
    account_link = models.URLField(
        blank=True,
        null=True,
        help_text="رابط الحساب (للمنصات التي تدعم الروابط)"
    )
    phone_number = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="رقم الهاتف (لـ WhatsApp)"
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


class TwilioSettings(models.Model):
    """
    إعدادات Twilio لإرسال SMS فقط.
    نستخدم Twilio حصرياً لخدمة الرسائل القصيرة (SMS).
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
    is_enabled = models.BooleanField(
        default=False,
        help_text="الربط مفعل",
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

