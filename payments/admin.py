from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('transaction_id', 'booking', 'method', 'amount', 'status', 'paid_at')
    list_filter = ('method', 'status')
    search_fields = ('transaction_id', 'booking__pnr')
    list_select_related = ('booking',)
    readonly_fields = ('transaction_id', 'created_at')