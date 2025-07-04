"""
URL configuration for attendancemachine project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https:///docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from myapp.views import RegisterView, LoginView, DashboardView, LogoutView, ChangePasswordView
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/register/', RegisterView.as_view(), name='auth_register'),
    path('api/auth/login/', LoginView.as_view(), name='auth_login'),
    path('api/auth/logout/', LogoutView.as_view(), name='auth_logout'),
    path('api/auth/change-password/', ChangePasswordView.as_view(), name='auth_change_password'),
    path('api/dashboard/', DashboardView.as_view(), name='dashboard'),
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # Include employee app routes
    path('api/employee/', include('employee.urls')),

    # ✅ Include profiles app routes
    path('api/profiles/', include('profiles.urls')),

    path('api/leave/', include('leave.urls')),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)