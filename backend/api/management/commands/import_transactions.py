import codecs
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction

# build_content_fingerprint and hash_with_occurrence are imported from here by
# older code and the tests.
from api.importer import (  # noqa: F401
    DEFAULT_CATEGORY,
    FALLBACK_ENCODING,
    FALLBACK_NOTICE,
    SOURCE_MAX_LENGTH,
    MissingHeader,
    UnreadableStatement,
    build_content_fingerprint,
    hash_with_occurrence,
    import_statement,
)


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
            default=DEFAULT_CATEGORY,
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
        source = self._source_label(csv_file)

        def report(line, message):
            if line is not None:
                self.stderr.write(f"{csv_file}:{line}: {message}")
            elif message == FALLBACK_NOTICE:
                self.stderr.write(f"{csv_file}: {message} (use --encoding to choose another)")
            else:
                self.stderr.write(f"{csv_file}: {message}")

        try:
            return import_statement(
                csv_file.read_bytes(),
                source=source,
                # Rows imported before sources included the folder carry the
                # bare file name.
                is_same_source=lambda stored: stored in (source, csv_file.name),
                user=user,
                account=options["account"],
                default_category=options["default_category"],
                include_pending=options["include_pending"],
                debits_positive=options["debits_positive"],
                flip_amounts=options["flip_amounts"],
                encoding=options["encoding"],
                report=report,
            )
        except MissingHeader as exc:
            self.stderr.write(f"{csv_file}: {exc}")
        except (OSError, UnreadableStatement) as exc:
            self.stderr.write(
                self.style.ERROR(
                    f"{csv_file}: could not be read; nothing was imported from this file ({exc})"
                )
            )
        return {"created": 0, "skipped": 0, "errors": 1, "matched_elsewhere": 0}

    def _source_label(self, csv_file):
        """The source recorded on a row: the file's name with its two folders.

        Statements are often kept one folder per account under the same file
        names (``checking/2024-03.csv`` and ``card/2024-03.csv``, or
        ``checking/2024/03.csv`` and ``card/2024/03.csv``); the folders are
        what tells those apart when reporting rows merged across files.
        """
        resolved = csv_file.resolve()
        label = "/".join(resolved.relative_to(resolved.anchor).parts[-3:])
        return label[-SOURCE_MAX_LENGTH:]
