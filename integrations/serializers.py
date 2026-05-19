from rest_framework import serializers
from .models import (
    IntegrationAccount,
    IntegrationLog,
    IntegrationPlatform,
    TwilioSettings,
    LeadSMSMessage,
    LeadWhatsAppMessage,
    MessageTemplate,
    SmsProvider,
)


class IntegrationAccountSerializer(serializers.ModelSerializer):
    """Serializer لحسابات التكامل"""
    platform_display = serializers.CharField(
        source='get_platform_display',
        read_only=True
    )
    status_display = serializers.CharField(
        source='get_status_display',
        read_only=True
    )
    is_token_expired = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = IntegrationAccount
        fields = [
            'id',
            'company',
            'platform',
            'platform_display',
            'name',
            'external_account_id',
            'external_account_name',
            'account_link',
            'phone_number',
            'status',
            'status_display',
            'is_active',
            'is_token_expired',
            'last_sync_at',
            'error_message',
            'metadata',
            'created_at',
            'updated_at',
            'created_by',
        ]
        read_only_fields = [
            'id',
            'company',
            'external_account_id',
            'external_account_name',
            'status',
            'last_sync_at',
            'error_message',
            'created_at',
            'updated_at',
            'created_by',
        ]
    
    def validate_platform(self, value):
        """التحقق من صحة المنصة"""
        if value not in [choice[0] for choice in IntegrationPlatform.choices]:
            raise serializers.ValidationError("منصة غير مدعومة")
        return value


class IntegrationAccountCreateSerializer(serializers.ModelSerializer):
    """Serializer لإنشاء حساب تكامل جديد"""
    class Meta:
        model = IntegrationAccount
        fields = [
            'platform',
            'name',
            'account_link',
            'phone_number',
        ]
    
    def validate(self, data):
        """التحقق من البيانات حسب المنصة"""
        platform = data.get('platform')
        request = self.context.get('request')
        if request and getattr(request.user, 'is_authenticated', False):
            company = getattr(request.user, 'company', None)
            if company and platform:
                if IntegrationAccount.objects.filter(company=company, platform=platform).exists():
                    raise serializers.ValidationError({
                        'platform': (
                            'Only one integration account per platform is allowed for your company. '
                            'Remove the existing account first.'
                        )
                    })

        if platform == 'whatsapp':
            if not data.get('phone_number'):
                raise serializers.ValidationError({
                    'phone_number': 'رقم الهاتف مطلوب لـ WhatsApp'
                })
        elif platform in ['meta', 'tiktok']:
            if not data.get('account_link'):
                raise serializers.ValidationError({
                    'account_link': 'رابط الحساب مطلوب'
                })

        return data


class IntegrationAccountUpdateSerializer(serializers.ModelSerializer):
    """Serializer لتحديث حساب تكامل"""
    class Meta:
        model = IntegrationAccount
        fields = [
            'name',
            'account_link',
            'phone_number',
            'is_active',
        ]


class IntegrationAccountDetailSerializer(IntegrationAccountSerializer):
    """Serializer مفصل مع معلومات إضافية"""
    # لا نعرض Access Token و Refresh Token لأسباب أمنية
    pass


