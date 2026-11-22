from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction as db_transaction
from django.db.models import Sum, Q
from rest_framework import generics, pagination, status
from rest_framework.exceptions import APIException, ParseError
from rest_framework.parsers import BaseParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .importer import SOURCE_MAX_LENGTH, UnreadableStatement, import_statement
from .models import Transaction
from .serializers import (
    ImportOptionsSerializer,
    TransactionFilterSerializer,
    TransactionSerializer,
    UserSerializer,
)


class TransactionPagination(pagination.PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500


def _filter_queryset(queryset, params):
    """Apply the shared list/summary query filters.

    Malformed values (a ``start`` that is not a date) are a 400, not a crash.
    """
    filters = TransactionFilterSerializer(data=params)
    filters.is_valid(raise_exception=True)
    category = filters.validated_data.get("category")
    account = filters.validated_data.get("account")
    start_date = filters.validated_data.get("start")
    end_date = filters.validated_data.get("end")

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


class TransactionFilterOptions(APIView):
    """The categories and accounts in use, for the frontend's filter controls.

    The list and summary filters match these case-insensitively, so spellings
    that differ only in case are offered once (the first in sort order).
    """

    def get(self, request):
        def distinct(field):
            values = (
                Transaction.objects.exclude(**{field: ""})
                .order_by()
                .values_list(field, flat=True)
                .distinct()
            )
            by_folded = {}
            for value in sorted(values):
                by_folded.setdefault(value.casefold(), value)
            return sorted(by_folded.values(), key=str.casefold)

        return Response(
            {
                "categories": distinct("trans_category"),
                "accounts": distinct("account_name"),
            }
        )


class StatementTooLarge(APIException):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_code = "statement_too_large"

    def __init__(self, limit):
        super().__init__(
            f"The file is too large: at most {limit / (1024 * 1024):g} MB can be "
            "imported at once. Export a shorter period and import it in parts, "
            "or use the import_transactions command."
        )


class CSVUploadParser(BaseParser):
    """Hands the view the request body, unparsed, when it is sent as text/csv.

    A browser asks permission (a CORS preflight) before it sends text/csv to
    another site, which it does not do for form, multipart or text/plain
    bodies. This is what keeps the upload endpoint, like the rest of the API,
    out of reach of other websites: do not add a parser for those types.
    """

    media_type = "text/csv"

    def parse(self, stream, media_type=None, parser_context=None):
        limit = settings.STATEMENT_UPLOAD_MAX_BYTES
        request = (parser_context or {}).get("request")
        try:
            declared = int(request.META.get("CONTENT_LENGTH") or 0) if request else 0
        except ValueError:
            declared = 0
        if declared > limit:
            raise StatementTooLarge(limit)
        # The declared length may be missing, so never read more than the
        # limit (plus the one byte that shows it was exceeded).
        body = stream.read(limit + 1)
        if len(body) > limit:
            raise StatementTooLarge(limit)
        return body


# At most this many row errors are sent back; `errors` still counts them all.
MAX_REPORTED_ROW_ERRORS = 100
UPLOAD_SOURCE_FALLBACK = "uploaded statement"


def _upload_source(filename):
    """Source label for an uploaded file: its name, without any folders."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return (name or UPLOAD_SOURCE_FALLBACK)[-SOURCE_MAX_LENGTH:]


class TransactionImport(APIView):
    """Import a CSV statement sent as the request body.

    The upload counterpart of ``manage.py import_transactions`` for one file:
    the body is the raw file with ``Content-Type: text/csv`` and the options
    are query parameters (``account``, ``dry_run``, ``flip_amounts``,
    ``debits_positive``, ``include_pending``, ``encoding``, ``filename``).
    """

    parser_classes = [CSVUploadParser]

    def post(self, request):
        # Reading the body first makes any other content type a 415.
        raw = request.data
        options = ImportOptionsSerializer(data=request.query_params)
        options.is_valid(raise_exception=True)
        options = options.validated_data
        if not isinstance(raw, bytes) or not raw.strip():
            raise ParseError("The file is empty.")

        source = _upload_source(options["filename"])
        row_errors = []
        warnings = []

        def report(line, message):
            if line is None:
                warnings.append(message)
            elif len(row_errors) < MAX_REPORTED_ROW_ERRORS:
                row_errors.append({"line": line, "message": message})

        try:
            # A dry run is a real run that gets rolled back, so the counts it
            # reports are exactly what importing this file would do.
            with db_transaction.atomic():
                stats = import_statement(
                    raw,
                    source=source,
                    # Only the name of an uploaded file is known, so a stored
                    # row counts as coming from this file when its source
                    # ends in the same file name (the command also records
                    # the folders).
                    is_same_source=lambda stored: _upload_source(stored) == source,
                    user=request.user if request.user.is_authenticated else None,
                    account=options["account"],
                    include_pending=options["include_pending"],
                    debits_positive=options["debits_positive"],
                    flip_amounts=options["flip_amounts"],
                    encoding=options["encoding"] or None,
                    report=report,
                )
                if options["dry_run"]:
                    db_transaction.set_rollback(True)
        except UnreadableStatement as exc:
            raise ParseError(
                f"The file could not be read as a CSV statement; nothing was imported ({exc})."
            ) from exc

        note = None
        if stats["matched_elsewhere"] and not options["account"]:
            note = (
                f"{stats['matched_elsewhere']} skipped row(s) matched transactions "
                "that did not come from a file with this name. That is expected "
                "when statements from one account overlap. If this file is from "
                "a different account, import it with an account name so "
                "identical charges on different accounts are kept."
            )
        return Response(
            {
                "dry_run": options["dry_run"],
                "created": stats["created"],
                "skipped": stats["skipped"],
                "errors": stats["errors"],
                "row_errors": row_errors,
                "warnings": warnings,
                "note": note,
            }
        )


class CreateUserView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [AllowAny]
