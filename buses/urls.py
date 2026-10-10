from django.urls import path

from . import views

app_name = 'buses'

urlpatterns = [
    path('search/', views.search, name='search'),
    path('routes/', views.route_list, name='routes'),
    path('schedule/<int:schedule_id>/seats/', views.seat_selection, name='seats'),
    path('schedule/<int:schedule_id>/seats/status/', views.seat_status, name='seat_status'),
    
]