class IntegrationLogSerializer(serializers.ModelSerializer):
    """Serializer لسجلات التكامل"""
    account_name = serializers.CharField(
        source='account.name',
        read_only=True
    )
    
    class Meta:
        model = IntegrationLog
        fields = [
            'id',
            'account',
            'account_name',
            'action',
            'status',
            'message',
            'response_data',
            'error_details',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class OAuthCallbackSerializer(serializers.Serializer):
    """Serializer لمعالجة OAuth Callback"""
    code = serializers.CharField(required=True)
    state = serializers.CharField(required=False)
    error = serializers.CharField(required=False)
    error_description = serializers.CharField(required=False)


class WhatsAppEmbeddedSignupCompleteSerializer(serializers.Serializer):
    """Code returned by FB.login (Embedded Signup) — exchanged server-side."""
    code = serializers.CharField(required=True, trim_whitespace=True)


# --------------- Twilio SMS ---------------

class TwilioSettingsSerializer(serializers.ModelSerializer):
    """Per-company SMS settings (Twilio or OTPIQ). Secrets are write-only with masked read fields."""
    auth_token_masked = serializers.SerializerMethodField(read_only=True)
    otpiq_api_key_masked = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = TwilioSettings
        fields = [
            'id',
            'provider',
            'account_sid',
            'twilio_number',
            'auth_token',
            'auth_token_masked',
            'otpiq_api_key',
            'otpiq_api_key_masked',
            'otpiq_route_provider',
            'sender_id',
            'is_enabled',
            'lead_created_sms_enabled',
            'lead_created_sms_template',
            'created_at',
            'updated_at',
        ]
        extra_kwargs = {
            'auth_token': {'write_only': True, 'required': False},
            'otpiq_api_key': {'write_only': True, 'required': False},
        }

    def get_auth_token_masked(self, obj):
        if obj.auth_token:
            return '••••••••••••••••••••••••••••••••••••••••'
        return None

    def get_otpiq_api_key_masked(self, obj):
        if obj.otpiq_api_key:
            return '••••••••••••••••••••••••••••••••••••••••'
        return None

    def validate(self, attrs):
        provider = attrs.get('provider')
        if provider is None and self.instance:
            provider = self.instance.provider
        provider = provider or SmsProvider.TWILIO

        instance = self.instance
        account_sid = attrs.get('account_sid', getattr(instance, 'account_sid', None) if instance else None)
        twilio_number = attrs.get('twilio_number', getattr(instance, 'twilio_number', None) if instance else None)
        sender_id = attrs.get('sender_id', getattr(instance, 'sender_id', None) if instance else None)
        auth_token = attrs.get('auth_token')
        otpiq_key = attrs.get('otpiq_api_key')
        has_auth = bool(getattr(instance, 'auth_token', None) if instance else False) or bool(auth_token)
        has_otpiq = bool(getattr(instance, 'otpiq_api_key', None) if instance else False) or bool(otpiq_key)

        if 'is_enabled' in attrs:
            enabled = bool(attrs['is_enabled'])
        elif instance:
            enabled = bool(instance.is_enabled)
        else:
            enabled = False
        if enabled:
                if provider == SmsProvider.TWILIO:
                    from_value = (sender_id or '').strip() or (twilio_number or '').strip()
                    if not account_sid or not has_auth or not from_value:
                        raise serializers.ValidationError(
                            'Twilio requires Account SID, Auth Token, and Sender ID or sender number when enabled.'
                        )
                elif provider == SmsProvider.OTPIQ:
                    if not has_otpiq:
                        raise serializers.ValidationError(
                            'OTPIQ requires an API key when enabled.'
                        )
        return attrs

    def create(self, validated_data):
        auth = validated_data.pop('auth_token', None)
        otpiq = validated_data.pop('otpiq_api_key', None)
        instance = TwilioSettings(**validated_data)
        if auth is not None:
            instance.set_auth_token(auth)
        if otpiq is not None:
            instance.set_otpiq_api_key(otpiq)
        instance.save()
        return instance

    def update(self, instance, validated_data):
        auth = validated_data.pop('auth_token', None)
        otpiq = validated_data.pop('otpiq_api_key', None)
        if auth is not None:
            instance.set_auth_token(auth)
        if otpiq is not None:
            instance.set_otpiq_api_key(otpiq)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


class LeadSMSMessageSerializer(serializers.ModelSerializer):
    """رسالة SMS للعميل المحتمل (للتايملاين والاستجابة)."""
    created_by_username = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = LeadSMSMessage
        fields = [
            'id',
            'client',
            'phone_number',
            'body',
            'direction',
            'provider',
            'external_message_id',
            'twilio_sid',
            'created_by',
            'created_by_username',
            'created_at',
        ]
        read_only_fields = [
            'id',
            'created_at',
            'direction',
            'provider',
            'external_message_id',
            'twilio_sid',
        ]

    def get_created_by_username(self, obj):
        if obj.created_by_id is None:
            return ''
        return getattr(obj.created_by, 'username', None) or ''


class SendLeadSMSSerializer(serializers.Serializer):
    """إرسال SMS إلى رقم مرتبط بالليد."""
    lead_id = serializers.IntegerField(help_text='معرف العميل المحتمل (الليد)')
    phone_number = serializers.CharField(max_length=20, help_text='رقم الهاتف المستلم')
    body = serializers.CharField(allow_blank=False, help_text='نص الرسالة')


class LeadWhatsAppMessageSerializer(serializers.ModelSerializer):
    """رسالة واتساب للعميل (للتايملاين ومركز المراسلات)."""
    created_by_username = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = LeadWhatsAppMessage
        fields = [
            'id',
            'client',
            'phone_number',
            'body',
            'direction',
            'whatsapp_message_id',
            'created_by',
            'created_by_username',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at', 'direction', 'whatsapp_message_id']

    def get_created_by_username(self, obj):
        if obj.created_by_id is None:
            return ''
        return getattr(obj.created_by, 'username', None) or ''


# --------------- Message Templates (WhatsApp / SMS) ---------------

def _template_name_english_only(value):
    """Allow only English letters, numbers, spaces, hyphens, underscores (required by WhatsApp/Meta)."""
    import re
    if not value or not isinstance(value, str):
        return
    if not re.match(r'^[a-zA-Z0-9_\s\-]+$', value.strip()):
        from rest_framework.exceptions import ValidationError
        raise ValidationError(
            'Template name must contain only English letters, numbers, spaces, hyphens and underscores.'
        )


class MessageTemplateSerializer(serializers.ModelSerializer):
    """قوالب الرسائل لمركز المراسلات."""
    channel_type_display = serializers.CharField(source='get_channel_type_display', read_only=True)
    category_display = serializers.CharField(source='get_category_display', read_only=True)

    class Meta:
        model = MessageTemplate
        fields = [
            'id',
            'name',
            'channel_type',
            'channel_type_display',
            'content',
            'category',
            'category_display',
            'language',
            'header_type',
            'header_text',
            'footer',
            'buttons',
            'meta_template_id',
            'meta_status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'meta_template_id', 'meta_status', 'created_at', 'updated_at']

    def validate_name(self, value):
        if not value:
            return value
        _template_name_english_only(value)
        return value.strip()

