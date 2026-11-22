"""Make import hashes unique among rows that have no author.

The unique constraint on (author, import_hash) never applied to rows imported
without ``--user``, because a unique index treats NULL authors as all
different. The next migration adds a constraint that does cover them; this one
first clears the hash on any row that would violate it.

Such rows are not expected (the importer checks before it inserts), but two
imports running at once could have produced them. The rows themselves are
kept: only the later duplicates lose their hash, and the importer still counts
them when it compares a statement against stored transactions.

This lives in its own migration because PostgreSQL can refuse to change a
table's schema in the same transaction that modified its rows.
"""

from django.db import migrations


def blank_duplicate_hashes(apps, schema_editor):
    Transaction = apps.get_model("api", "Transaction")
    rows = (
        Transaction.objects.filter(author__isnull=True)
        .exclude(import_hash="")
        .order_by("id")
        .values_list("id", "import_hash")
    )
    seen = set()
    for pk, import_hash in rows.iterator():
        if import_hash in seen:
            Transaction.objects.filter(pk=pk).update(import_hash="")
        else:
            seen.add(import_hash)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0006_drop_category_from_import_hash"),
    ]

    operations = [
        migrations.RunPython(blank_duplicate_hashes, migrations.RunPython.noop),
    ]
