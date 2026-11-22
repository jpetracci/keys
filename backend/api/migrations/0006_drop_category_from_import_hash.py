"""Drop category from the import_hash fingerprint.

Banks sometimes re-categorize the same transaction between exports, and users
can edit categories in the UI. Including category in the dedup fingerprint
made both of those surface as duplicates on the next import. We now hash only
(user, account, date, description, amount) plus an occurrence index, and
backfill existing rows so their hashes line up.
"""

import hashlib

from django.db import migrations


# Frozen copies of the importer's helpers as they were when this migration was
# written. Do not import them from the importer: a migration has to keep doing
# what it did on the day it was written, whatever the importer does later.
def build_content_fingerprint(
    *, user_id, account_name, trans_date, trans_description, trans_amount
):
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
