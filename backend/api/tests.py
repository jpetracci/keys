import csv
import json
import os
import subprocess
import sys
import unittest
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

import django
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from backend import settings as project_settings

from .management.commands.import_transactions import (
    build_content_fingerprint,
    hash_with_occurrence,
)
from .models import Transaction


HEADER = "Date,Description,Category,Amount,Status\n"


def write_csv(directory, name, body, encoding="utf-8"):
    path = Path(directory) / name
    path.write_text(body, encoding=encoding)
    return path


def run_import(*args):
    """Run the importer and return what it wrote to (stdout, stderr)."""
    out, err = StringIO(), StringIO()
    call_command("import_transactions", *[str(arg) for arg in args], stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


class ImportTransactionsCommandTests(TestCase):
    def test_imports_csv_without_user_and_dedupes_repeat_imports(self):
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "checking.csv"
            csv_path.write_text(
                "Date,Description,Category,Amount,Status\n"
                "2024-03-01,Paycheck,Income,100.00,Posted\n"
                "2024-03-02,Groceries,Food,-25.50,Posted\n",
                encoding="utf-8",
            )

            call_command("import_transactions", str(csv_path), stdout=StringIO())
            self.assertEqual(Transaction.objects.count(), 2)
            self.assertTrue(all(t.author_id is None for t in Transaction.objects.all()))

            call_command("import_transactions", str(csv_path), stdout=StringIO())
            self.assertEqual(Transaction.objects.count(), 2)

    def test_dedupes_when_same_file_is_renamed(self):
        with TemporaryDirectory() as temp_dir:
            original = Path(temp_dir) / "march.csv"
            original.write_text(
                "Date,Description,Category,Amount,Status\n"
                "2024-03-01,Paycheck,Income,100.00,Posted\n"
                "2024-03-02,Groceries,Food,-25.50,Posted\n",
                encoding="utf-8",
            )
            call_command("import_transactions", str(original), stdout=StringIO())

            renamed = Path(temp_dir) / "march-copy.csv"
            renamed.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
            call_command("import_transactions", str(renamed), stdout=StringIO())

        self.assertEqual(Transaction.objects.count(), 2)

    def test_dedupes_when_rows_shift_position(self):
        with TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "a.csv"
            first.write_text(
                "Date,Description,Category,Amount,Status\n"
                "2024-03-01,Paycheck,Income,100.00,Posted\n"
                "2024-03-02,Groceries,Food,-25.50,Posted\n",
                encoding="utf-8",
            )
            call_command("import_transactions", str(first), stdout=StringIO())

            # Same two rows but with one extra prior-period row shifting their
            # line numbers. Only the new row should be inserted.
            second = Path(temp_dir) / "b.csv"
            second.write_text(
                "Date,Description,Category,Amount,Status\n"
                "2024-02-28,Old Charge,Misc,-1.00,Posted\n"
                "2024-03-01,Paycheck,Income,100.00,Posted\n"
                "2024-03-02,Groceries,Food,-25.50,Posted\n",
                encoding="utf-8",
            )
            call_command("import_transactions", str(second), stdout=StringIO())

        self.assertEqual(Transaction.objects.count(), 3)

    def test_preserves_legitimate_same_day_duplicates(self):
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "coffee.csv"
            csv_path.write_text(
                # Two identical $5 charges on the same day are real, not a dedup target.
                "Date,Description,Category,Amount,Status\n"
                "2024-03-01,Coffee Shop,Food,-5.00,Posted\n"
                "2024-03-01,Coffee Shop,Food,-5.00,Posted\n",
                encoding="utf-8",
            )
            call_command("import_transactions", str(csv_path), stdout=StringIO())
            self.assertEqual(Transaction.objects.count(), 2)

            # Re-importing the same file must still dedupe both rows.
            call_command("import_transactions", str(csv_path), stdout=StringIO())
            self.assertEqual(Transaction.objects.count(), 2)

    def test_recategorized_row_is_treated_as_duplicate(self):
        """If the bank (or a user) recategorizes a row, re-importing it must
        not create a duplicate."""
        with TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "a.csv"
            first.write_text(
                "Date,Description,Category,Amount,Status\n"
                "2024-03-01,Coffee Shop,Food,-5.00,Posted\n",
                encoding="utf-8",
            )
            call_command("import_transactions", str(first), stdout=StringIO())

            # Same row but with a different category.
            second = Path(temp_dir) / "b.csv"
            second.write_text(
                "Date,Description,Category,Amount,Status\n"
                "2024-03-01,Coffee Shop,Dining,-5.00,Posted\n",
                encoding="utf-8",
            )
            call_command("import_transactions", str(second), stdout=StringIO())

        self.assertEqual(Transaction.objects.count(), 1)

    def test_imports_debit_credit_csvs(self):
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "card.csv"
            csv_path.write_text(
                "Transaction Date,Description,Category,Debit,Credit\n"
                "2024-03-01,Charge,Shopping,10.00,\n"
                "2024-03-02,Payment,Payment,,5.00\n",
                encoding="utf-8",
            )

            call_command("import_transactions", str(csv_path), stdout=StringIO())

        amounts = list(Transaction.objects.order_by("trans_date").values_list("trans_amount", flat=True))
        self.assertEqual([str(amount) for amount in amounts], ["-10.00", "5.00"])

    def test_debit_credit_ignores_csv_sign(self):
        """Citi-style: Credit column holds negative numbers for payments. The
        Debit/Credit column name -- not the CSV sign -- determines direction.
        """
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "citi.csv"
            csv_path.write_text(
                "Status,Date,Description,Debit,Credit\n"
                "Cleared,03/01/2024,Charge,12.00,\n"
                "Cleared,03/02/2024,Payment,,-2040.00\n",
                encoding="utf-8",
            )
            call_command("import_transactions", str(csv_path), stdout=StringIO())

        amounts = list(Transaction.objects.order_by("trans_date").values_list("trans_amount", flat=True))
        # Charge is an expense (negative); payment is income (positive)
        # regardless of how the source CSV signed them.
        self.assertEqual([str(a) for a in amounts], ["-12.00", "2040.00"])

    def test_flip_amounts_inverts_single_amount_column(self):
        """Discover-style: single Amount column where charges are positive.
        --flip-amounts inverts the sign so charges become expenses.
        """
        with TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "discover.csv"
            csv_path.write_text(
                "Trans. Date,Description,Category,Amount\n"
                "03/01/2024,Restaurant,Food,17.32\n"
                "03/02/2024,Payment,Payments,-583.76\n",
                encoding="utf-8",
            )
            call_command(
                "import_transactions", str(csv_path), "--flip-amounts", stdout=StringIO()
            )

        amounts = list(Transaction.objects.order_by("trans_date").values_list("trans_amount", flat=True))
        self.assertEqual([str(a) for a in amounts], ["-17.32", "583.76"])


class TransactionApiNoAuthTests(TestCase):
    def test_anonymous_can_list_create_update_delete(self):
        # list empty (paginated response shape)
        response = self.client.get("/api/transactions/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["results"], [])

        # create
        payload = {
            "trans_date": "2024-05-01",
            "trans_description": "Lunch",
            "trans_category": "Food",
            "trans_amount": "-12.34",
            "account_name": "Wallet",
        }
        response = self.client.post(
            "/api/transactions/", data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 201, response.content)
        created = response.json()
        self.assertIsNone(created["author"])
        tx_id = created["id"]

        # list returns the new row
        response = self.client.get("/api/transactions/")
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(len(body["results"]), 1)

        # patch (edit)
        response = self.client.patch(
            f"/api/transactions/{tx_id}/",
            data=json.dumps({"trans_category": "Dining"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["trans_category"], "Dining")

        # delete via legacy URL
        response = self.client.delete(f"/api/transactions/delete/{tx_id}/")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_summary_endpoint_computes_totals_server_side(self):
        from decimal import Decimal

        for amount in ("100.00", "-25.50", "-12.34", "50.00"):
            self.client.post(
                "/api/transactions/",
                data=json.dumps(
                    {
                        "trans_date": "2024-05-01",
                        "trans_description": "x",
                        "trans_category": "x",
                        "trans_amount": amount,
                        "account_name": "",
                    }
                ),
                content_type="application/json",
            )

        response = self.client.get("/api/transactions/summary/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(Decimal(body["income"]), Decimal("150.00"))
        self.assertEqual(Decimal(body["expenses"]), Decimal("-37.84"))
        self.assertEqual(Decimal(body["net"]), Decimal("112.16"))
        self.assertEqual(body["count"], 4)

    def test_list_pagination_respects_page_size_query_param(self):
        for i in range(3):
            self.client.post(
                "/api/transactions/",
                data=json.dumps(
                    {
                        "trans_date": "2024-05-01",
                        "trans_description": f"row {i}",
                        "trans_category": "x",
                        "trans_amount": "1.00",
                        "account_name": "",
                    }
                ),
                content_type="application/json",
            )

        response = self.client.get("/api/transactions/?page_size=2")
        body = response.json()
        self.assertEqual(body["count"], 3)
        self.assertEqual(len(body["results"]), 2)
        self.assertIsNotNone(body["next"])


class ImporterDedupTests(TestCase):
    COFFEE = "2024-01-15,Coffee,Food,-5.00,Posted\n"
    GROCERY = "2024-01-20,Grocery,Food,-30.00,Posted\n"
    RENT = "2024-02-01,Rent,Housing,-1000.00,Posted\n"

    def write_overlapping_statements(self, directory):
        write_csv(directory, "jan.csv", HEADER + self.COFFEE + self.GROCERY)
        write_csv(directory, "jan-feb.csv", HEADER + self.GROCERY + self.RENT)

    def test_overlapping_statements_in_one_run_are_not_duplicated(self):
        """Importing a folder must give the same result as importing its files
        one run at a time: the row both statements share is stored once."""
        with TemporaryDirectory() as temp_dir:
            self.write_overlapping_statements(temp_dir)
            run_import(temp_dir)

        self.assertEqual(Transaction.objects.count(), 3)
        self.assertEqual(Transaction.objects.filter(trans_description="Grocery").count(), 1)

    def test_extra_copy_in_a_later_file_of_the_same_run_is_kept(self):
        """One coffee in the first statement, two in the next: the second
        statement proves there were two, so both exist afterwards."""
        with TemporaryDirectory() as temp_dir:
            write_csv(temp_dir, "a.csv", HEADER + self.COFFEE)
            write_csv(temp_dir, "b.csv", HEADER + self.COFFEE + self.COFFEE)
            run_import(temp_dir)

        self.assertEqual(Transaction.objects.count(), 2)

    def test_dry_run_reports_what_a_real_run_does_and_writes_nothing(self):
        with TemporaryDirectory() as temp_dir:
            self.write_overlapping_statements(temp_dir)

            out, _ = run_import(temp_dir, "--dry-run")
            self.assertIn("Would create 3 transaction(s); skipped 1", out)
            self.assertEqual(Transaction.objects.count(), 0)

            out, _ = run_import(temp_dir)
            self.assertIn("Created 3 transaction(s); skipped 1", out)
            self.assertEqual(Transaction.objects.count(), 3)

    def test_same_charge_on_two_accounts_is_kept_with_account(self):
        charge = HEADER + "2024-03-01,NETFLIX.COM,Entertainment,-15.99,Posted\n"
        with TemporaryDirectory() as temp_dir:
            out_one, _ = run_import("--account", "Chase", write_csv(temp_dir, "chase.csv", charge))
            out_two, _ = run_import("--account", "Citi", write_csv(temp_dir, "citi.csv", charge))

        self.assertEqual(
            sorted(Transaction.objects.values_list("account_name", flat=True)), ["Chase", "Citi"]
        )
        self.assertNotIn("Note:", out_one + out_two)

    def test_rows_merged_across_files_without_account_are_reported(self):
        """Without --account the importer cannot tell an overlapping statement
        from a second account, so it says how many rows it merged and how to
        keep them apart."""
        charge = HEADER + "2024-03-01,NETFLIX.COM,Entertainment,-15.99,Posted\n"
        with TemporaryDirectory() as temp_dir:
            write_csv(temp_dir, "chase.csv", charge)
            write_csv(temp_dir, "citi.csv", charge)
            out, _ = run_import(temp_dir)

        self.assertEqual(Transaction.objects.count(), 1)
        self.assertIn("Note: 1 skipped row(s)", out)
        self.assertIn("--account", out)

    def test_reimporting_the_same_file_is_not_reported_as_a_merge(self):
        with TemporaryDirectory() as temp_dir:
            statement = write_csv(temp_dir, "jan.csv", HEADER + self.COFFEE)
            run_import(statement)
            out, _ = run_import(statement)

        self.assertIn("skipped 1", out)
        self.assertNotIn("Note:", out)

    def test_same_named_statements_in_different_folders_are_reported_as_a_merge(self):
        """One folder per account and year, each with a 03.csv: the file name
        cannot tell the two statements apart, the folders can."""
        charge = HEADER + "2024-03-01,NETFLIX.COM,Entertainment,-15.99,Posted\n"
        with TemporaryDirectory() as temp_dir:
            for account in ("card", "checking"):
                (Path(temp_dir) / account / "2024").mkdir(parents=True)
                write_csv(Path(temp_dir) / account / "2024", "03.csv", charge)
            out, _ = run_import(Path(temp_dir) / "card" / "2024", Path(temp_dir) / "checking" / "2024")

        self.assertEqual(Transaction.objects.get().source_file, "card/2024/03.csv")
        self.assertIn("Note: 1 skipped row(s)", out)

    def test_rows_imported_before_sources_named_the_folder_count_as_the_same_file(self):
        Transaction.objects.create(
            trans_date="2024-01-15",
            trans_description="Coffee",
            trans_amount="-5.00",
            source_file="jan.csv",
            import_hash="imported-by-an-older-version",
        )
        with TemporaryDirectory() as temp_dir:
            out, _ = run_import(write_csv(temp_dir, "jan.csv", HEADER + self.COFFEE))

        self.assertEqual(Transaction.objects.count(), 1)
        self.assertNotIn("Note:", out)

    def test_pending_rows_are_skipped_unless_included(self):
        body = HEADER + self.COFFEE + "2024-01-16,Gas,Auto,-40.00,Pending\n"
        with TemporaryDirectory() as temp_dir:
            statement = write_csv(temp_dir, "card.csv", body)

            run_import(statement)
            self.assertEqual(
                list(Transaction.objects.values_list("trans_description", flat=True)), ["Coffee"]
            )

            run_import(statement, "--include-pending")
            self.assertEqual(Transaction.objects.count(), 2)


class ImporterParsingTests(TestCase):
    def import_body(self, body, *args):
        with TemporaryDirectory() as temp_dir:
            return run_import(write_csv(temp_dir, "statement.csv", body), *args)

    def amounts(self):
        return dict(Transaction.objects.values_list("trans_description", "trans_amount"))

    def test_zero_in_the_unused_debit_or_credit_column_is_ignored(self):
        """Some exports write 0.00 instead of leaving the other column blank;
        the zero must not win over the real amount beside it."""
        self.import_body(
            "Date,Description,Debit,Credit\n"
            "2024-03-01,Paycheck,0.00,2500.00\n"
            "2024-03-02,Rent,1200.00,0.00\n"
        )

        self.assertEqual(
            self.amounts(), {"Paycheck": Decimal("2500.00"), "Rent": Decimal("-1200.00")}
        )

    def test_placeholder_in_the_unused_debit_or_credit_column_is_ignored(self):
        self.import_body(
            "Date,Description,Debit,Credit\n"
            "2024-03-01,Rent,1200.00,-\n"
            "2024-03-02,Paycheck,N/A,2500.00\n"
        )

        self.assertEqual(
            self.amounts(), {"Rent": Decimal("-1200.00"), "Paycheck": Decimal("2500.00")}
        )

    def test_amounts_the_database_cannot_hold_are_row_errors(self):
        """"NaN" used to abort the run, and a nine-digit amount was stored by
        SQLite and then made the transaction list fail for every request."""
        out, err = self.import_body(
            "Date,Description,Amount\n"
            "2024-03-01,Not a number,NaN\n"
            "2024-03-02,Too large,100000000.00\n"
            "2024-03-03,Too negative,-250000000\n"
            "2024-03-04,Largest that fits,99999999.99\n"
        )

        self.assertEqual(self.amounts(), {"Largest that fits": Decimal("99999999.99")})
        self.assertIn("errors 3", out)
        self.assertIn("statement.csv:2: amount out of range: NaN", err)
        self.assertEqual(self.client.get("/api/transactions/").status_code, 200)

    def test_row_with_both_a_debit_and_a_credit_is_an_error(self):
        out, err = self.import_body(
            "Date,Description,Debit,Credit\n2024-03-01,Unclear,10.00,5.00\n"
        )

        self.assertEqual(Transaction.objects.count(), 0)
        self.assertIn("errors 1", out)
        self.assertIn("statement.csv:2: row has both a debit and a credit amount", err)

    def test_debits_positive_swaps_the_columns(self):
        self.import_body(
            "Date,Description,Debit,Credit\n"
            "2024-03-01,Refund,10.00,\n"
            "2024-03-02,Purchase,0.00,5.00\n",
            "--debits-positive",
        )

        self.assertEqual(
            self.amounts(), {"Refund": Decimal("10.00"), "Purchase": Decimal("-5.00")}
        )

    def test_posting_date_is_used_when_there_is_no_transaction_date(self):
        """A checking-account layout whose only date column is Posting Date
        (and whose rows end in a stray comma) imports."""
        _, err = self.import_body(
            "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
            "DEBIT,03/01/2024,STARBUCKS #123,-5.75,DEBIT_CARD,994.25,,\n"
        )

        self.assertEqual(err, "")
        self.assertEqual(Transaction.objects.get().trans_date, date(2024, 3, 1))

    def test_transaction_date_wins_over_post_date(self):
        self.import_body(
            "Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
            "03/01/2024,03/03/2024,Lunch,Food,Sale,-9.00,\n"
        )

        self.assertEqual(Transaction.objects.get().trans_date, date(2024, 3, 1))


class ImporterFileHandlingTests(TestCase):
    GOOD_ROW = "2024-03-02,Bakery,Food,-3.00,Posted\n"

    def test_windows_1252_file_is_imported(self):
        with TemporaryDirectory() as temp_dir:
            statement = write_csv(
                temp_dir, "bank.csv", HEADER + "2024-03-01,Café Rouge,Food,-4.00,Posted\n", "cp1252"
            )
            _, err = run_import(statement)

        self.assertEqual(Transaction.objects.get().trans_description, "Café Rouge")
        self.assertIn("read as cp1252", err)

    def test_encoding_option_is_used_and_a_byte_order_mark_is_ignored(self):
        with TemporaryDirectory() as temp_dir:
            run_import(write_csv(temp_dir, "wide.csv", HEADER + self.GOOD_ROW, "utf-16"), "--encoding", "utf-16")
            run_import(
                write_csv(temp_dir, "bom.csv", HEADER + "2024-03-03,Deli,Food,-6.00,Posted\n", "utf-8-sig"),
                "--encoding",
                "utf-8",
            )

        self.assertEqual(
            sorted(Transaction.objects.values_list("trans_description", flat=True)), ["Bakery", "Deli"]
        )

    def test_unknown_encoding_is_rejected_before_anything_is_read(self):
        with TemporaryDirectory() as temp_dir:
            statement = write_csv(temp_dir, "bank.csv", HEADER + self.GOOD_ROW)
            with self.assertRaisesMessage(CommandError, "Unknown encoding 'latin-9000'"):
                run_import(statement, "--encoding", "latin-9000")

    def test_file_that_cannot_be_decoded_does_not_stop_the_run(self):
        with TemporaryDirectory() as temp_dir:
            # 0x81 is valid in neither UTF-8 (here) nor Windows-1252.
            (Path(temp_dir) / "a-broken.csv").write_bytes(HEADER.encode() + b"2024-03-01,Caf\x81,Food,-4.00,Posted\n")
            write_csv(temp_dir, "b-good.csv", HEADER + self.GOOD_ROW)
            out, err = run_import(temp_dir)

        self.assertEqual(
            list(Transaction.objects.values_list("trans_description", flat=True)), ["Bakery"]
        )
        self.assertIn("a-broken.csv: could not be read", err)
        self.assertIn("Created 1 transaction(s); skipped 0; errors 1.", out)

    def test_file_that_fails_part_way_leaves_none_of_its_rows_behind(self):
        oversized = "x" * (csv.field_size_limit() + 1)
        with TemporaryDirectory() as temp_dir:
            write_csv(
                temp_dir,
                "a-broken.csv",
                HEADER + "2024-03-01,Kept Only If Whole File Imports,Food,-4.00,Posted\n"
                f"2024-03-02,{oversized},Food,-1.00,Posted\n",
            )
            write_csv(temp_dir, "b-good.csv", HEADER + self.GOOD_ROW)
            out, err = run_import(temp_dir)

        self.assertEqual(
            list(Transaction.objects.values_list("trans_description", flat=True)), ["Bakery"]
        )
        self.assertIn("a-broken.csv: could not be read", err)
        self.assertIn("errors 1", out)

    def test_file_without_a_header_is_reported(self):
        with TemporaryDirectory() as temp_dir:
            out, err = run_import(write_csv(temp_dir, "empty.csv", ""))

        self.assertIn("empty.csv: no header row found", err)
        self.assertIn("errors 1", out)


class ImportHashConstraintTests(TestCase):
    def test_database_rejects_a_repeated_import_hash_without_an_author(self):
        """The (author, import_hash) constraint cannot see rows whose author is
        NULL, which is every row imported without --user."""
        Transaction.objects.create(trans_description="Coffee", import_hash="abc")

        with self.assertRaises(IntegrityError), transaction.atomic():
            Transaction.objects.create(trans_description="Coffee", import_hash="abc")

    def test_rows_without_an_import_hash_are_unaffected(self):
        Transaction.objects.create(trans_description="Manual entry")
        Transaction.objects.create(trans_description="Manual entry")

        self.assertEqual(Transaction.objects.filter(import_hash="").count(), 2)

    def test_two_users_may_hold_the_same_import_hash(self):
        for name in ("ann", "bob"):
            Transaction.objects.create(
                trans_description="Coffee", import_hash="abc", author=User.objects.create(username=name)
            )

        self.assertEqual(Transaction.objects.filter(import_hash="abc").count(), 2)


class TransactionFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        rows = [
            ("2024-01-10", "Lunch", "Food", "Checking", "-10.00"),
            ("2024-02-10", "Dinner", "Food", "Card", "-20.00"),
            ("2024-03-10", "Rent", "Housing", "Checking", "-1000.00"),
        ]
        for trans_date, description, category, account, amount in rows:
            Transaction.objects.create(
                trans_date=trans_date,
                trans_description=description,
                trans_category=category,
                account_name=account,
                trans_amount=amount,
            )

    def descriptions(self, query):
        response = self.client.get(f"/api/transactions/{query}")
        self.assertEqual(response.status_code, 200, response.content)
        return sorted(row["trans_description"] for row in response.json()["results"])

    def test_malformed_date_filter_is_a_400_not_a_crash(self):
        cases = [
            ("/api/transactions/?start=not-a-date", "start"),
            ("/api/transactions/?end=2024-13-45", "end"),
            ("/api/transactions/summary/?start=not-a-date", "start"),
            ("/api/transactions/summary/?end=2024-13-45", "end"),
        ]
        for url, field in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())

    def test_filters_narrow_the_list(self):
        self.assertEqual(self.descriptions("?category=food"), ["Dinner", "Lunch"])
        self.assertEqual(self.descriptions("?account=CHECKING"), ["Lunch", "Rent"])
        self.assertEqual(self.descriptions("?start=2024-02-01&end=2024-02-29"), ["Dinner"])
        self.assertEqual(self.descriptions("?start=2024-02-10"), ["Dinner", "Rent"])

    def test_empty_filter_values_are_ignored(self):
        self.assertEqual(
            self.descriptions("?category=&account=&start=&end="), ["Dinner", "Lunch", "Rent"]
        )

    def test_summary_uses_the_same_filters(self):
        body = self.client.get("/api/transactions/summary/?category=Food&end=2024-01-31").json()

        self.assertEqual(Decimal(body["expenses"]), Decimal("-10.00"))
        self.assertEqual(body["count"], 1)

    def test_filter_options_list_the_categories_and_accounts_in_use(self):
        body = self.client.get("/api/transactions/filter-options/").json()

        self.assertEqual(body, {"categories": ["Food", "Housing"], "accounts": ["Card", "Checking"]})

    def test_filter_options_offer_each_value_once_whatever_its_case(self):
        # The filters match case-insensitively, so "food" would select the
        # same rows as "Food". Rows without an account offer no blank choice.
        Transaction.objects.create(
            trans_description="Snack", trans_category="food", account_name="", trans_amount="-1.00"
        )
        Transaction.objects.create(
            trans_description="Bus", trans_category="transit", account_name="checking", trans_amount="-2.00"
        )

        body = self.client.get("/api/transactions/filter-options/").json()

        self.assertEqual(body["categories"], ["Food", "Housing", "transit"])
        self.assertEqual(body["accounts"], ["Card", "Checking"])
        for category in body["categories"]:
            self.assertTrue(self.descriptions(f"?category={category}"))

    def test_filter_options_are_empty_without_transactions(self):
        Transaction.objects.all().delete()

        body = self.client.get("/api/transactions/filter-options/").json()

        self.assertEqual(body, {"categories": [], "accounts": []})


class CrossSiteAccessTests(TestCase):
    """The API has no login, so the browser's same-origin rules are all that
    stop a web page on another site from using it."""

    def test_unlisted_origin_gets_no_cors_headers(self):
        Transaction.objects.create(trans_description="Salary", trans_amount="5000.00")

        read = self.client.get("/api/transactions/", HTTP_ORIGIN="https://evil.example")
        preflight = self.client.options(
            "/api/transactions/1/",
            HTTP_ORIGIN="https://evil.example",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="DELETE",
        )

        self.assertNotIn("Access-Control-Allow-Origin", read.headers)
        self.assertNotIn("Access-Control-Allow-Origin", preflight.headers)

    @override_settings(CORS_ALLOWED_ORIGINS=["http://localhost:5173"])
    def test_listed_origin_is_allowed_without_credentials(self):
        response = self.client.get("/api/transactions/", HTTP_ORIGIN="http://localhost:5173")

        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://localhost:5173")
        self.assertNotIn("Access-Control-Allow-Credentials", response.headers)

    def test_allow_all_origins_is_not_configurable(self):
        self.assertFalse(getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False))
        self.assertFalse(settings.CORS_ALLOW_CREDENTIALS)

    def test_only_json_bodies_are_accepted(self):
        """Form-encoded and text/plain POSTs are the ones a browser sends
        cross-site without a preflight, so the API must not act on them."""
        bodies = [
            ("multipart/form-data; boundary=x", '--x\r\nContent-Disposition: form-data; name="trans_description"\r\n\r\nx\r\n--x--\r\n'),
            ("application/x-www-form-urlencoded", "trans_description=x&trans_amount=1"),
            ("text/plain", '{"trans_description": "x", "trans_amount": "1"}'),
        ]
        for content_type, body in bodies:
            with self.subTest(content_type=content_type):
                response = self.client.post("/api/transactions/", data=body, content_type=content_type)
                self.assertEqual(response.status_code, 415)

        self.assertEqual(Transaction.objects.count(), 0)


class StatementUploadTests(TestCase):
    """POST /api/transactions/import/: the importer, for a file sent from the
    browser instead of read from a folder on the server."""

    COFFEE = "2024-01-15,Coffee,Food,-5.00,Posted\n"
    GROCERY = "2024-01-20,Grocery,Food,-30.00,Posted\n"
    STATEMENT = HEADER + COFFEE + GROCERY

    def upload(self, body, content_type="text/csv", **params):
        if isinstance(body, str):
            body = body.encode()
        return self.client.post(
            "/api/transactions/import/", data=body, content_type=content_type, QUERY_STRING=urlencode(params)
        )

    def test_imports_the_rows_and_reports_the_counts(self):
        response = self.upload(self.STATEMENT, filename="jan.csv", account="Checking")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            response.json(),
            {
                "dry_run": False,
                "created": 2,
                "skipped": 0,
                "errors": 0,
                "row_errors": [],
                "warnings": [],
                "note": None,
            },
        )
        self.assertEqual(
            sorted(Transaction.objects.values_list("trans_description", "account_name", "source_file")),
            [("Coffee", "Checking", "jan.csv"), ("Grocery", "Checking", "jan.csv")],
        )
        self.assertTrue(all(t.author_id is None for t in Transaction.objects.all()))

    def test_dry_run_reports_what_an_import_would_do_and_writes_nothing(self):
        preview = self.upload(self.STATEMENT, filename="jan.csv", dry_run="true").json()

        self.assertEqual((preview["dry_run"], preview["created"]), (True, 2))
        self.assertEqual(Transaction.objects.count(), 0)

        imported = self.upload(self.STATEMENT, filename="jan.csv").json()
        self.assertEqual((imported["dry_run"], imported["created"]), (False, 2))
        self.assertEqual(Transaction.objects.count(), 2)

    def test_uploading_the_same_statement_again_creates_nothing(self):
        self.upload(self.STATEMENT, filename="jan.csv")
        again = self.upload(self.STATEMENT, filename="jan.csv").json()

        self.assertEqual((again["created"], again["skipped"]), (0, 2))
        self.assertIsNone(again["note"])
        self.assertEqual(Transaction.objects.count(), 2)

    def test_upload_and_command_share_one_set_of_dedup_rules(self):
        """A statement imported from a folder and then uploaded (or the other
        way round) is the same statement."""
        with TemporaryDirectory() as temp_dir:
            run_import(write_csv(temp_dir, "jan.csv", self.STATEMENT))
            uploaded = self.upload(self.STATEMENT + "2024-01-25,Rent,Housing,-900.00,Posted\n", filename="jan.csv").json()
            out, _ = run_import(write_csv(temp_dir, "jan.csv", self.STATEMENT))

        self.assertEqual((uploaded["created"], uploaded["skipped"]), (1, 2))
        self.assertIsNone(uploaded["note"])
        self.assertIn("Created 0 transaction(s); skipped 2", out)
        self.assertEqual(Transaction.objects.count(), 3)

    def test_rows_matching_another_file_are_noted_unless_an_account_is_given(self):
        self.upload(self.STATEMENT, filename="chase.csv")

        merged = self.upload(self.STATEMENT, filename="citi.csv").json()
        self.assertEqual((merged["created"], merged["skipped"]), (0, 2))
        self.assertIn("2 skipped row(s) matched transactions", merged["note"])
        self.assertIn("account name", merged["note"])

        kept = self.upload(self.STATEMENT, filename="citi.csv", account="Citi").json()
        self.assertEqual(kept["created"], 2)
        self.assertIsNone(kept["note"])

    def test_row_errors_are_reported_with_their_line_numbers(self):
        body = (
            HEADER + self.COFFEE + "2024-01-16,Broken,Food,abc,Posted\n" + ",No date,Food,-1.00,Posted\n" + self.GROCERY
        )
        result = self.upload(body, filename="jan.csv").json()

        self.assertEqual((result["created"], result["errors"]), (2, 2))
        self.assertEqual(
            result["row_errors"],
            [
                {"line": 3, "message": "invalid amount: abc"},
                {"line": 4, "message": "missing transaction date"},
            ],
        )

    def test_options_are_applied(self):
        self.upload(
            "Date,Description,Amount,Status\n2024-03-01,Charge,12.00,Posted\n2024-03-02,Hold,3.00,Pending\n",
            flip_amounts="true",
            include_pending="true",
        )
        self.upload("Date,Description,Debit,Credit\n2024-03-03,Refund,10.00,\n", debits_positive="true")
        self.upload((HEADER + "2024-03-04,Wide,Food,-2.00,Posted\n").encode("utf-16"), encoding="utf-16")

        self.assertEqual(
            dict(Transaction.objects.values_list("trans_description", "trans_amount")),
            {
                "Charge": Decimal("-12.00"),
                "Hold": Decimal("-3.00"),
                "Refund": Decimal("10.00"),
                "Wide": Decimal("-2.00"),
            },
        )

    def test_windows_1252_file_is_imported_with_a_warning(self):
        result = self.upload((HEADER + "2024-03-01,Café Rouge,Food,-4.00,Posted\n").encode("cp1252")).json()

        self.assertEqual(result["warnings"], ["not valid UTF-8, read as cp1252"])
        self.assertEqual(Transaction.objects.get().trans_description, "Café Rouge")
        self.assertEqual(Transaction.objects.get().source_file, "uploaded statement")

    def test_folders_in_the_file_name_are_not_stored(self):
        self.upload(self.STATEMENT, filename="C:\\Users\\me\\Downloads\\jan.csv")

        self.assertEqual(set(Transaction.objects.values_list("source_file", flat=True)), {"jan.csv"})

    def test_unreadable_file_is_a_400_and_saves_nothing(self):
        oversized = "x" * (csv.field_size_limit() + 1)
        cases = {
            "not decodable": HEADER.encode() + b"2024-03-01,Caf\x81,Food,-4.00,Posted\n",
            "fails part way": (HEADER + self.COFFEE + f"2024-03-02,{oversized},Food,-1.00,Posted\n").encode(),
            "empty": b"",
            "blank": b"\n\n",
        }
        for name, body in cases.items():
            with self.subTest(name), override_settings(STATEMENT_UPLOAD_MAX_BYTES=len(oversized) * 2):
                response = self.upload(body, filename="broken.csv")
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIsInstance(response.json()["detail"], str)

        self.assertEqual(Transaction.objects.count(), 0)

    def test_unknown_option_values_are_a_400(self):
        for params in ({"encoding": "latin-9000"}, {"encoding": "zlib"}, {"dry_run": "maybe"}):
            with self.subTest(params):
                response = self.upload(self.STATEMENT, **params)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(next(iter(params)), response.json())

        self.assertEqual(Transaction.objects.count(), 0)

    @override_settings(STATEMENT_UPLOAD_MAX_BYTES=200)
    def test_file_over_the_size_limit_is_refused(self):
        response = self.upload(HEADER + self.COFFEE * 10)

        self.assertEqual(response.status_code, 413)
        self.assertIn("too large", response.json()["detail"])
        self.assertEqual(Transaction.objects.count(), 0)

    def test_only_text_csv_bodies_are_accepted(self):
        """Multipart, form-encoded and text/plain POSTs are the ones a browser
        sends cross-site without a preflight, so an upload must not arrive as
        any of them (nor as JSON, which the rest of the API takes)."""
        bodies = [
            (
                "multipart/form-data; boundary=x",
                '--x\r\nContent-Disposition: form-data; name="file"; filename="jan.csv"\r\n'
                f"Content-Type: text/csv\r\n\r\n{self.STATEMENT}\r\n--x--\r\n",
            ),
            ("application/x-www-form-urlencoded", self.STATEMENT),
            ("text/plain", self.STATEMENT),
            ("application/json", json.dumps(self.STATEMENT)),
        ]
        for content_type, body in bodies:
            with self.subTest(content_type=content_type):
                response = self.upload(body, content_type=content_type)
                self.assertEqual(response.status_code, 415)

        self.assertEqual(Transaction.objects.count(), 0)

    def test_upload_from_an_unlisted_origin_fails_its_preflight(self):
        preflight = self.client.options(
            "/api/transactions/import/",
            HTTP_ORIGIN="https://evil.example",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type",
        )

        self.assertNotIn("Access-Control-Allow-Origin", preflight.headers)

    def test_csv_bodies_are_not_accepted_by_the_other_endpoints(self):
        response = self.client.post("/api/transactions/", data=self.STATEMENT.encode(), content_type="text/csv")

        self.assertEqual(response.status_code, 415)


class RegistrationTests(TestCase):
    def register(self, username, password):
        return self.client.post(
            "/api/user/register/",
            data=json.dumps({"username": username, "password": password}),
            content_type="application/json",
        )

    def test_weak_passwords_are_rejected(self):
        cases = {
            "too short": ("carol", "1"),
            "too common": ("carol", "password"),
            "all digits": ("carol", "9173640285"),
            "like the username": ("alexandria", "alexandria1"),
        }
        for reason, (username, password) in cases.items():
            with self.subTest(reason=reason):
                response = self.register(username, password)
                self.assertEqual(response.status_code, 400)
                self.assertIn("password", response.json())

        self.assertEqual(User.objects.count(), 0)

    def test_acceptable_password_creates_a_user_who_can_log_in(self):
        response = self.register("carol", "plum-violin-tractor-62")

        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn("password", response.json())
        self.assertTrue(User.objects.get(username="carol").check_password("plum-violin-tractor-62"))


class SecretKeyTests(SimpleTestCase):
    def test_missing_or_public_key_is_refused_when_debug_is_off(self):
        for value in (None, "", "change-me", project_settings.DEV_SECRET_KEY):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured):
                project_settings.resolve_secret_key(value, debug=False)

    def test_private_key_is_used_as_given(self):
        self.assertEqual(project_settings.resolve_secret_key("k" * 50, debug=False), "k" * 50)

    def test_debug_falls_back_to_the_development_key(self):
        self.assertEqual(
            project_settings.resolve_secret_key(None, debug=True), project_settings.DEV_SECRET_KEY
        )
        self.assertEqual(project_settings.resolve_secret_key("mine", debug=True), "mine")


class SettingsFromEnvironmentTests(SimpleTestCase):
    """Load the settings module in a fresh interpreter with a chosen environment.

    Every variable the settings read is given a value here (an empty string
    counts as unset) so a developer's own backend/.env cannot change the
    outcome.
    """

    NEUTRAL = {
        "DEBUG": "True",
        "SECRET_KEY": "",
        "ALLOWED_HOSTS": "",
        "COOLIFY_FQDN": "",
        "COOLIFY_URL": "",
        "CSRF_TRUSTED_ORIGINS": "",
        "CORS_ALLOW_ALL_ORIGINS": "",
        "CORS_ALLOWED_ORIGINS": "",
        "USE_SQLITE": "",
        "DATABASE_URL": "",
        "DB_NAME": "",
        "FRONTEND_DIST": "",
    }

    def run_settings(self, **env):
        code = (
            "import json; from backend import settings as s; "
            "database = s.DATABASES['default']; "
            "print(json.dumps({"
            "'allow_all': getattr(s, 'CORS_ALLOW_ALL_ORIGINS', False), "
            "'origins': s.CORS_ALLOWED_ORIGINS, "
            "'credentials': s.CORS_ALLOW_CREDENTIALS, "
            "'hosts': s.ALLOWED_HOSTS, "
            "'csrf': s.CSRF_TRUSTED_ORIGINS, "
            "'engine': database['ENGINE'], "
            "'database_host': database.get('HOST', ''), "
            "'sqlite_transaction_mode': database.get('OPTIONS', {}).get('transaction_mode'), "
            "'serves_frontend': 'whitenoise.middleware.WhiteNoiseMiddleware' in s.MIDDLEWARE}))"
        )
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=settings.BASE_DIR,
            env={**os.environ, **self.NEUTRAL, **env},
            capture_output=True,
            text=True,
        )

    def load_settings(self, **env):
        result = self.run_settings(**env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_server_does_not_start_with_debug_off_and_no_secret_key(self):
        result = self.run_settings(DEBUG="False")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SECRET_KEY must be set to a private value", result.stderr)

    def test_allow_all_origins_from_an_old_env_file_is_ignored(self):
        result = self.run_settings(CORS_ALLOW_ALL_ORIGINS="True")

        self.assertEqual(result.returncode, 0, result.stderr)
        loaded = json.loads(result.stdout)
        self.assertEqual(
            (loaded["allow_all"], loaded["origins"], loaded["credentials"]), (False, [], False)
        )
        self.assertIn("CORS_ALLOW_ALL_ORIGINS is ignored", result.stderr)

    def test_domains_assigned_by_coolify_are_allowed_hosts(self):
        """Coolify passes the application's domains in two variables, one with
        the scheme and one without (which is which varies by version)."""
        loaded = self.load_settings(
            ALLOWED_HOSTS="extra.example",
            COOLIFY_FQDN="keys.example.lan,10.0.0.5",
            COOLIFY_URL="http://keys.example.lan,http://10.0.0.5:8080",
        )

        self.assertEqual(
            loaded["hosts"], ["localhost", "127.0.0.1", "extra.example", "keys.example.lan", "10.0.0.5"]
        )
        self.assertEqual(loaded["csrf"], ["http://keys.example.lan", "http://10.0.0.5:8080"])

    def test_localhost_stays_allowed_when_hosts_are_configured(self):
        """Container health checks ask for localhost."""
        loaded = self.load_settings(ALLOWED_HOSTS="keys.example.lan")

        self.assertEqual(loaded["hosts"], ["localhost", "127.0.0.1", "keys.example.lan"])

    def test_allowed_hosts_may_be_typed_with_a_scheme_or_a_port(self):
        loaded = self.load_settings(ALLOWED_HOSTS="http://keys.home.lan, 10.0.0.5:8000")

        self.assertEqual(loaded["hosts"], ["localhost", "127.0.0.1", "keys.home.lan", "10.0.0.5"])

    @unittest.skipUnless(django.VERSION >= (5, 1), "the transaction_mode option needs Django 5.1")
    def test_sqlite_takes_its_write_lock_at_the_start_of_a_transaction(self):
        """Otherwise an import that reads before it writes fails with "database
        is locked" as soon as the web app saves something in between."""
        self.assertEqual(self.load_settings()["sqlite_transaction_mode"], "IMMEDIATE")

    def test_database_url_selects_postgresql(self):
        loaded = self.load_settings(DATABASE_URL="postgres://keys:secret@database.internal:5432/keys")

        self.assertEqual(
            (loaded["engine"], loaded["database_host"]),
            ("django.db.backends.postgresql", "database.internal"),
        )

    def test_sqlite_is_the_default_and_can_be_forced(self):
        url = "postgres://keys:secret@database.internal:5432/keys"

        self.assertEqual(self.load_settings()["engine"], "django.db.backends.sqlite3")
        self.assertEqual(
            self.load_settings(DATABASE_URL=url, USE_SQLITE="True")["engine"],
            "django.db.backends.sqlite3",
        )

    def test_frontend_build_turns_on_static_file_serving(self):
        with TemporaryDirectory() as dist:
            self.assertTrue(self.load_settings(FRONTEND_DIST=dist)["serves_frontend"])


class DeploymentSettingsTests(SimpleTestCase):
    def test_database_url_is_taken_apart(self):
        database = project_settings.database_from_url(
            "postgres://keys:p%40ss%2Fword@database.internal:5433/ledger?sslmode=require"
        )

        self.assertEqual(
            database,
            {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "ledger",
                "USER": "keys",
                "PASSWORD": "p@ss/word",
                "HOST": "database.internal",
                "PORT": "5433",
                "OPTIONS": {"sslmode": "require"},
            },
        )

    def test_database_url_errors_do_not_repeat_the_password(self):
        for url in ("mysql://keys:hunter2@database/ledger", "postgres://keys:hunter2@database:port/ledger"):
            with self.subTest(url=url.split(":")[0]), self.assertRaises(ImproperlyConfigured) as caught:
                project_settings.database_from_url(url)
            self.assertNotIn("hunter2", str(caught.exception))

    def test_hosts_and_origins_are_read_from_domains_in_either_form(self):
        domains = ["keys.example.lan", "https://keys.example.com/", "http://10.0.0.5:8080/app"]

        self.assertEqual(
            project_settings.hosts_from(domains), ["keys.example.lan", "keys.example.com", "10.0.0.5"]
        )
        self.assertEqual(
            project_settings.origins_from(domains), ["https://keys.example.com", "http://10.0.0.5:8080"]
        )

    def test_hosts_keep_the_forms_django_understands(self):
        """A port or a scheme in ALLOWED_HOSTS would otherwise make Django
        answer 400 under that very name; IPv6 addresses need their brackets."""
        self.assertEqual(
            project_settings.hosts_from(
                ["10.0.0.5:8000", "http://[fd00::5]:8000", "fd00::6", "[fd00::7]", ".example.lan", "*"]
            ),
            ["10.0.0.5", "[fd00::5]", "[fd00::6]", "[fd00::7]", ".example.lan", "*"],
        )


class HealthCheckTests(TestCase):
    def test_reports_ok_when_the_database_answers(self):
        response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @override_settings(ALLOWED_HOSTS=["localhost", "keys.example.lan"])
    def test_requests_under_an_unknown_host_name_are_refused(self):
        """The Host check is what keeps a page on another site from reaching
        this login-less API by pointing a name of its own at it."""
        self.assertEqual(self.client.get("/api/health/", HTTP_HOST="localhost:8000").status_code, 200)
        self.assertEqual(self.client.get("/api/transactions/", HTTP_HOST="evil.example").status_code, 400)


class FrontendServingTests(TestCase):
    """With FRONTEND_DIST set (as in the Docker image) Django serves the built
    single-page app next to the API."""

    PAGE = '<!doctype html><div id="root"></div>'

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        (Path(directory.name) / "index.html").write_text(self.PAGE, encoding="utf-8")
        self.dist = directory.name

    def test_addresses_of_the_app_get_its_page(self):
        with self.settings(FRONTEND_DIST=self.dist):
            for url in ("/", "/login", "/register", "/some/deep/link"):
                with self.subTest(url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.content.decode(), self.PAGE)
                    self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
                    self.assertEqual(response["Cache-Control"], "no-cache")

    def test_api_and_admin_addresses_keep_their_own_answers(self):
        with self.settings(FRONTEND_DIST=self.dist):
            self.assertEqual(self.client.get("/api/no-such-endpoint/").status_code, 404)
            self.assertEqual(self.client.get("/api/health/").json(), {"status": "ok"})
            for url in ("/admin", "/api/transactions"):
                with self.subTest(url=url):
                    self.assertRedirects(
                        self.client.get(url), f"{url}/", status_code=301, fetch_redirect_response=False
                    )

    def test_missing_files_are_not_answered_with_the_page(self):
        """A script that is gone must be a 404: answering it with HTML only
        turns a missing file into a confusing syntax error in the browser."""
        with self.settings(FRONTEND_DIST=self.dist):
            for url in ("/assets/index-old-build.js", "/favicon.ico"):
                with self.subTest(url=url):
                    self.assertEqual(self.client.get(url).status_code, 404)

    def test_nothing_is_served_without_a_frontend_build(self):
        with self.settings(FRONTEND_DIST=None):
            self.assertEqual(self.client.get("/").status_code, 404)
            self.assertEqual(self.client.get("/login").status_code, 404)


class MigrationTests(TransactionTestCase):
    """Run the data migrations against databases that already hold rows.

    The normal test run only ever migrates an empty database, which is how a
    migration that crashed on the first imported row went unnoticed.
    """

    def setUp(self):
        # Later tests expect the current schema, whatever this one did.
        self.addCleanup(self.migrate_to_latest)

    def migrate_to(self, name):
        """Migrate the api app to ``name`` and return its Transaction model as
        it was at that point in history."""
        executor = MigrationExecutor(connection)
        executor.migrate([("api", name)])
        executor.loader.build_graph()
        return executor.loader.project_state([("api", name)]).apps.get_model("api", "Transaction")

    def migrate_to_latest(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_rows_imported_before_content_hashes_migrate_to_the_importers_hashes(self):
        """Regression: 0005 called the importer's fingerprint helper with an
        argument that helper has since dropped, so upgrading any database with
        imported rows crashed."""
        OldTransaction = self.migrate_to("0004_transaction_author_nullable")
        for old_hash in ("hash-of-march.csv-row-2", "hash-of-march.csv-row-3"):
            OldTransaction.objects.create(
                trans_date="2024-03-01",
                trans_description="Coffee Shop",
                trans_category="Food",
                trans_amount="-5.00",
                import_hash=old_hash,
            )

        self.migrate_to_latest()

        fingerprint = build_content_fingerprint(
            user_id=None,
            account_name="",
            trans_date=date(2024, 3, 1),
            trans_description="Coffee Shop",
            trans_amount=Decimal("-5.00"),
        )
        self.assertEqual(
            list(Transaction.objects.order_by("id").values_list("import_hash", flat=True)),
            [hash_with_occurrence(fingerprint, 0), hash_with_occurrence(fingerprint, 1)],
        )

        # The statement those rows came from still adds nothing.
        coffee = "2024-03-01,Coffee Shop,Food,-5.00,Posted\n"
        with TemporaryDirectory() as temp_dir:
            run_import(write_csv(temp_dir, "march.csv", HEADER + coffee + coffee))
        self.assertEqual(Transaction.objects.count(), 2)

    def test_repeated_hashes_without_an_author_are_cleared_before_the_constraint(self):
        OldTransaction = self.migrate_to("0006_drop_category_from_import_hash")
        first = OldTransaction.objects.create(trans_description="Coffee", import_hash="same")
        second = OldTransaction.objects.create(trans_description="Coffee", import_hash="same")
        other = OldTransaction.objects.create(trans_description="Tea", import_hash="other")

        self.migrate_to_latest()

        hashes = dict(Transaction.objects.values_list("id", "import_hash"))
        self.assertEqual(hashes, {first.id: "same", second.id: "", other.id: "other"})
