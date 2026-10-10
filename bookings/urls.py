from django.urls import path

from . import views

app_name = 'bookings'

urlpatterns = [
    path('passengers/', views.passenger_details, name='passengers'),
    path('my/', views.my_bookings, name='my'),
    path('find/', views.find_booking, name='find'),
    path('<str:pnr>/', views.booking_detail, name='detail'),
    path('<str:pnr>/ticket/', views.ticket, name='ticket'),
    path('<str:pnr>/cancel/', views.cancel_booking, name='cancel'),
]