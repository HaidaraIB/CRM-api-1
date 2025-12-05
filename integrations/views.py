from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import redirect
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from accounts.permissions import HasActiveSubscription
from .models import IntegrationAccount, IntegrationLog, IntegrationPlatform
from .serializers import (
    IntegrationAccountSerializer,
    IntegrationAccountCreateSerializer,
    IntegrationAccountUpdateSerializer,
    IntegrationAccountDetailSerializer,
    IntegrationLogSerializer,
    OAuthCallbackSerializer,
)
from .oauth_utils import get_oauth_handler
import json


class IntegrationAccountViewSet(viewsets.ModelViewSet):
    """
    ViewSet لإدارة حسابات التكامل
    
    العمليات المتاحة:
    - GET /api/integrations/accounts/ - قائمة الحسابات
    - GET /api/integrations/accounts/{id}/ - تفاصيل حساب
    - POST /api/integrations/accounts/ - إنشاء حساب جديد
    - PUT /api/integrations/accounts/{id}/ - تحديث حساب
    - DELETE /api/integrations/accounts/{id}/ - حذف حساب
    - POST /api/integrations/accounts/{id}/connect/ - ربط حساب (OAuth)
    - POST /api/integrations/accounts/{id}/disconnect/ - قطع الاتصال
    - POST /api/integrations/accounts/{id}/sync/ - مزامنة البيانات
    """
    
    permission_classes = [IsAuthenticated, HasActiveSubscription]
    
    def get_queryset(self):
        """الحصول على حسابات الشركة فقط"""
        user = self.request.user
        queryset = IntegrationAccount.objects.filter(company=user.company)
        
        # فلترة حسب المنصة
        platform = self.request.query_params.get('platform', None)
        if platform:
            queryset = queryset.filter(platform=platform)
        
        # فلترة حسب الحالة
        status_filter = self.request.query_params.get('status', None)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        return queryset.order_by('-created_at')
    
    def get_serializer_class(self):
        """اختيار Serializer حسب العملية"""
        if self.action == 'create':
            return IntegrationAccountCreateSerializer
        elif self.action == 'update' or self.action == 'partial_update':
            return IntegrationAccountUpdateSerializer
        elif self.action == 'retrieve':
            return IntegrationAccountDetailSerializer
        return IntegrationAccountSerializer
    
    def perform_create(self, serializer):
        """إنشاء حساب تكامل جديد"""
        serializer.save(
            company=self.request.user.company,
            created_by=self.request.user,
        )
    
    @action(detail=False, methods=['get'])
    def platforms(self, request):
        """الحصول على قائمة المنصات المدعومة"""
        platforms = [
            {
                'value': choice[0],
                'label': choice[1],
            }
            for choice in IntegrationPlatform.choices
        ]
        return Response(platforms)
    
    @action(detail=True, methods=['post'])
    def connect(self, request, pk=None):
        """
        بدء عملية OAuth لربط الحساب
        
        هذا سيرجع رابط OAuth للمستخدم للانتقال إليه
        """
        account = self.get_object()
        
        try:
            oauth_handler = get_oauth_handler(account.platform)
            state = oauth_handler.generate_state()
            
            # حفظ state في session أو database للتحقق لاحقاً
            # يمكن استخدام Redis أو Database
            request.session[f'oauth_state_{account.id}'] = state
            request.session[f'oauth_account_id_{state}'] = account.id
            
            if account.platform == 'tiktok':
                auth_url, code_verifier = oauth_handler.get_authorization_url(state)
                request.session[f'oauth_code_verifier_{account.id}'] = code_verifier
            else:
                auth_url = oauth_handler.get_authorization_url(state)
            
            return Response({
                'authorization_url': auth_url,
                'state': state,
            })
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=False, methods=['get', 'post'], url_path='oauth/callback/(?P<platform>[^/]+)')
    def oauth_callback(self, request, platform):
        """
        معالجة OAuth Callback من المنصة
        
        هذا endpoint يتم استدعاؤه من المنصة بعد موافقة المستخدم
        """
        serializer = OAuthCallbackSerializer(data=request.query_params or request.data)
        
        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST
            )
        
        code = serializer.validated_data.get('code')
        state = serializer.validated_data.get('state')
        error = serializer.validated_data.get('error')
        
        if error:
            return Response(
                {'error': error, 'description': serializer.validated_data.get('error_description')},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # التحقق من state
        account_id = request.session.get(f'oauth_account_id_{state}')
        if not account_id:
            return Response(
                {'error': 'Invalid state'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            account = IntegrationAccount.objects.get(id=account_id, company=request.user.company)
        except IntegrationAccount.DoesNotExist:
            return Response(
                {'error': 'Account not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            oauth_handler = get_oauth_handler(account.platform)
            
            # استبدال code بـ access token
            if account.platform == 'tiktok':
                code_verifier = request.session.get(f'oauth_code_verifier_{account.id}')
                if not code_verifier:
                    return Response(
                        {'error': 'Code verifier not found'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                token_data = oauth_handler.exchange_code_for_token(code, code_verifier)
            else:
                token_data = oauth_handler.exchange_code_for_token(code)
            
            # الحصول على معلومات المستخدم/الحساب
            user_info = oauth_handler.get_user_info(token_data['access_token'])
            
            # تحديث الحساب
            account.access_token = token_data['access_token']
            account.refresh_token = token_data.get('refresh_token')
            
            # حساب تاريخ انتهاء الصلاحية
            expires_in = token_data.get('expires_in', 0)
            if expires_in:
                account.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
            
            account.external_account_id = user_info.get('id') or user_info.get('open_id')
            account.external_account_name = user_info.get('name') or user_info.get('display_name')
            account.status = 'connected'
            account.error_message = None
            
            # حفظ معلومات إضافية في metadata
            account.metadata = {
                'user_info': user_info,
                'token_type': token_data.get('token_type', 'Bearer'),
            }
            
            # للحصول على الصفحات (Meta)
            if account.platform == 'meta' and hasattr(oauth_handler, 'get_pages'):
                try:
                    pages = oauth_handler.get_pages(token_data['access_token'])
                    account.metadata['pages'] = pages
                except Exception as e:
                    # لا نوقف العملية إذا فشل الحصول على الصفحات
                    pass
            
            account.save()
            
            # تسجيل العملية
            IntegrationLog.objects.create(
                account=account,
                action='oauth_connect',
                status='success',
                message='Account connected successfully',
            )
            
            # تنظيف session
            request.session.pop(f'oauth_state_{account.id}', None)
            request.session.pop(f'oauth_account_id_{state}', None)
            if account.platform == 'tiktok':
                request.session.pop(f'oauth_code_verifier_{account.id}', None)
            
            # إعادة التوجيه إلى صفحة النجاح في Frontend
            frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173')
            return redirect(f"{frontend_url}/integrations?connected=true&account_id={account.id}")
            
        except Exception as e:
            account.status = 'error'
            account.error_message = str(e)
            account.save()
            
            IntegrationLog.objects.create(
                account=account,
                action='oauth_connect',
                status='error',
                message='Failed to connect account',
                error_details=str(e),
            )
            
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['post'])
    def disconnect(self, request, pk=None):
        """قطع الاتصال مع الحساب"""
        account = self.get_object()
        
        account.access_token = None
        account.refresh_token = None
        account.token_expires_at = None
        account.status = 'disconnected'
        account.save()
        
        IntegrationLog.objects.create(
            account=account,
            action='disconnect',
            status='success',
            message='Account disconnected',
        )
        
        return Response({'message': 'Account disconnected successfully'})
    
    @action(detail=True, methods=['post'])
    def sync(self, request, pk=None):
        """مزامنة البيانات مع المنصة"""
        account = self.get_object()
        
        if account.status != 'connected':
            return Response(
                {'error': 'Account is not connected'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if account.is_token_expired():
            # محاولة تجديد Token
            try:
                oauth_handler = get_oauth_handler(account.platform)
                if account.refresh_token:
                    token_data = oauth_handler.refresh_token(account.refresh_token)
                    account.access_token = token_data['access_token']
                    if 'refresh_token' in token_data:
                        account.refresh_token = token_data['refresh_token']
                    expires_in = token_data.get('expires_in', 0)
                    if expires_in:
                        account.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
                    account.save()
                else:
                    return Response(
                        {'error': 'Token expired and no refresh token available'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except Exception as e:
                account.status = 'expired'
                account.error_message = str(e)
                account.save()
                return Response(
                    {'error': f'Failed to refresh token: {str(e)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # TODO: تنفيذ منطق المزامنة حسب المنصة
        # مثال: جلب المنشورات، الإحصائيات، إلخ
        
        account.last_sync_at = timezone.now()
        account.save()
        
        IntegrationLog.objects.create(
            account=account,
            action='sync',
            status='success',
            message='Data synced successfully',
        )
        
        return Response({'message': 'Sync completed successfully'})


class IntegrationLogViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet لعرض سجلات التكامل فقط (قراءة)"""
    
    serializer_class = IntegrationLogSerializer
    permission_classes = [IsAuthenticated, HasActiveSubscription]
    
    def get_queryset(self):
        """الحصول على سجلات حسابات الشركة فقط"""
        user = self.request.user
        account_id = self.request.query_params.get('account', None)
        
        queryset = IntegrationLog.objects.filter(
            account__company=user.company
        )
        
        if account_id:
            queryset = queryset.filter(account_id=account_id)
        
        return queryset.order_by('-created_at')

