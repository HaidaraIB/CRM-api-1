from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated
from .models import User
from .serializers import UserSerializer, UserListSerializer


class UserViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing User instances.
    Provides CRUD operations: Create, Read, Update, Delete
    """
    queryset = User.objects.all()
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["username", "email", "first_name", "last_name", "role"]
    ordering_fields = ["date_joined", "last_login", "username"]
    ordering = ["-date_joined"]

    def get_serializer_class(self):
        if self.action == "list":
            return UserListSerializer
        return UserSerializer
