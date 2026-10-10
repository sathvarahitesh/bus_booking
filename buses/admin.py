from django.contrib import admin, messages
from datetime import timedelta

from .services import create_schedules

from .models import (BoardingPoint, Bus, BusOperator, BusSchedule,
                     DroppingPoint, Route, Seat)


@admin.register(BusOperator)
class BusOperatorAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact_phone', 'contact_email', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name',)


class SeatInline(admin.TabularInline):
    model = Seat
    extra = 0


@admin.register(Bus)
class BusAdmin(admin.ModelAdmin):
    list_display = ('bus_number', 'operator', 'bus_type', 'is_ac', 'seat_type',
                    'total_seats', 'seat_price', 'is_active')
    list_filter = ('bus_type', 'is_ac', 'seat_type', 'is_active', 'operator')
    search_fields = ('bus_number', 'operator__name')
    list_select_related = ('operator',)
    actions = ['generate_seats']
    inlines = [SeatInline]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            obj.generate_seats()

    @admin.action(description='Generate seats for selected buses')
    def generate_seats(self, request, queryset):
        created = sum(bus.generate_seats() for bus in queryset)
        self.message_user(request, f'{created} seats created.', messages.SUCCESS)


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ('bus', 'seat_number', 'deck', 'row', 'column')
    list_filter = ('deck', 'bus')
    search_fields = ('bus__bus_number', 'seat_number')
    list_select_related = ('bus',)


class BoardingPointInline(admin.TabularInline):
    model = BoardingPoint
    extra = 1


class DroppingPointInline(admin.TabularInline):
    model = DroppingPoint
    extra = 1


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ('source', 'destination', 'distance_km', 'duration')
    search_fields = ('source', 'destination')
    inlines = [BoardingPointInline, DroppingPointInline]


@admin.register(BoardingPoint)
class BoardingPointAdmin(admin.ModelAdmin):
    list_display = ('name', 'route', 'minutes_before')
    search_fields = ('name', 'route__source', 'route__destination')
    list_select_related = ('route',)


@admin.register(DroppingPoint)
class DroppingPointAdmin(admin.ModelAdmin):
    list_display = ('name', 'route', 'minutes_before')
    search_fields = ('name', 'route__source', 'route__destination')
    list_select_related = ('route',)


@admin.register(BusSchedule)
class BusScheduleAdmin(admin.ModelAdmin):
    list_display = ('route', 'bus', 'travel_date', 'departure_time', 'arrival_time', 'is_active')
    list_filter = ('is_active', 'travel_date', 'route')
    search_fields = ('bus__bus_number', 'route__source', 'route__destination')
    list_select_related = ('bus', 'route')
    date_hierarchy = 'travel_date'
    readonly_fields = ('arrival_time',)

    actions = ['repeat_30_days']

    @admin.action(description='Repeat selected trips daily for the next 30 days')
    def repeat_30_days(self, request, queryset):
        created = 0
        for trip in queryset.select_related('bus', 'route'):
            result = create_schedules(
                trip.bus, trip.route,
                trip.travel_date + timedelta(days=1), trip.travel_date + timedelta(days=30),
                trip.departure_time, set(range(7)))
            created += result['created']
        self.message_user(request, f'{created} trips created.', messages.SUCCESS)