from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.index, name='index'),
    path('login/', views.DashboardLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(next_page='dashboard:login'), name='logout'),
    path('reports/', views.reports, name='reports'),
    path('reports/export/', views.export_bookings, name='export'),
    path('bookings/<int:pk>/', views.booking_detail, name='booking'),
    path('manage/<slug:entity>/', views.entity_list, name='list'),
    path('manage/<slug:entity>/add/', views.entity_form, name='add'),
    path('manage/<slug:entity>/<int:pk>/edit/', views.entity_form, name='edit'),
    path('manage/<slug:entity>/<int:pk>/delete/', views.entity_delete, name='delete'),
    path('schedules/bulk/', views.bulk_schedules, name='bulk_schedules'),
    path('bookings/<int:pk>/notify/', views.resend_notifications, name='resend_notifications'),
]