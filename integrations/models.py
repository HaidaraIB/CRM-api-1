from django.db import models
from django.contrib.auth import get_user_model
from companies.models import Company

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
    
    # حالة الحساب
    status = models.CharField(
        max_length=20,
        choices=[
            ('connected', 'Connected'),
            ('disconnected', 'Disconnected'),
            ('error', 'Error'),
            ('expired', 'Token Expired'),
        ],
        default='connected',
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

