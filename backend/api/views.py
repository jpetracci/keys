from django.contrib.auth.models import User
from rest_framework import generics
from rest_framework.permissions import AllowAny

from .models import Transaction
from .serializers import TransactionSerializer, UserSerializer


class TransactionListCreate(generics.ListCreateAPIView):
    """List or create transactions.

    Auth is disabled for now: anyone can list/create. When users are added back
    in, filter by ``self.request.user`` here and re-enable ``IsAuthenticated``.
    """

    serializer_class = TransactionSerializer

    def get_queryset(self):
        queryset = Transaction.objects.all()

        category = self.request.query_params.get('category')
        account = self.request.query_params.get('account')
        start_date = self.request.query_params.get('start')
        end_date = self.request.query_params.get('end')

        if category:
            queryset = queryset.filter(trans_category__iexact=category)
        if account:
            queryset = queryset.filter(account_name__iexact=account)
        if start_date:
            queryset = queryset.filter(trans_date__gte=start_date)
        if end_date:
            queryset = queryset.filter(trans_date__lte=end_date)

        return queryset

    def perform_create(self, serializer):
        author = self.request.user if self.request.user.is_authenticated else None
        serializer.save(author=author)


class TransactionDetail(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, edit, or delete a single transaction."""

    serializer_class = TransactionSerializer
    queryset = Transaction.objects.all()


class CreateUserView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [AllowAny]
