import csv
import hashlib
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from api.models import Transaction


DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")
POSTED_STATUSES = {"", "posted", "cleared"}


def build_content_fingerprint(
    *, user_id, account_name, trans_date, trans_description, trans_amount
):
    """Stable content-only fingerprint string for a transaction.

    Category is deliberately excluded: banks sometimes re-categorize the same
    transaction between exports, and users may edit categories themselves
    through the UI. Including category in the fingerprint would cause those
    edits to surface as duplicates on the next import.

    Shared with the data migrations so backfilled hashes match the runtime
    importer's hashes exactly.
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
                "Account name to apply to every imported row. Defaults to empty so "
                "that re-importing the same statement under a different filename "
                "still dedupes correctly; pass --account to group rows per account."
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

        files = self._expand_paths(options["paths"])
        if not files:
            raise CommandError("No CSV files found.")

        # Track occurrences of each content fingerprint across this whole run so
        # legitimate same-content rows (e.g. two $5 coffees on the same day) get
        # distinct hashes instead of collapsing into one.
        self._run_seen = {}

        totals = {"created": 0, "skipped": 0, "errors": 0}
        for csv_file in files:
            stats = self._import_file(csv_file, user, options)
            for key in totals:
                totals[key] += stats[key]
            self.stdout.write(
                f"{csv_file}: created={stats['created']} skipped={stats['skipped']} errors={stats['errors']}"
            )

        action = "Would create" if options["dry_run"] else "Created"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {totals['created']} transaction(s); "
                f"skipped {totals['skipped']}; errors {totals['errors']}."
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

    def _import_file(self, csv_file, user, options):
        stats = {"created": 0, "skipped": 0, "errors": 0}
        account_name = options["account"]

        with csv_file.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return {"created": 0, "skipped": 0, "errors": 1}

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

                import_hash = self._hash_transaction(
                    user.id if user else None, transaction
                )
                if import_hash is None:
                    # Row is a re-import of a transaction already in the DB.
                    stats["skipped"] += 1
                    continue
                if Transaction.objects.filter(
                    author=user, import_hash=import_hash
                ).exists():
                    stats["skipped"] += 1
                    continue

                if not options["dry_run"]:
                    Transaction.objects.create(
                        author=user,
                        trans_date=transaction["trans_date"],
                        trans_description=transaction["trans_description"],
                        trans_category=transaction["trans_category"],
                        trans_amount=transaction["trans_amount"],
                        account_name=transaction["account_name"],
                        source_file=csv_file.name,
                        import_hash=import_hash,
                    )
                stats["created"] += 1

        return stats

    def _parse_row(self, row, account_name, default_category, debits_positive, flip_amounts):
        description = self._get(row, "description") or self._get(row, "original description")
        if not description:
            raise ValueError("missing description")

        raw_date = (
            self._get(row, "transaction date")
            or self._get(row, "trans. date")
            or self._get(row, "date")
        )
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

        # Debit/Credit columns: the column itself encodes direction, so ignore
        # whatever sign the CSV happens to use. Debit = money out (negative),
        # Credit = money in (positive). --debits-positive swaps this when the
        # columns are mislabeled.
        debit = self._get(row, "debit")
        credit = self._get(row, "credit")
        if debit:
            value = abs(self._to_decimal(debit))
            return value if debits_positive else -value
        if credit:
            value = abs(self._to_decimal(credit))
            return -value if debits_positive else value

        raise ValueError("missing amount")

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
            return Decimal(cleaned).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"invalid amount: {value}") from exc

    def _get(self, row, name):
        for key, value in row.items():
            if key and key.strip().lower() == name:
                return (value or "").strip()
        return ""

    def _hash_transaction(self, user_id, transaction):
        """Return a stable dedup hash for the transaction, or ``None`` to skip.

        The fingerprint is content-only (no filename or row number), so the
        same statement re-imported under a different name -- or a statement
        that overlaps a previous import -- dedupes correctly.

        Legitimate same-content duplicates (e.g. two identical $5 coffee
        charges on the same day) are preserved by appending an occurrence
        index. We return ``None`` when the run has already seen as many
        copies of this content as the DB currently stores, meaning this row
        is a re-import of something already on disk.
        """
        fingerprint = build_content_fingerprint(
            user_id=user_id,
            account_name=transaction["account_name"],
            trans_date=transaction["trans_date"],
            trans_description=transaction["trans_description"],
            trans_amount=transaction["trans_amount"],
        )

        existing = Transaction.objects.filter(
            author_id=user_id,
            account_name=transaction["account_name"],
            trans_date=transaction["trans_date"],
            trans_description=transaction["trans_description"],
            trans_amount=transaction["trans_amount"],
        ).count()
        seen = self._run_seen.get(fingerprint, 0)
        self._run_seen[fingerprint] = seen + 1

        if seen < existing:
            # The DB already holds at least `seen + 1` copies of this content;
            # treat this CSV row as the (seen+1)-th re-import and skip it.
            return None
        return hash_with_occurrence(fingerprint, seen)
