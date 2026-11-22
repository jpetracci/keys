import codecs
import csv
import hashlib
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction

from api.models import Transaction


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


class Command(BaseCommand):
    help = "Import transactions from common bank and credit-card CSV statement exports."

    def add_arguments(self, parser):
        parser.add_argument(
            "paths",
            nargs="+",
            help="CSV file(s) or directories. Directories are scanned for CSV files.",
        )
        parser.add_argument(
            "--user",
            default=None,
            help=(
                "Optional username that should own the imported transactions. "
                "If omitted, transactions are imported with no owner (auth is disabled)."
            ),
        )
        parser.add_argument(
            "--account",
            default="",
            help=(
                "Account name to apply to every imported row. Without it, rows "
                "with the same date, description and amount count as one "
                "transaction even when they come from different accounts, so "
                "pass --account when you import more than one account."
            ),
        )
        parser.add_argument(
            "--default-category",
            default="Uncategorized",
            help="Category to use when a CSV row does not contain one.",
        )
        parser.add_argument(
            "--include-pending",
            action="store_true",
            help="Import rows whose Status column is not Posted/Cleared.",
        )
        parser.add_argument(
            "--debits-positive",
            action="store_true",
            help=(
                "For CSVs with Debit/Credit columns, swap the interpretation: "
                "treat the Debit column as income (positive) and Credit as expense "
                "(negative). Use this only if the columns appear mislabeled."
            ),
        )
        parser.add_argument(
            "--flip-amounts",
            action="store_true",
            help=(
                "For single-Amount-column CSVs, negate every parsed amount. Use "
                "when the bank exports charges as positive numbers (e.g. some "
                "Discover exports) so that they import as expenses (negative) "
                "in our DB convention."
            ),
        )
        parser.add_argument(
            "--encoding",
            default=None,
            help=(
                "Text encoding of the CSV files. By default each file is read as "
                f"UTF-8, falling back to {FALLBACK_ENCODING} when it is not valid UTF-8."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse files and report counts without writing to the database.",
        )

    def handle(self, *args, **options):
        user = None
        if options["user"]:
            User = get_user_model()
            try:
                user = User.objects.get(username=options["user"])
            except User.DoesNotExist as exc:
                raise CommandError(f"User '{options['user']}' does not exist.") from exc

        if options["encoding"]:
            try:
                codecs.lookup(options["encoding"])
            except LookupError as exc:
                raise CommandError(f"Unknown encoding '{options['encoding']}'.") from exc

        files = self._expand_paths(options["paths"])
        if not files:
            raise CommandError("No CSV files found.")

        if options["dry_run"]:
            # A dry run is a real run that gets rolled back, so the counts it
            # reports are exactly what importing these files would do.
            with db_transaction.atomic():
                totals = self._import_files(files, user, options)
                db_transaction.set_rollback(True)
        else:
            totals = self._import_files(files, user, options)

        action = "Would create" if options["dry_run"] else "Created"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {totals['created']} transaction(s); "
                f"skipped {totals['skipped']}; errors {totals['errors']}."
            )
        )
        if totals["matched_elsewhere"] and not options["account"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Note: {totals['matched_elsewhere']} skipped row(s) matched "
                    "transactions that did not come from the same file. That is "
                    "expected when statements from one account overlap. If these "
                    "files are from different accounts, import each account with "
                    "--account NAME so identical charges on different accounts "
                    "are kept."
                )
            )

    def _expand_paths(self, paths):
        files_by_path = {}
        for raw_path in paths:
            path = Path(raw_path)
            if path.is_dir():
                candidates = [
                    item for item in path.iterdir() if item.is_file() and item.suffix.lower() == ".csv"
                ]
                for candidate in candidates:
                    files_by_path[candidate.resolve()] = candidate
            elif path.is_file():
                files_by_path[path.resolve()] = path
            else:
                self.stderr.write(self.style.WARNING(f"Skipping missing path: {path}"))
        return [files_by_path[key] for key in sorted(files_by_path)]

    def _import_files(self, files, user, options):
        totals = {"created": 0, "skipped": 0, "errors": 0, "matched_elsewhere": 0}
        for csv_file in files:
            stats = self._import_file(csv_file, user, options)
            for key in totals:
                totals[key] += stats[key]
            self.stdout.write(
                f"{csv_file}: created={stats['created']} skipped={stats['skipped']} errors={stats['errors']}"
            )
        return totals

    def _import_file(self, csv_file, user, options):
        """Import one file as a unit: all of its new rows are saved, or none.

        A file that cannot be read is reported and counted as an error so the
        remaining files still import.
        """
        try:
            with db_transaction.atomic():
                return self._import_rows(csv_file, user, options)
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            self.stderr.write(
                self.style.ERROR(
                    f"{csv_file}: could not be read; nothing was imported from this file ({exc})"
                )
            )
            return {"created": 0, "skipped": 0, "errors": 1, "matched_elsewhere": 0}

    def _import_rows(self, csv_file, user, options):
        """Create a transaction for every row of the file that is not stored yet.

        Deduplication is content-only (no filename or row number), so the same
        statement re-imported under a different name -- or a statement that
        overlaps a previous import -- adds nothing. A row is new when the file
        holds more copies of its content than the database does; each copy gets
        an occurrence index so genuinely repeated rows (two identical $5 coffee
        charges on one day) are all kept.
        """
        stats = {"created": 0, "skipped": 0, "errors": 0, "matched_elsewhere": 0}
        account_name = options["account"]
        source = self._source_label(csv_file)

        text = self._read_text(csv_file, options["encoding"])
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if not reader.fieldnames:
            self.stderr.write(f"{csv_file}: no header row found")
            stats["errors"] += 1
            return stats

        # Occurrences are counted per file, never across files: overlapping
        # statements repeat the same transaction, and a count that spans files
        # would import the overlap a second time.
        seen_in_file = {}

        for row_number, row in enumerate(reader, start=2):
            try:
                transaction = self._parse_row(
                    row,
                    account_name=account_name,
                    default_category=options["default_category"],
                    debits_positive=options["debits_positive"],
                    flip_amounts=options["flip_amounts"],
                )
            except ValueError as exc:
                stats["errors"] += 1
                self.stderr.write(f"{csv_file}:{row_number}: {exc}")
                continue

            status = self._get(row, "status")
            if not options["include_pending"] and status.lower() not in POSTED_STATUSES:
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

            existing_sources = self._existing_sources(user, transaction)
            if occurrence < len(existing_sources):
                # The database already holds at least `occurrence + 1` copies
                # of this content, so this row is a re-import.
                stats["skipped"] += 1
                # Rows imported before sources included the folder carry the
                # bare file name.
                if source not in existing_sources and csv_file.name not in existing_sources:
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

    def _source_label(self, csv_file):
        """The source recorded on a row: the file's name with its two folders.

        Statements are often kept one folder per account under the same file
        names (``checking/2024-03.csv`` and ``card/2024-03.csv``, or
        ``checking/2024/03.csv`` and ``card/2024/03.csv``); the folders are
        what tells those apart when reporting rows merged across files.
        """
        resolved = csv_file.resolve()
        label = "/".join(resolved.relative_to(resolved.anchor).parts[-3:])
        return label[-Transaction._meta.get_field("source_file").max_length:]

    def _read_text(self, csv_file, encoding):
        raw = csv_file.read_bytes()
        if encoding:
            text = raw.decode(encoding)
        else:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode(FALLBACK_ENCODING)
                self.stderr.write(
                    f"{csv_file}: not valid UTF-8, read as {FALLBACK_ENCODING} "
                    "(use --encoding to choose another)"
                )
        # A byte-order mark would otherwise become part of the first column name.
        return text.removeprefix(BYTE_ORDER_MARK)

    def _existing_sources(self, user, transaction):
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

    def _parse_row(self, row, account_name, default_category, debits_positive, flip_amounts):
        description = self._get(row, "description") or self._get(row, "original description")
        if not description:
            raise ValueError("missing description")

        raw_date = ""
        for column in DATE_COLUMNS:
            raw_date = self._get(row, column)
            if raw_date:
                break
        if not raw_date:
            raise ValueError("missing transaction date")

        category = self._get(row, "category") or self._get(row, "type") or default_category
        amount = self._parse_amount(row, debits_positive, flip_amounts)

        return {
            "trans_date": self._parse_date(raw_date),
            "trans_description": description[:255],
            "trans_category": category[:100],
            "trans_amount": amount,
            "account_name": account_name[:100],
        }

    def _parse_amount(self, row, debits_positive, flip_amounts):
        amount = self._get(row, "amount")
        if amount:
            value = self._to_decimal(amount)
            return -value if flip_amounts else value

        debit = self._amount_cell(row, "debit")
        credit = self._amount_cell(row, "credit")
        if not debit and not credit:
            raise ValueError("missing amount")

        # Debit/Credit columns: the column itself encodes direction, so ignore
        # whatever sign the CSV happens to use. Debit = money out (negative),
        # Credit = money in (positive). Some exports fill the unused column
        # with 0.00 instead of leaving it blank, so zero means "not this one".
        money_out = abs(self._to_decimal(debit)) if debit else ZERO
        money_in = abs(self._to_decimal(credit)) if credit else ZERO
        if money_out and money_in:
            raise ValueError("row has both a debit and a credit amount")
        if debits_positive:
            # --debits-positive swaps the two when the columns are mislabeled.
            money_out, money_in = money_in, money_out
        return money_in - money_out

    def _parse_date(self, value):
        for date_format in DATE_FORMATS:
            try:
                return datetime.strptime(value, date_format).date()
            except ValueError:
                continue
        raise ValueError(f"unsupported date format: {value}")

    def _to_decimal(self, value):
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

    def _amount_cell(self, row, name):
        """A Debit or Credit cell, with "no amount" placeholders read as empty."""
        value = self._get(row, name)
        return "" if value.lower() in NO_AMOUNT_CELLS else value

    def _get(self, row, name):
        for key, value in row.items():
            if key and key.strip().lower() == name:
                return (value or "").strip()
        return ""
