from django.db import models
from django.contrib.auth.models import User

# Create your models here.
class Transaction(models.Model):
    trans_date = models.DateTimeField(auto_now_add=True)
    trans_description = models.CharField(max_length=100)
    trans_category = models.CharField(max_length=20)
    trans_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='transactions')

    def __str__(self):
        return self.trans_description
    