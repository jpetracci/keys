from django.contrib import admin

from .models import Transaction


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "trans_date",
        "trans_description",
        "trans_category",
        "trans_amount",
        "account_name",
        "author",
    )
    list_filter = ("trans_category", "account_name", "author")
    search_fields = ("trans_description", "trans_category", "account_name", "source_file")
    date_hierarchy = "trans_date"
