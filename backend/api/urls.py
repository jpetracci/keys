from django.urls import path
from . import views

urlpatterns = [
    path('transactions/', views.TransactionListCreate.as_view(), name='trans-list-create'),
    path('transactions/summary/', views.TransactionSummary.as_view(), name='trans-summary'),
    path('transactions/<int:pk>/', views.TransactionDetail.as_view(), name='trans-detail'),
    # Back-compat alias for the original delete endpoint used by the frontend.
    path('transactions/delete/<int:pk>/', views.TransactionDetail.as_view(), name='trans-delete'),
]
