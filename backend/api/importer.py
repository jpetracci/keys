"""Import one CSV statement into the transaction table.

Shared by the ``import_transactions`` management command (files on disk) and
the upload endpoint (file bytes in a request body).
"""

import csv
import hashlib
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction as db_transaction

from .models import Transaction


DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")
# In priority order: the purchase date when the export has one, otherwise the
# date it posted (some checking exports only carry a posting date).
DATE_COLUMNS = (
    "transaction date",
    "trans. date",
    "date",
    "posting date",
    "post date",
    "posted date",
)
POSTED_STATUSES = {"", "posted", "cleared"}
# Tried when a file is not valid UTF-8: many bank exports are Windows-1252.
FALLBACK_ENCODING = "cp1252"
ZERO = Decimal("0.00")
BYTE_ORDER_MARK = chr(0xFEFF)
# What some exports write in a Debit or Credit cell that holds no amount.
NO_AMOUNT_CELLS = {"-", "--", "n/a", "na"}
# The first amount too large for Transaction.trans_amount (100,000,000.00).
_AMOUNT_FIELD = Transaction._meta.get_field("trans_amount")
AMOUNT_LIMIT = Decimal(10) ** (_AMOUNT_FIELD.max_digits - _AMOUNT_FIELD.decimal_places)
SOURCE_MAX_LENGTH = Transaction._meta.get_field("source_file").max_length
DEFAULT_CATEGORY = "Uncategorized"
# Reported (with no line number) when a file is read with the fallback encoding.
FALLBACK_NOTICE = f"not valid UTF-8, read as {FALLBACK_ENCODING}"


class UnreadableStatement(Exception):
    """The statement could not be read as CSV; none of its rows were saved."""


class MissingHeader(UnreadableStatement):
    """The statement has no header row (an empty file, for example)."""

    def __init__(self):
        super().__init__("no header row found")


def build_content_fingerprint(
    *, user_id, account_name, trans_date, trans_description, trans_amount
):
    """Stable content-only fingerprint string for a transaction.

    Category is deliberately excluded: banks sometimes re-categorize the same
    transaction between exports, and users may edit categories themselves
    through the UI. Including category in the fingerprint would cause those
    edits to surface as duplicates on the next import.

    Stored hashes depend on this exact layout. If it changes, add a data
    migration that re-hashes existing rows with its own copy of the new
    function. Migrations must not import this one: they have to keep working
    after it changes again (0005 and 0006 carry frozen copies for that reason).
    """
    return "|".join(
        [
            str(user_id) if user_id is not None else "",
            (account_name or "").lower(),
            trans_date.isoformat() if trans_date else "",
            (trans_description or "").lower(),
            str(trans_amount),
        ]
    )


def hash_with_occurrence(fingerprint, occurrence):
    payload = f"{fingerprint}|{occurrence}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def import_statement(
    raw,
    *,
    source,
    is_same_source=None,
    user=None,
    account="",
    default_category=DEFAULT_CATEGORY,
    include_pending=False,
    debits_positive=False,
    flip_amounts=False,
    encoding=None,
    report=lambda line, message: None,
):
    """Import one statement as a unit: all of its new rows are saved, or none.

    ``raw`` is the file's bytes and ``source`` the label stored on each row it
    creates. ``is_same_source`` tells whether the source label of a stored row
    names this same file (by default: the label is equal); skipped rows that
    match a stored row from any other source are counted as
    ``matched_elsewhere``. ``report(line, message)`` is called for every
    problem that does not stop the import; ``line`` is the row's line number
    in the file, or None when the message is about the whole file.

    Returns the counts ``created``, ``skipped``, ``errors`` and
    ``matched_elsewhere``. Raises UnreadableStatement, having saved nothing,
    when the bytes cannot be decoded or parsed as CSV.
    """
    source = source[-SOURCE_MAX_LENGTH:]
    if is_same_source is None:
        def is_same_source(stored):
            return stored == source

    try:
        with db_transaction.atomic():
            return _import_rows(
                raw,
                source=source,
                is_same_source=is_same_source,
                user=user,
                account=account,
                default_category=default_category,
                include_pending=include_pending,
                debits_positive=debits_positive,
                flip_amounts=flip_amounts,
                encoding=encoding,
                report=report,
            )
    except (UnicodeDecodeError, csv.Error) as exc:
        raise UnreadableStatement(str(exc)) from exc


