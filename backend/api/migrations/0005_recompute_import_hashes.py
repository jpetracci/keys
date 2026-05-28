"""Recompute import_hash using a content-only fingerprint.

Earlier imports built ``import_hash`` from the source filename and CSV row
number, which broke deduplication when the same statement was re-imported
under a different filename or with shifted row numbers. We now hash only the
transaction's content (plus an occurrence index to preserve legitimate
same-day duplicates). This migration backfills existing rows so they line up
with the new algorithm.
"""

from django.db import migrations

from api.management.commands.import_transactions import (
    build_content_fingerprint,
    hash_with_occurrence,
)


def recompute_hashes(apps, schema_editor):
    Transaction = apps.get_model("api", "Transaction")
    seen = {}
    # Order by id so the assigned occurrence indices are deterministic and
    # stable across re-runs.
    qs = Transaction.objects.exclude(import_hash="").order_by("id")
    for tx in qs:
        fingerprint = build_content_fingerprint(
            user_id=tx.author_id,
            account_name=tx.account_name,
            trans_date=tx.trans_date,
            trans_description=tx.trans_description,
            trans_category=tx.trans_category,
            trans_amount=tx.trans_amount,
        )
        occurrence = seen.get(fingerprint, 0)
        seen[fingerprint] = occurrence + 1
        new_hash = hash_with_occurrence(fingerprint, occurrence)
        if tx.import_hash != new_hash:
            tx.import_hash = new_hash
            tx.save(update_fields=["import_hash"])


def noop_reverse(apps, schema_editor):
    # The old hashes cannot be reconstructed (filename/row are gone), so
    # reversing is a no-op. The current hashes remain valid for dedup; they
    # just no longer match the pre-migration algorithm.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0004_transaction_author_nullable"),
    ]

    operations = [
        migrations.RunPython(recompute_hashes, noop_reverse),
    ]
