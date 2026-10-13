from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = 'accounts'

urlpatterns = [
    # path('login/', views.UserLoginView.as_view(), name='login'),
    path('login/', views.login_view, name='login'),
    path('register/', views.register, name='register'),
    path('verify/', views.verify, name='verify'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('profile/', views.profile, name='profile'),
    path('password/', views.UserPasswordChangeView.as_view(), name='password_change'),
]