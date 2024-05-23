from django.shortcuts import render
from django.contrib.auth.models import User
from rest_framework import generics
from .serializers import UserSerializer, TransactionSerializer
from rest_framework.permissions import IsAuthenticated, AllowAny
from .models import Transaction



class TransactionListCreate(generics.ListCreateAPIView):
    serializer_class = TransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return Transaction.objects.filter(author=user)
    
    def perform_create(self, serialzer):
        if serialzer.is_valid():
            serialzer.save(author=self.request.user)
        else:
            print(serialzer.errors)

class TransactionDelete(generics.DestroyAPIView):
    serializer_class = TransactionSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        return Transaction.objects.filter(author=user)


class CreateUserView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [AllowAny]
