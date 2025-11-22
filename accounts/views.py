from rest_framework import viewsets, filters, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .models import User, Role
from .serializers import (
    UserSerializer,
    UserListSerializer,
    CustomTokenObtainPairSerializer,
    ChangePasswordSerializer,
    RegisterCompanySerializer,
)
from .permissions import CanAccessUser
from companies.models import Company


class CustomTokenObtainPairView(TokenObtainPairView):
    """
    Custom view to get a JWT token with user information
    """

    serializer_class = CustomTokenObtainPairSerializer
    permission_classes = [AllowAny]


class UserViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing User instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """

    queryset = User.objects.all()
    permission_classes = [IsAuthenticated, CanAccessUser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["username", "email", "first_name", "last_name", "phone", "role"]
    ordering_fields = ["date_joined", "last_login", "username"]
    ordering = ["-date_joined"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin() and user.company:
            return queryset.filter(company=user.company)
        return queryset.filter(id=user.id)

    def perform_create(self, serializer):
        """Create user and automatically link company owner if user is admin"""
        user = serializer.save()
        
        # إذا كان المستخدم admin وله company، ربط Company.owner به تلقائياً
        if user.role == Role.ADMIN.value and user.company:
            # إذا لم يكن للـ company owner أو كان owner مختلف، ربط المستخدم كـ owner
            if not user.company.owner:
                user.company.owner = user
                user.company.save(update_fields=['owner'])
            elif user.company.owner != user:
                # إذا كان هناك owner آخر، يمكن اختيار استبداله أو عدم التحديث
                # هنا سنستبدله إذا كان المستخدم الجديد admin
                user.company.owner = user
                user.company.save(update_fields=['owner'])
    
    def perform_update(self, serializer):
        """Update user and handle company owner changes"""
        old_company = None
        old_role = None
        if self.get_object():
            old_company = self.get_object().company
            old_role = self.get_object().role
        
        user = serializer.save()
        new_company = user.company
        new_role = user.role
        
        # إذا كان المستخدم admin وله company، ربط Company.owner به تلقائياً
        if new_role == Role.ADMIN.value and new_company:
            # إذا تغيرت company أو role، تحديث Company.owner
            if new_company != old_company or new_role != old_role:
                # إزالة owner من company القديمة (إن وجدت)
                if old_company and old_company != new_company and old_company.owner == user:
                    old_company.owner = None
                    old_company.save(update_fields=['owner'])
                
                # ربط Company.owner بالمستخدم الجديد
                if not new_company.owner or new_company.owner != user:
                    new_company.owner = user
                    new_company.save(update_fields=['owner'])
        elif old_company and old_company.owner == user:
            # إذا لم يعد المستخدم admin أو تمت إزالة company، إزالة owner من company
            old_company.owner = None
            old_company.save(update_fields=['owner'])

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    def get_serializer_class(self):
        if self.action == "list":
            return UserListSerializer
        return UserSerializer

    @action(detail=False, methods=["get"], permission_classes=[IsAuthenticated])
    def me(self, request):
        serializer = UserSerializer(request.user, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated])
    def change_password(self, request):
        """
        Change password for the current authenticated user
        """
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        
        if serializer.is_valid():
            user = request.user
            current_password = serializer.validated_data['current_password']
            new_password = serializer.validated_data['new_password']
            
            # Verify current password
            if not user.check_password(current_password):
                return Response(
                    {'error': 'Current password is incorrect.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Validate new password
            try:
                validate_password(new_password, user)
            except ValidationError as e:
                return Response(
                    {'error': ' '.join(e.messages)},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Set new password
            user.set_password(new_password)
            user.save()
            
            return Response(
                {'message': 'Password changed successfully.'},
                status=status.HTTP_200_OK
            )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([AllowAny])
def register_company(request):
    """
    Register a new company with owner
    POST /api/auth/register/
    Body: {
        company: { name, domain, specialization },
        owner: { first_name, last_name, email, username, password },
        plan_id?: number,
        billing_cycle?: 'monthly' | 'yearly'
    }
    Response: { access, refresh, user, company, subscription? }
    """
    serializer = RegisterCompanySerializer(data=request.data)
    
    if serializer.is_valid():
        result = serializer.save()
        company = result['company']
        owner = result['owner']
        subscription = result.get('subscription')
        
        # Generate JWT tokens
        refresh = RefreshToken.for_user(owner)
        
        # Prepare response data
        response_data = {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id': owner.id,
                'username': owner.username,
                'email': owner.email,
                'first_name': owner.first_name,
                'last_name': owner.last_name,
                'phone': owner.phone or "",
                'role': owner.role,
                'company': company.id,
                'company_name': company.name,
                'company_specialization': company.specialization,
            },
            'company': {
                'id': company.id,
                'name': company.name,
                'domain': company.domain,
                'specialization': company.specialization,
            },
        }
        
        if subscription:
            response_data['subscription'] = {
                'id': subscription.id,
                'plan_id': subscription.plan.id,
                'plan_name': subscription.plan.name,
                'is_active': subscription.is_active,
                'end_date': subscription.end_date.isoformat(),
            }
        
        return Response(response_data, status=status.HTTP_201_CREATED)
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
