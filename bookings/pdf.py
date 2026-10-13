from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing, Line, Polygon
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY = colors.HexColor('#0A3D91')
BLUE = colors.HexColor('#0D6EFD')
LIGHT = colors.HexColor('#EAF2FF')
GRAY = colors.HexColor('#6C7A8F')
LINE = colors.HexColor('#C9D3E3')
GREEN = colors.HexColor('#198754')
AMBER = colors.HexColor('#B8860B')
RED = colors.HexColor('#B00020')

PAGE_W, PAGE_H = A4
MARGIN = 14 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

_font_cache = {}


def _fonts(font_dir):
    """(regular, bold, rupee symbol). Falls back to Helvetica and 'Rs.' without the TTF files."""
    key = str(font_dir)
    if key in _font_cache:
        return _font_cache[key]
    result = ('Helvetica', 'Helvetica-Bold', 'Rs. ')
    if font_dir:
        regular_file = Path(font_dir) / 'DejaVuSans.ttf'
        bold_file = Path(font_dir) / 'DejaVuSans-Bold.ttf'
        if regular_file.exists() and bold_file.exists():
            names = pdfmetrics.getRegisteredFontNames()
            if 'DejaVuSans' not in names:
                pdfmetrics.registerFont(TTFont('DejaVuSans', str(regular_file)))
            if 'DejaVuSans-Bold' not in names:
                pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', str(bold_file)))
            result = ('DejaVuSans', 'DejaVuSans-Bold', '\u20b9')
    _font_cache[key] = result
    return result


def _arrow(width):
    drawing = Drawing(width, 8)
    drawing.add(Line(0, 4, width - 6, 4, strokeColor=BLUE, strokeWidth=1.4))
    drawing.add(Polygon([width - 7, 0, width, 4, width - 7, 8], fillColor=BLUE, strokeColor=BLUE))
    return drawing


def _qr(value, size):
    widget = QrCodeWidget(value, barLevel='M')
    x1, y1, x2, y2 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    drawing.add(widget)
    return drawing


def _safe(value):
    """Escape text for ReportLab's mini-HTML, so a name like 'A & B <b>' cannot break the PDF."""
    if isinstance(value, str):
        return escape(value)
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    return value


