from rest_framework import serializers
from .models import IntegrationAccount, IntegrationLog, IntegrationPlatform, TwilioSettings, LeadSMSMessage, MessageTemplate


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


# --------------- Twilio SMS ---------------

class TwilioSettingsSerializer(serializers.ModelSerializer):
    """إعدادات Twilio لعرض/تحديث. Auth Token لا يُعاد في الاستجابة؛ عند التحديث إذا وُجد auth_token يُحفظ مشفراً."""
    auth_token_masked = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = TwilioSettings
        fields = [
            'id',
            'account_sid',
            'twilio_number',
            'auth_token',
            'auth_token_masked',
            'sender_id',
            'is_enabled',
            'created_at',
            'updated_at',
        ]
        extra_kwargs = {
            'auth_token': {'write_only': True, 'required': False},
        }

    def get_auth_token_masked(self, obj):
        if obj.auth_token:
            return '••••••••••••••••••••••••••••••••••••••••'
        return None

    def update(self, instance, validated_data):
        auth = validated_data.pop('auth_token', None)
        if auth is not None:
            instance.set_auth_token(auth)
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
            'twilio_sid',
            'created_by',
            'created_by_username',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at', 'direction', 'twilio_sid']

    def get_created_by_username(self, obj):
        if obj.created_by_id is None:
            return ''
        return getattr(obj.created_by, 'username', None) or ''


class SendLeadSMSSerializer(serializers.Serializer):
    """إرسال SMS إلى رقم مرتبط بالليد."""
    lead_id = serializers.IntegerField(help_text='معرف العميل المحتمل (الليد)')
    phone_number = serializers.CharField(max_length=20, help_text='رقم الهاتف المستلم')
    body = serializers.CharField(allow_blank=False, help_text='نص الرسالة')


# --------------- Message Templates (WhatsApp / SMS) ---------------

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
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