def _import_rows(
    raw,
    *,
    source,
    is_same_source,
    user,
    account,
    default_category,
    include_pending,
    debits_positive,
    flip_amounts,
    encoding,
    report,
):
    """Create a transaction for every row of the file that is not stored yet.

    Deduplication is content-only (no filename or row number), so the same
    statement re-imported under a different name -- or a statement that
    overlaps a previous import -- adds nothing. A row is new when the file
    holds more copies of its content than the database does; each copy gets
    an occurrence index so genuinely repeated rows (two identical $5 coffee
    charges on one day) are all kept.
    """
    stats = {"created": 0, "skipped": 0, "errors": 0, "matched_elsewhere": 0}

    text = _decode(raw, encoding, report)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise MissingHeader()

    # Occurrences are counted per file, never across files: overlapping
    # statements repeat the same transaction, and a count that spans files
    # would import the overlap a second time.
    seen_in_file = {}

    for row_number, row in enumerate(reader, start=2):
        try:
            transaction = _parse_row(
                row,
                account_name=account,
                default_category=default_category,
                debits_positive=debits_positive,
                flip_amounts=flip_amounts,
            )
        except ValueError as exc:
            stats["errors"] += 1
            report(row_number, str(exc))
            continue

        status = _get(row, "status")
        if not include_pending and status.lower() not in POSTED_STATUSES:
            stats["skipped"] += 1
            continue

        fingerprint = build_content_fingerprint(
            user_id=user.id if user else None,
            account_name=transaction["account_name"],
            trans_date=transaction["trans_date"],
            trans_description=transaction["trans_description"],
            trans_amount=transaction["trans_amount"],
        )
        occurrence = seen_in_file.get(fingerprint, 0)
        seen_in_file[fingerprint] = occurrence + 1

        existing_sources = _existing_sources(user, transaction)
        if occurrence < len(existing_sources):
            # The database already holds at least `occurrence + 1` copies
            # of this content, so this row is a re-import.
            stats["skipped"] += 1
            if not any(is_same_source(stored) for stored in existing_sources):
                stats["matched_elsewhere"] += 1
            continue

        import_hash = hash_with_occurrence(fingerprint, occurrence)
        if Transaction.objects.filter(author=user, import_hash=import_hash).exists():
            # Imported before and edited since: its content no longer
            # matches this row, but its hash still identifies it.
            stats["skipped"] += 1
            continue

        Transaction.objects.create(
            author=user,
            trans_date=transaction["trans_date"],
            trans_description=transaction["trans_description"],
            trans_category=transaction["trans_category"],
            trans_amount=transaction["trans_amount"],
            account_name=transaction["account_name"],
            source_file=source,
            import_hash=import_hash,
        )
        stats["created"] += 1

    return stats


def _decode(raw, encoding, report):
    if encoding:
        text = raw.decode(encoding)
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode(FALLBACK_ENCODING)
            report(None, FALLBACK_NOTICE)
    # A byte-order mark would otherwise become part of the first column name.
    return text.removeprefix(BYTE_ORDER_MARK)


def _existing_sources(user, transaction):
    """Source file of every stored row with the same content as this one."""
    return list(
        Transaction.objects.filter(
            author=user,
            account_name=transaction["account_name"],
            trans_date=transaction["trans_date"],
            trans_description=transaction["trans_description"],
            trans_amount=transaction["trans_amount"],
        ).values_list("source_file", flat=True)
    )


def _parse_row(row, account_name, default_category, debits_positive, flip_amounts):
    description = _get(row, "description") or _get(row, "original description")
    if not description:
        raise ValueError("missing description")

    raw_date = ""
    for column in DATE_COLUMNS:
        raw_date = _get(row, column)
        if raw_date:
            break
    if not raw_date:
        raise ValueError("missing transaction date")

    category = _get(row, "category") or _get(row, "type") or default_category
    amount = _parse_amount(row, debits_positive, flip_amounts)

    return {
        "trans_date": _parse_date(raw_date),
        "trans_description": description[:255],
        "trans_category": category[:100],
        "trans_amount": amount,
        "account_name": account_name[:100],
    }


def _parse_amount(row, debits_positive, flip_amounts):
    amount = _get(row, "amount")
    if amount:
        value = _to_decimal(amount)
        return -value if flip_amounts else value

    debit = _amount_cell(row, "debit")
    credit = _amount_cell(row, "credit")
    if not debit and not credit:
        raise ValueError("missing amount")

    # Debit/Credit columns: the column itself encodes direction, so ignore
    # whatever sign the CSV happens to use. Debit = money out (negative),
    # Credit = money in (positive). Some exports fill the unused column
    # with 0.00 instead of leaving it blank, so zero means "not this one".
    money_out = abs(_to_decimal(debit)) if debit else ZERO
    money_in = abs(_to_decimal(credit)) if credit else ZERO
    if money_out and money_in:
        raise ValueError("row has both a debit and a credit amount")
    if debits_positive:
        # debits_positive swaps the two when the columns are mislabeled.
        money_out, money_in = money_in, money_out
    return money_in - money_out


def _parse_date(value):
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    raise ValueError(f"unsupported date format: {value}")


def _to_decimal(value):
    cleaned = value.strip().replace("$", "").replace(",", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    try:
        amount = Decimal(cleaned).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid amount: {value}") from exc
    # "NaN" parses as a Decimal, and SQLite stores an amount with too many
    # digits without complaint and then cannot read it back, which breaks
    # the whole transaction list. Neither may reach the database.
    if not amount.is_finite() or abs(amount) >= AMOUNT_LIMIT:
        raise ValueError(f"amount out of range: {value}")
    return amount


def _amount_cell(row, name):
    """A Debit or Credit cell, with "no amount" placeholders read as empty."""
    value = _get(row, name)
    return "" if value.lower() in NO_AMOUNT_CELLS else value


def _get(row, name):
    for key, value in row.items():
        if key and key.strip().lower() == name:
            return (value or "").strip()
    return ""