def render_ticket_pdf(data, font_dir=None, logo_path=None):
    """Build the e-ticket PDF from a plain dict (see bookings/ticket.py) and return the bytes."""
    verify_url = data['verify_url']  # goes into the QR code as-is, never into a Paragraph
    data = _safe(data)
    regular, bold, rupee = _fonts(font_dir)

    def style(name, font=None, size=9, color=colors.black, leading=None, align=0, **extra):
        return ParagraphStyle(name, fontName=font or regular, fontSize=size, textColor=color,
                              leading=leading or size * 1.3, alignment=align, **extra)

    small = style('small', size=8, color=GRAY)
    label = style('label', size=7, color=GRAY, leading=9)
    body = style('body', size=9)
    body_bold = style('body_bold', font=bold, size=9)
    city = style('city', font=bold, size=17, color=NAVY, leading=20)
    city_right = style('city_right', font=bold, size=17, color=NAVY, leading=20, align=2)
    time_left = style('time_left', size=9.5, leading=12)
    time_right = style('time_right', size=9.5, leading=12, align=2)
    center_gray = style('center_gray', size=8, color=GRAY, align=1)
    head_left = style('head_left', font=bold, size=18, color=colors.white, leading=22)
    head_sub = style('head_sub', size=7.5, color=colors.HexColor('#DCE8FF'), leading=10)
    head_tag = style('head_tag', size=8, color=colors.HexColor('#DCE8FF'), leading=10, align=2)
    head_pnr = style('head_pnr', font=bold, size=17, color=colors.white, leading=21, align=2)
    th = style('th', font=bold, size=8, color=colors.white)
    th_right = style('th_right', font=bold, size=8, color=colors.white, align=2)
    td_right = style('td_right', size=9, align=2)
    td_right_bold = style('td_right_bold', font=bold, size=10, align=2)
    policy_style = style('policy', size=7.5, color=colors.HexColor('#33415C'), leading=10.5)

    def money(amount):
        return f'{rupee}{amount}'

    def field(title, value, extra=None):
        parts = [Paragraph(title.upper(), label), Paragraph(value, body_bold)]
        if extra:
            parts.append(Paragraph(extra, small))
        return parts

    story = []

    # ---- Header band ----
    brand_cell = [Paragraph(data['brand'], head_left), Paragraph(data['tagline'], head_sub)]
    if logo_path and Path(logo_path).exists():
        logo = Image(str(logo_path), width=12 * mm, height=12 * mm)
        brand_cell = Table([[logo, brand_cell]], colWidths=[15 * mm, 90 * mm])
        brand_cell.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
    header = Table(
        [[brand_cell, [Paragraph('E-TICKET', head_tag), Paragraph(data['pnr'], head_pnr)]]],
        colWidths=[CONTENT_W * 0.6, CONTENT_W * 0.4])
    header.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8 * mm), ('RIGHTPADDING', (0, 0), (-1, -1), 8 * mm),
        ('TOPPADDING', (0, 0), (-1, -1), 4 * mm), ('BOTTOMPADDING', (0, 0), (-1, -1), 4 * mm),
    ]))
    story += [header, Spacer(1, 3 * mm)]

    # ---- Status row ----
    ok = data['status_ok']
    status_color = GREEN if ok else RED
    status = Table([[
        Paragraph(f'<font color="{status_color.hexval().replace("0x", "#")}"><b>'
                  f'BOOKING {data["status"].upper()}</b></font>', style('st', font=bold, size=10)),
        Paragraph(f'Payment: <b>{data["payment_status"]}</b>', body),
        Paragraph(f'Booked on {data["booked_on"]}', style('bo', size=8.5, color=GRAY, align=2)),
    ]], colWidths=[CONTENT_W * 0.36, CONTENT_W * 0.30, CONTENT_W * 0.34])
    status.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4 * mm), ('RIGHTPADDING', (0, 0), (-1, -1), 4 * mm),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5 * mm), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5 * mm),
    ]))
    story += [status, Spacer(1, 4 * mm)]

    # ---- Route ----
    route = Table([[
        [Paragraph(data['source'], city), Paragraph(f'<b>{data["departure_time"]}</b>', time_left),
         Paragraph(data['travel_date'], small)],
        [Paragraph(f'{data["duration"]} | {data["distance"]}', center_gray), _arrow(38 * mm)],
        [Paragraph(data['destination'], city_right),
         Paragraph(f'<b>{data["arrival_time"]}</b>', time_right),
         Paragraph(data['arrival_date'], style('ar', size=8, color=GRAY, align=2))],
    ]], colWidths=[CONTENT_W * 0.36, CONTENT_W * 0.28, CONTENT_W * 0.36])
    route.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, 0), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, LINE), ('BOTTOMPADDING', (0, 0), (-1, 0), 4 * mm),
    ]))
    story += [route, Spacer(1, 3 * mm)]

    # ---- Journey details ----
    col = CONTENT_W / 3
    details = Table([
        [field('Bus operator', data['operator']), field('Bus number', data['bus_number']),
         field('Bus type', data['bus_type'])],
        [field('Boarding point', data['boarding_name'], data['boarding_time']),
         field('Dropping point', data['dropping_name'], data['dropping_time']),
         field('Contact', data['contact_phone'], data['contact_email'])],
    ], colWidths=[col, col, col])
    details.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 3 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3 * mm),
    ]))
    story += [details]

    # boarding / dropping addresses (only when present)
    addresses = [a for a in (data['boarding_address'], data['dropping_address']) if a]
    if addresses:
        story.append(Paragraph(
            f'Boarding address: {data["boarding_address"] or "-"} &nbsp;|&nbsp; '
            f'Dropping address: {data["dropping_address"] or "-"}', small))
    story.append(Spacer(1, 3 * mm))

    # ---- Passengers ----
    rows = [[Paragraph('#', th), Paragraph('PASSENGER NAME', th), Paragraph('AGE', th),
             Paragraph('GENDER', th), Paragraph('SEAT', th_right)]]
    for number, passenger in enumerate(data['passengers'], 1):
        rows.append([Paragraph(str(number), body), Paragraph(passenger['name'], body),
                     Paragraph(str(passenger['age']), body), Paragraph(passenger['gender'], body),
                     Paragraph(f'<b>{passenger["seat"]}</b>', td_right)])
    passengers = Table(rows, colWidths=[10 * mm, CONTENT_W - 10 * mm - 20 * mm - 28 * mm - 24 * mm,
                                        20 * mm, 28 * mm, 24 * mm], repeatRows=1)
    passengers.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5F8FD')]),
        ('LINEBELOW', (0, 0), (-1, -1), 0.4, LINE),
        ('TOPPADDING', (0, 0), (-1, -1), 1.4 * mm), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.4 * mm),
    ]))
    story += [passengers, Spacer(1, 3 * mm)]

    # ---- Fare breakdown and payment ----
    fare_rows = [[Paragraph('FARE BREAKDOWN', th), Paragraph('AMOUNT', th_right)]]
    for line in data['fare_lines']:
        fare_rows.append([Paragraph(line['text'], body), Paragraph(money(line['amount']), td_right)])
    fare_rows.append([Paragraph('Total fare', body_bold),
                      Paragraph(money(data['total']), td_right_bold)])
    fare_w = CONTENT_W * 0.6 - 4 * mm
    fare = Table(fare_rows, colWidths=[fare_w * 0.68, fare_w * 0.32])
    last = len(fare_rows) - 1
    fare.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('LINEBELOW', (0, 1), (-1, last - 1), 0.4, LINE),
        ('LINEABOVE', (0, last), (-1, last), 1, NAVY),
        ('BACKGROUND', (0, last), (-1, last), LIGHT),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 1.2 * mm), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.2 * mm),
    ]))
    payment = [
        Paragraph('PAYMENT', label),
        Paragraph(data['payment_method'], body_bold),
        Paragraph(f'Status: {data["payment_status"]}', body),
    ]
    if data['transaction_id']:
        payment.append(Paragraph(f'Ref: {data["transaction_id"]}', small))
    payment.append(Paragraph(data['payment_note'], style('demo', size=7.5, color=AMBER)))
    money_block = Table([[fare, payment]], colWidths=[CONTENT_W * 0.6, CONTENT_W * 0.4])
    money_block.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (0, 0), 4 * mm),
        ('LEFTPADDING', (1, 0), (1, 0), 4 * mm),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    story += [money_block, Spacer(1, 4 * mm)]

    # ---- QR code and cancellation policy ----
    policy = [Paragraph('CANCELLATION AND REFUND POLICY', label)]
    policy += [Paragraph(f'&bull; {line}', policy_style) for line in data['policy']]
    qr_cell = [_qr(verify_url, 30 * mm), Paragraph('Scan to verify this ticket', center_gray)]
    footer_block = Table([[qr_cell, policy]], colWidths=[38 * mm, CONTENT_W - 38 * mm])
    footer_block.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.6, LINE),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4 * mm), ('RIGHTPADDING', (0, 0), (-1, -1), 4 * mm),
        ('TOPPADDING', (0, 0), (-1, -1), 3 * mm), ('BOTTOMPADDING', (0, 0), (-1, -1), 3 * mm),
        ('LINEAFTER', (0, 0), (0, 0), 0.4, LINE),
    ]))
    story.append(footer_block)

    def draw_page(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.line(MARGIN, 16 * mm, PAGE_W - MARGIN, 16 * mm)
        canvas.setFillColor(GRAY)
        canvas.setFont(regular, 7)
        canvas.drawCentredString(PAGE_W / 2, 12.5 * mm, data['disclaimer'])
        canvas.drawString(MARGIN, 8.5 * mm, f'{data["brand"]} E-Ticket | PNR {data["pnr"]}')
        canvas.drawRightString(PAGE_W - MARGIN, 8.5 * mm, f'Page {doc.page}')
        canvas.restoreState()

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=11 * mm, bottomMargin=19 * mm,
        title=f'E-Ticket {data["pnr"]}', author=data['brand'], subject='Bus e-ticket')
    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buffer.getvalue()