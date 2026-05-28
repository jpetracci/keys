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
            help="Account name to apply to every imported row. Defaults to each file name.",
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
                "For CSVs with Debit/Credit columns, import debits as positive and credits "
                "as negative. By default debits are negative and credits are positive."
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
        account_name = options["account"] or csv_file.stem

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
                    user.id if user else None, transaction, csv_file.name, row_number
                )
                exists = Transaction.objects.filter(
                    author=user, import_hash=import_hash
                ).exists()
                if exists:
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

    def _parse_row(self, row, account_name, default_category, debits_positive):
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
        amount = self._parse_amount(row, debits_positive)

        return {
            "trans_date": self._parse_date(raw_date),
            "trans_description": description[:255],
            "trans_category": category[:100],
            "trans_amount": amount,
            "account_name": account_name[:100],
        }

    def _parse_amount(self, row, debits_positive):
        amount = self._get(row, "amount")
        if amount:
            return self._to_decimal(amount)

        debit = self._get(row, "debit")
        credit = self._get(row, "credit")
        if debit:
            value = self._to_decimal(debit)
            return value if debits_positive else -abs(value)
        if credit:
            value = self._to_decimal(credit)
            return -abs(value) if debits_positive else value

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

    def _hash_transaction(self, user_id, transaction, source_file, row_number):
        fingerprint = "|".join(
            [
                str(user_id) if user_id is not None else "",
                source_file.lower(),
                str(row_number),
                transaction["account_name"].lower(),
                transaction["trans_date"].isoformat(),
                transaction["trans_description"].lower(),
                transaction["trans_category"].lower(),
                str(transaction["trans_amount"]),
            ]
        )
        return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
