import csv
from datetime import timedelta
from decimal import Decimal
from core.throttle import ThrottledPostMixin
from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from django.db.models import Count, Max, ProtectedError, Q, Sum
from buses.services import create_schedules, extend_schedules
from .forms import BulkScheduleForm, StaffLoginForm, make_form_class

from django.db.models.functions import TruncDate
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from accounts.models import User
from bookings.models import Booking, Cancellation
from buses.models import Bus, BusSchedule, Route
from payments.models import Payment

from .config import ENTITIES, NAV
from .decorators import staff_required
from .forms import StaffLoginForm, make_form_class
from bookings.notifications import send_confirmation

PAGE_SIZE = 15


# ---------- helpers ----------
def page(request, template, context):
    context['nav'] = NAV
    return render(request, template, context)


def get_cfg(entity):
    cfg = ENTITIES.get(entity)
    if not cfg:
        raise Http404('Unknown section')
    return cfg


def entity_queryset(cfg):
    qs = cfg['model'].objects.all()
    if cfg.get('base_filter'):
        qs = qs.filter(**cfg['base_filter'])
    if cfg.get('select_related'):
        qs = qs.select_related(*cfg['select_related'])
    if not qs.ordered:
        qs = qs.order_by('-pk')
    return qs


def resolve(obj, path):
    """Follow a dotted path like 'bus.operator.name'; call it if it is a method."""
    value = obj
    for part in path.split('.'):
        if value is None:
            return None
        value = getattr(value, part, None)
        if callable(value):
            value = value()
    return value


def safe_date(value):
    try:
        return parse_date(value or '')
    except ValueError:
        return None


def money(qs, field):
    return qs.aggregate(total=Sum(field))['total'] or Decimal('0')


def revenue_figures(start=None, end=None):
    payments = Payment.objects.filter(status__in=['SUCCESS', 'REFUNDED'])
    refunds = Cancellation.objects.filter(booking__payment_status='REFUNDED')
    if start and end:
        payments = payments.filter(paid_at__date__range=(start, end))
        refunds = refunds.filter(cancelled_at__date__range=(start, end))
    gross = money(payments, 'amount')
    refunded = money(refunds, 'refund_amount')
    return {'gross': gross, 'refunds': refunded, 'net': gross - refunded}


def report_range(request):
    today = timezone.localdate()
    start = safe_date(request.GET.get('start')) or today - timedelta(days=29)
    end = safe_date(request.GET.get('end')) or today
    if start > end:
        start, end = end, start
    return start, end


# ---------- authentication ----------
class DashboardLoginView(ThrottledPostMixin, LoginView):
    throttle_scope = 'dashboard-login'
    template_name = 'dashboard/login.html'
    authentication_form = StaffLoginForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_staff:
            return redirect('dashboard:index')
        return super().dispatch(request, *args, **kwargs)

    def get_default_redirect_url(self):
        return reverse('dashboard:index')


# ---------- dashboard home ----------
@staff_required
def index(request):
    today = timezone.localdate()
    revenue = revenue_figures()
    confirmed = Booking.objects.filter(booking_status='CONFIRMED')

    stats = [
        {'label': 'Net revenue', 'value': f"\u20b9{revenue['net']:,.0f}", 'icon': 'bi-currency-rupee', 'color': 'success'},
        {'label': 'Confirmed bookings', 'value': confirmed.count(), 'icon': 'bi-ticket-perforated', 'color': 'primary'},
        {'label': 'Bookings today', 'value': confirmed.filter(created_at__date=today).count(), 'icon': 'bi-calendar-check', 'color': 'info'},
        {'label': 'Cancellations', 'value': Cancellation.objects.count(), 'icon': 'bi-x-circle', 'color': 'danger'},
        {'label': 'Pay-at-counter pending', 'value': confirmed.filter(payment_status='PENDING').count(), 'icon': 'bi-cash-coin', 'color': 'warning'},
        {'label': 'Customers', 'value': User.objects.filter(is_staff=False).count(), 'icon': 'bi-people', 'color': 'secondary'},
        {'label': 'Active buses', 'value': Bus.objects.filter(is_active=True).count(), 'icon': 'bi-bus-front', 'color': 'dark'},
        {'label': 'Upcoming trips', 'value': BusSchedule.objects.filter(is_active=True, travel_date__gte=today).count(), 'icon': 'bi-calendar-event', 'color': 'primary'},
    ]

    start = today - timedelta(days=6)
    daily = dict(
        Payment.objects.filter(status__in=['SUCCESS', 'REFUNDED'], paid_at__date__gte=start)
        .annotate(day=TruncDate('paid_at')).values('day')
        .annotate(total=Sum('amount')).values_list('day', 'total'))
    days = [start + timedelta(days=i) for i in range(7)]

    return page(request, 'dashboard/index.html', {
        'active': 'index',
        'stats': stats,
        'chart_labels': [d.strftime('%d %b') for d in days],
        'chart_values': [float(daily.get(d, 0)) for d in days],
        'recent': Booking.objects.select_related('user', 'schedule__route').order_by('-created_at')[:8],
    })


