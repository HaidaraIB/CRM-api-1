from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from .models import User
from .serializers import (
    UserSerializer,
    UserListSerializer,
    CustomTokenObtainPairSerializer,
)
from .permissions import CanAccessUser


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
    search_fields = ["username", "email", "first_name", "last_name", "role"]
    ordering_fields = ["date_joined", "last_login", "username"]
    ordering = ["-date_joined"]

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if user.is_admin() and user.company:
            return queryset.filter(company=user.company)
        return queryset.filter(id=user.id)

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
