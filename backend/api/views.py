from django.contrib.auth.models import User
from django.db.models import Sum, Q
from rest_framework import generics, pagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Transaction
from .serializers import TransactionSerializer, UserSerializer


class TransactionPagination(pagination.PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500


def _filter_queryset(queryset, params):
    """Apply the shared list/summary query filters."""
    category = params.get("category")
    account = params.get("account")
    start_date = params.get("start")
    end_date = params.get("end")

    if category:
        queryset = queryset.filter(trans_category__iexact=category)
    if account:
        queryset = queryset.filter(account_name__iexact=account)
    if start_date:
        queryset = queryset.filter(trans_date__gte=start_date)
    if end_date:
        queryset = queryset.filter(trans_date__lte=end_date)
    return queryset


class TransactionListCreate(generics.ListCreateAPIView):
    """List or create transactions.

    Auth is disabled for now: anyone can list/create. When users are added back
    in, filter by ``self.request.user`` here and re-enable ``IsAuthenticated``.
    """

    serializer_class = TransactionSerializer
    pagination_class = TransactionPagination

    def get_queryset(self):
        return _filter_queryset(Transaction.objects.all(), self.request.query_params)

    def perform_create(self, serializer):
        author = self.request.user if self.request.user.is_authenticated else None
        serializer.save(author=author)


class TransactionDetail(generics.RetrieveUpdateDestroyAPIView):
    """Retrieve, edit, or delete a single transaction."""

    serializer_class = TransactionSerializer
    queryset = Transaction.objects.all()


class TransactionSummary(APIView):
    """Server-computed totals across the full (filtered) transaction set.

    The list endpoint is paginated, so the frontend can't sum amounts client
    side without loading every page. This endpoint returns income, expenses,
    and net using a single DB aggregate so totals stay correct regardless of
    list size or pagination.
    """

    def get(self, request):
        queryset = _filter_queryset(Transaction.objects.all(), request.query_params)
        aggregates = queryset.aggregate(
            income=Sum("trans_amount", filter=Q(trans_amount__gte=0)),
            expenses=Sum("trans_amount", filter=Q(trans_amount__lt=0)),
            net=Sum("trans_amount"),
            count=Sum(1),
        )
        return Response(
            {
                "income": str(aggregates["income"] or 0),
                "expenses": str(aggregates["expenses"] or 0),
                "net": str(aggregates["net"] or 0),
                "count": aggregates["count"] or 0,
            }
        )


class CreateUserView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [AllowAny]
