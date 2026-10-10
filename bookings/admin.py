from django.contrib import admin

from payments.models import Payment

from .models import Booking, Cancellation, Passenger


class PassengerInline(admin.TabularInline):
    model = Passenger
    extra = 0


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ('pnr', 'user', 'schedule', 'total_amount', 'booking_status',
                    'payment_status', 'created_at')
    list_filter = ('booking_status', 'payment_status', 'created_at')
    search_fields = ('pnr', 'user__username', 'user__email', 'contact_phone')
    list_select_related = ('user', 'schedule__route', 'schedule__bus')
    readonly_fields = ('pnr', 'created_at')
    inlines = [PassengerInline, PaymentInline]


@admin.register(Cancellation)
class CancellationAdmin(admin.ModelAdmin):
    list_display = ('booking', 'refund_amount', 'cancelled_at')
    search_fields = ('booking__pnr',)
    list_select_related = ('booking',)