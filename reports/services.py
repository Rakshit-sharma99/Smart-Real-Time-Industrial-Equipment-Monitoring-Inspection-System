"""
PDF reports (ReportLab platypus): the operations report and a table history.

Shared layout: header with app and report name, footer with generation time
and "Page X of Y", tables that repeat their header row across pages.
Authorization happens in the views BEFORE these functions are called.
"""
import io
import re
from datetime import timedelta
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from inspections.models import TableInspection
from reservations.models import Reservation
from tables.models import TableStatus

from .analytics import ESTIMATE

INK = colors.HexColor("#1c1f23")
MUTED = colors.HexColor("#6b7280")
BORDER = colors.HexColor("#e6e6e3")
HEADER_BG = colors.HexColor("#f3f3f0")
ACCENT = colors.HexColor("#0f766e")

styles = getSampleStyleSheet()
TITLE = ParagraphStyle("Title", parent=styles["Heading1"], fontSize=16, textColor=INK, spaceAfter=2)
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=11.5, textColor=INK, spaceBefore=10, spaceAfter=4)
BODY = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=9, leading=12, textColor=INK)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8, leading=10, textColor=MUTED)
CELL = ParagraphStyle("Cell", parent=BODY, fontSize=8.5, leading=11)


class NumberedCanvas(canvas.Canvas):
    """Canvas that knows the total page count, so the footer can say "Page X of Y"."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_pages = []

    def showPage(self):
        self._saved_pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_pages)
        for page_state in self._saved_pages:
            self.__dict__.update(page_state)
            self.setFont("Helvetica", 8)
            self.setFillColor(MUTED)
            self.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {self._pageNumber} of {total}")
            super().showPage()
        super().save()


def safe_filename(*parts):
    """operations_copper-fork_2026-11-01_2026-11-07.pdf - letters, digits, dot, dash and underscore only."""
    name = "_".join(str(part) for part in parts)
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") + ".pdf"


def text(value):
    return escape(str(value if value is not None else "")).replace("\n", "<br/>")


def local(dt, fmt="%d %b %Y %H:%M"):
    return timezone.localtime(dt).strftime(fmt) if dt else "-"


def grid(rows, widths, header=True):
    """A styled table that splits across pages and repeats its header row."""
    result = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), HEADER_BG), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8)]
    result.setStyle(TableStyle(style))
    return result


def build_pdf(story, title, compress=True):
    buffer = io.BytesIO()
    generated = timezone.localtime().strftime("%d %b %Y, %H:%M")
    footer_left = " · ".join(part for part in [settings.COLLEGE_NAME, settings.TEAM_NAMES] if part)

    def decorate(pdf, doc):
        pdf.saveState()
        width, height = A4
        pdf.setFillColor(ACCENT)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(18 * mm, height - 14 * mm, settings.APP_NAME)
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 9)
        pdf.drawRightString(width - 18 * mm, height - 14 * mm, title)
        pdf.setStrokeColor(BORDER)
        pdf.line(18 * mm, height - 17 * mm, width - 18 * mm, height - 17 * mm)
        pdf.line(18 * mm, 15 * mm, width - 18 * mm, 15 * mm)
        pdf.setFont("Helvetica", 8)
        pdf.drawString(18 * mm, 10 * mm, f"Generated {generated}" + (f" · {footer_left}" if footer_left else ""))
        pdf.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, title=title, author=settings.APP_NAME,
                            pageCompression=1 if compress else 0,
                            leftMargin=18 * mm, rightMargin=18 * mm, topMargin=24 * mm, bottomMargin=22 * mm)
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate, canvasmaker=NumberedCanvas)
    return buffer.getvalue()


def fmt(value, unit=""):
    if value is None:
        return "-"
    return f"{value:g} {unit}".strip() if isinstance(value, float) else f"{value} {unit}".strip()


# --- Operations report ---------------------------------------------------------------------


def build_operations_pdf(restaurant, report, compress=True):
    period = f"{report.start_date:%d %b %Y} – {report.end_date:%d %b %Y} ({report.days} day{'s' if report.days != 1 else ''})"
    story = [
        Paragraph("Operations report", TITLE),
        Paragraph(f"{text(restaurant.name)} · {period}", SMALL),
        Spacer(1, 8),
        Paragraph("Measured = counted or timed from recorded events. Estimate = calculated from measured data "
                  "with the assumption shown.", SMALL),
        Paragraph("Summary", H2),
    ]
    rows = [["Figure", "Value", "Type", "How it is calculated"]]
    for figure in report.figures:
        rows.append([Paragraph(text(figure.label), CELL), fmt(figure.value, figure.unit),
                     "Estimate" if figure.kind == ESTIMATE else "Measured", Paragraph(text(figure.note) or "-", CELL)])
    story.append(grid(rows, [58 * mm, 24 * mm, 20 * mm, 72 * mm]))

    story.append(Paragraph("Waiter task statistics", H2))
    if report.waiters:
        rows = [["Waiter", "Assigned", "Completed", "Avg minutes", "Late"]]
        rows += [[w["name"], w["assigned"], w["completed"], fmt(w["avg_minutes"]), w["late"]] for w in report.waiters]
        story.append(grid(rows, [64 * mm, 26 * mm, 26 * mm, 30 * mm, 28 * mm]))
    else:
        story.append(Paragraph("No tasks completed in this period.", BODY))

    story.append(Paragraph("Daily summary", H2))
    rows = [["Date", "Parties", "Guests", "Tasks done", "Avg cleaning (min)", "Inspections", "Cancelled"]]
    rows += [[f"{d['date']:%a %d %b}", d["seated"], d["guests"], d["tasks"], fmt(d["avg_cleaning"]),
              d["inspections"], d["cancelled"]] for d in report.daily]
    story.append(grid(rows, [30 * mm, 20 * mm, 20 * mm, 22 * mm, 32 * mm, 26 * mm, 24 * mm]))

    story.append(Paragraph("Peak occupancy (estimate: average occupied tables per hour of day)", H2))
    busy = [(hour, value) for hour, value in report.peak_hours if value > 0]
    if busy:
        rows = [["Hour", "Avg occupied tables"]] + [[f"{hour:02d}:00–{hour + 1:02d}:00", f"{value:.2f}"] for hour, value in busy]
        story.append(grid(rows, [50 * mm, 50 * mm]))
    else:
        story.append(Paragraph("No occupancy recorded in this period.", BODY))

    story.append(Paragraph("Inspections by source", H2))
    rows = [["Source", "Inspections"]] + [[label, count] for label, count in report.inspections_by_source]
    story.append(grid(rows, [90 * mm, 40 * mm]) if len(rows) > 1 else Paragraph("No inspections in this period.", BODY))
    return build_pdf(story, "Operations report", compress=compress)


# --- Table history ----------------------------------------------------------------------------------


def build_table_history_pdf(table, days=30, compress=True):
    since = timezone.now() - timedelta(days=days)
    story = [
        Paragraph(f"Table {text(table.label)} history", TITLE),
        Paragraph(f"{text(table.restaurant.name)} · {text(table.section.name)} · {table.capacity} seats · "
                  f"last {days} days", SMALL),
        Paragraph("Inspections", H2),
    ]
    inspections = TableInspection.objects.filter(table=table, created_at__gte=since).select_related("inspector")
    rows = [["When", "Source", "By", "Seated", "Dishes", "Result"]]
    for i in inspections:
        by = (i.inspector.get_full_name() or i.inspector.username) if i.inspector else (i.device_id or "camera")
        result = TableStatus(i.resulting_status).label if i.resulting_status else "-"
        rows.append([local(i.created_at), i.get_source_display(), Paragraph(text(by), CELL),
                     "yes" if i.customers_present else "no", "yes" if i.dishes_present else "no", result])
    story.append(grid(rows, [30 * mm, 34 * mm, 34 * mm, 16 * mm, 16 * mm, 44 * mm]) if len(rows) > 1
                 else Paragraph("No inspections.", BODY))

    story.append(Paragraph("Status changes (audit log)", H2))
    history = table.status_history.filter(created_at__gte=since).select_related("changed_by")
    rows = [["When", "From", "To", "Source", "By", "Note"]]
    for h in history:
        rows.append([local(h.created_at), h.get_from_status_display() or "-", h.get_to_status_display(),
                     h.get_source_display(), (h.changed_by.get_full_name() or h.changed_by.username) if h.changed_by else "-",
                     Paragraph(text(h.note), CELL)])
    story.append(grid(rows, [27 * mm, 28 * mm, 28 * mm, 26 * mm, 25 * mm, 40 * mm]) if len(rows) > 1
                 else Paragraph("No changes.", BODY))

    story.append(Paragraph("Waiter tasks", H2))
    tasks = table.tasks.filter(created_at__gte=since).select_related("assigned_to")
    rows = [["Task", "Type", "Waiter", "Status", "Created", "Minutes"]]
    for t in tasks:
        rows.append([t.task_code, Paragraph(text(t.get_task_type_display()), CELL),
                     (t.assigned_to.get_full_name() or t.assigned_to.username) if t.assigned_to else "-",
                     t.get_status_display(), local(t.created_at), fmt(t.duration_minutes)])
    story.append(grid(rows, [20 * mm, 46 * mm, 34 * mm, 24 * mm, 30 * mm, 20 * mm]) if len(rows) > 1
                 else Paragraph("No tasks.", BODY))

    story.append(Paragraph("Reservations", H2))
    bookings = table.reservations.filter(start_at__gte=since).exclude(status=Reservation.Status.CANCELLED)
    rows = [["Booking", "Guests", "Start", "Status", "Seated", "Completed"]]
    for r in bookings:
        rows.append([r.reference, r.party_size, local(r.start_at), r.get_status_display(), local(r.seated_at), local(r.completed_at)])
    story.append(grid(rows, [22 * mm, 16 * mm, 32 * mm, 24 * mm, 32 * mm, 32 * mm]) if len(rows) > 1
                 else Paragraph("No reservations.", BODY))
    return build_pdf(story, f"Table {table.label} history", compress=compress)
