"""Drop category from the import_hash fingerprint.

Banks sometimes re-categorize the same transaction between exports, and users
can edit categories in the UI. Including category in the dedup fingerprint
made both of those surface as duplicates on the next import. We now hash only
(user, account, date, description, amount) plus an occurrence index, and
backfill existing rows so their hashes line up.
"""

from django.db import migrations

from api.management.commands.import_transactions import (
    build_content_fingerprint,
    hash_with_occurrence,
)


def recompute_hashes(apps, schema_editor):
    Transaction = apps.get_model("api", "Transaction")
    seen = {}
    qs = Transaction.objects.exclude(import_hash="").order_by("id")
    for tx in qs:
        fingerprint = build_content_fingerprint(
            user_id=tx.author_id,
            account_name=tx.account_name,
            trans_date=tx.trans_date,
            trans_description=tx.trans_description,
            trans_amount=tx.trans_amount,
        )
        occurrence = seen.get(fingerprint, 0)
        seen[fingerprint] = occurrence + 1
        new_hash = hash_with_occurrence(fingerprint, occurrence)
        if tx.import_hash != new_hash:
            tx.import_hash = new_hash
            tx.save(update_fields=["import_hash"])


def noop_reverse(apps, schema_editor):
    # Old hashes can't be reconstructed; current hashes remain valid for dedup.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0005_recompute_import_hashes"),
    ]

    operations = [
        migrations.RunPython(recompute_hashes, noop_reverse),
    ]
