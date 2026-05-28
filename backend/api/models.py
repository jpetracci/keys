from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Transaction(models.Model):
    trans_date = models.DateField(default=timezone.localdate)
    trans_description = models.CharField(max_length=255)
    trans_category = models.CharField(max_length=100, blank=True, default="Uncategorized")
    trans_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    account_name = models.CharField(max_length=100, blank=True, default="")
    source_file = models.CharField(max_length=255, blank=True, default="")
    import_hash = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    author = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="transactions",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-trans_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["author", "import_hash"],
                condition=~Q(import_hash=""),
                name="unique_transaction_import_per_user",
            )
        ]

    def __str__(self):
        return f"{self.trans_date} {self.trans_description} ({self.trans_amount})"
