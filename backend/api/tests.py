import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase

from .models import Transaction


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


class TransactionApiNoAuthTests(TestCase):
    def test_anonymous_can_list_create_update_delete(self):
        # list empty
        response = self.client.get("/api/transactions/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

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
        self.assertEqual(len(response.json()), 1)

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
