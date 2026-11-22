from django.contrib import admin
from django.urls import path, include, re_path
from api.views import CreateUserView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from . import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/health/', views.health, name='health'),
    path('api/user/register/', CreateUserView.as_view(), name='register'),
    path('api/token/', TokenObtainPairView.as_view(), name='get-token'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='refresh'),
    path('api-auth/', include('rest_framework.urls')),
    path('api/', include('api.urls')),
    # Every other address belongs to the frontend, when this app serves it.
    # The prefixes above are excluded with or without their slash, so that
    # "/admin" still redirects to "/admin/" instead of landing here.
    re_path(
        r'^(?!(?:admin|api|api-auth|static)(?:/|$))(?P<path>.*)$',
        views.frontend,
        name='frontend',
    ),
]