# ---------- generic CRUD ----------
@staff_required
def entity_list(request, entity):
    cfg = get_cfg(entity)
    qs = entity_queryset(cfg)

    q = request.GET.get('q', '').strip()
    if q and cfg.get('search'):
        condition = Q()
        for field in cfg['search']:
            condition |= Q(**{f'{field}__icontains': q})
        qs = qs.filter(condition).distinct()

    filters_ui = []
    for flt in cfg.get('filters', []):
        value = request.GET.get(flt['param'], '')
        if value:
            if flt['type'] == 'bool' and value in ('0', '1'):
                qs = qs.filter(**{flt['lookup']: value == '1'})
            elif flt['type'] == 'choice' and value in dict(flt['choices']):
                qs = qs.filter(**{flt['lookup']: value})
            elif flt['type'] == 'date':
                day = safe_date(value)
                if day:
                    qs = qs.filter(**{flt['lookup']: day})
        filters_ui.append({**flt, 'value': value})

    paginator = Paginator(qs, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    rows = []
    for obj in page_obj.object_list:
        cells = []
        for _label, path in cfg['columns']:
            value = resolve(obj, path)
            cells.append({'is_bool': isinstance(value, bool), 'value': value})
        rows.append({'obj': obj, 'cells': cells})

    params = request.GET.copy()
    params.pop('page', None)

    return page(request, 'dashboard/list.html', {
        'active': entity,
        'entity': entity,
        'meta': {k: cfg.get(k) for k in ('title', 'singular', 'icon', 'create', 'edit', 'delete', 'detail')},
        'headers': [label for label, _path in cfg['columns']],
        'colspan': len(cfg['columns']) + 1,
        'rows': rows,
        'page': page_obj,
        'q': q,
        'has_search': bool(cfg.get('search')),
        'filters': filters_ui,
        'qs': params.urlencode(),
    })


@staff_required
def entity_form(request, entity, pk=None):
    cfg = get_cfg(entity)
    if (pk is None and not cfg['create']) or (pk is not None and not cfg['edit']):
        raise Http404('Not allowed')

    instance = get_object_or_404(entity_queryset(cfg), pk=pk) if pk else None
    form = make_form_class(cfg['model'], cfg['fields'])(request.POST or None, instance=instance)

    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        if pk is None and cfg.get('after_create'):
            cfg['after_create'](obj)
        if cfg.get('after_save'):
            cfg['after_save'](obj)
        messages.success(request, f"{cfg['singular']} {'updated' if pk else 'created'} successfully.")
        return redirect('dashboard:list', entity=entity)

    return page(request, 'dashboard/form.html', {
        'active': entity,
        'entity': entity,
        'form': form,
        'title': f"{'Edit' if pk else 'Add'} {cfg['singular']}",
        'back_title': cfg['title'],
        'bulk_form': BulkScheduleForm() if entity == 'schedules' and pk is None else None,
        'extend_choices': EXTEND_CHOICES,
    })


@staff_required
@require_POST
def entity_delete(request, entity, pk):
    cfg = get_cfg(entity)
    if not cfg['delete']:
        raise Http404('Not allowed')
    obj = get_object_or_404(entity_queryset(cfg), pk=pk)
    try:
        obj.delete()
        messages.success(request, f"{cfg['singular']} deleted.")
    except ProtectedError:
        messages.error(request, f"This {cfg['singular'].lower()} is used by other records "
                                '(schedules, seats or bookings) and cannot be deleted. '
                                'Mark it inactive instead.')
    return redirect('dashboard:list', entity=entity)


@staff_required
def booking_detail(request, pk):
    booking = get_object_or_404(
        Booking.objects.select_related(
            'user', 'schedule__bus__operator', 'schedule__route', 'boarding_point', 'dropping_point'),
        pk=pk)
    return page(request, 'dashboard/booking_detail.html', {
        'active': 'bookings',
        'booking': booking,
        'schedule': booking.schedule,
        'passengers': booking.passengers.select_related('seat'),
        'payments': booking.payments.all(),
    })


# ---------- reports ----------
@staff_required
def reports(request):
    start, end = report_range(request)
    figures = revenue_figures(start, end)

    payments = Payment.objects.filter(
        status__in=['SUCCESS', 'REFUNDED'], paid_at__date__range=(start, end))
    sold = Booking.objects.filter(
        booking_status='CONFIRMED', created_at__date__range=(start, end))

    by_day = (payments.annotate(day=TruncDate('paid_at')).values('day')
              .annotate(n=Count('id'), total=Sum('amount')).order_by('day'))
    by_route = (sold.values('schedule__route__source', 'schedule__route__destination')
                .annotate(n=Count('id'), total=Sum('total_amount')).order_by('-total')[:10])
    by_operator = (sold.values('schedule__bus__operator__name')
                   .annotate(n=Count('id'), total=Sum('total_amount')).order_by('-total'))
    method_names = dict(Payment.METHODS)
    by_method = [
        {'label': method_names.get(row['method'], row['method']), 'n': row['n'], 'total': row['total']}
        for row in payments.values('method').annotate(n=Count('id'), total=Sum('amount')).order_by('-total')
    ]

    return page(request, 'dashboard/reports.html', {
        'active': 'reports',
        'start': start, 'end': end,
        'figures': figures,
        'confirmed_count': sold.count(),
        'cancelled_count': Cancellation.objects.filter(cancelled_at__date__range=(start, end)).count(),
        'by_day': by_day, 'by_route': by_route, 'by_operator': by_operator, 'by_method': by_method,
    })


@staff_required
def export_bookings(request):
    start, end = report_range(request)
    bookings = (Booking.objects.filter(created_at__date__range=(start, end))
                .select_related('user', 'schedule__route', 'schedule__bus')
                .prefetch_related('passengers__seat').order_by('created_at'))

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="bookings_{start}_{end}.csv"'
    writer = csv.writer(response)
    writer.writerow(['PNR', 'Account', 'Mobile', 'Route', 'Bus', 'Travel date', 'Seats',
                     'Amount', 'Booking status', 'Payment status', 'Booked on'])
    for b in bookings:
        writer.writerow([
            b.pnr,
            b.user.username if b.user else 'Guest',
            b.contact_phone,
            str(b.schedule.route),
            b.schedule.bus.bus_number,
            b.schedule.travel_date,
            ' '.join(p.seat.seat_number for p in b.passengers.all()),
            b.total_amount,
            b.get_booking_status_display(),
            b.get_payment_status_display(),
            timezone.localtime(b.created_at).strftime('%Y-%m-%d %H:%M'),
        ])
    return response

EXTEND_CHOICES = [30, 45, 60, 90]


@staff_required
def bulk_schedules(request):
    form = BulkScheduleForm()
    if request.method == 'POST':
        if request.POST.get('action') == 'extend':
            try:
                days = int(request.POST.get('days', ''))
            except ValueError:
                days = 0
            if days not in EXTEND_CHOICES:
                messages.error(request, 'Choose a valid number of days.')
            else:
                r = extend_schedules(days)
                messages.success(
                    request,
                    f"{r['patterns']} regular trips extended: {r['created']} created, "
                    f"{r['skipped']} already existed, {r['conflicts']} skipped (bus busy).")
            # return redirect('dashboard:bulk_schedules')
            return redirect('dashboard:list', entity='schedules')
        

        form = BulkScheduleForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            r = create_schedules(d['bus'], d['route'], d['start_date'], d['end_date'],
                                 d['departure_time'], set(d['weekdays']))
            messages.success(
                request,
                f"{r['created']} trips created, {r['skipped']} already existed, "
                f"{r['conflicts']} skipped because the bus is busy on another trip.")
            # return redirect('dashboard:bulk_schedules')
            return redirect('dashboard:list', entity='schedules')

    return page(request, 'dashboard/bulk_schedules.html', {
        'active': 'bulk',
        'form': form,
        'last_date': BusSchedule.objects.aggregate(last=Max('travel_date'))['last'],
        'extend_choices': EXTEND_CHOICES,
    })

@staff_required
@require_POST
def resend_notifications(request, pk):
    booking = get_object_or_404(Booking, pk=pk)
    if booking.booking_status != 'CONFIRMED':
        messages.error(request, 'Only confirmed bookings can be notified.')
    else:
        send_confirmation(booking, mode='force')
        messages.success(request, 'Resend finished. Check the notification statuses below.')
    return redirect('dashboard:booking', pk=pk)