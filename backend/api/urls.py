from django.urls import path
from . import views

urlpatterns = [
    path('transactions/', views.TransactionListCreate.as_view(), name='trans-create'),
    path('transactions/delete/<int:pk>/', views.TransactionDelete.as_view(), name='trans-delete'),
]