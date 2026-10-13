from django.urls import path

from . import views

app_name = 'bookings'

urlpatterns = [
    path('passengers/', views.passenger_details, name='passengers'),
    path('my/', views.my_bookings, name='my'),
    path('find/', views.find_booking, name='find'),
    path('verify/<uuid:uid>/', views.verify_ticket, name='verify'),
    path('access/<str:token>/', views.access_booking, name='access'),
    path('access/<str:token>/pdf/', views.access_pdf, name='access_pdf'),
    path('<str:pnr>/', views.booking_detail, name='detail'),
    path('<str:pnr>/ticket/', views.ticket, name='ticket'),
    path('<str:pnr>/ticket.pdf', views.ticket_pdf, name='ticket_pdf'),
    path('<str:pnr>/cancel/', views.cancel_booking, name='cancel'),
]