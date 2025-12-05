from rest_framework import serializers
from .models import IntegrationAccount, IntegrationLog, IntegrationPlatform


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

