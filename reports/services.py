"""
PDF report builders (ReportLab platypus).

Both reports share one layout: a header with the app name and report title, a
footer with the generation time and "Page X of Y", and tables that repeat their
header row when they split across pages. Authorization happens in the views
BEFORE these functions are called.
"""
import io
import re
from datetime import timedelta
from xml.sax.saxutils import escape

from django.conf import settings
from django.db.models import Avg, Count, Max, Min
from django.utils import timezone
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from equipment.metrics import METRICS
from inspections.models import Inspection, InspectionResponse
from maintenance.models import MaintenanceTask
from monitoring.models import Source

DEFAULT_DAYS = 7
MAX_DAYS = 90

INK = colors.HexColor("#1f2328")
MUTED = colors.HexColor("#6b7280")
BORDER = colors.HexColor("#e5e7eb")
HEADER_BG = colors.HexColor("#f3f4f6")
ACCENT = colors.HexColor("#2563eb")
STATUS_COLORS = {
    "PASS": colors.HexColor("#16a34a"), "HEALTHY": colors.HexColor("#16a34a"), "RESOLVED": colors.HexColor("#16a34a"),
    "FAIL": colors.HexColor("#dc2626"), "CRITICAL": colors.HexColor("#dc2626"), "HIGH": colors.HexColor("#dc2626"),
    "ATTENTION": colors.HexColor("#d97706"), "NEEDS_ATTENTION": colors.HexColor("#d97706"),
    "WARNING": colors.HexColor("#d97706"), "MEDIUM": colors.HexColor("#d97706"),
}

styles = getSampleStyleSheet()
TITLE = ParagraphStyle("Title", parent=styles["Heading1"], fontSize=16, textColor=INK, spaceAfter=2)
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=11.5, textColor=INK, spaceBefore=10, spaceAfter=4)
BODY = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=9, leading=12, textColor=INK)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8, leading=10, textColor=MUTED)
CELL = ParagraphStyle("Cell", parent=BODY, fontSize=8.5, leading=11)
CELL_BOLD = ParagraphStyle("CellBold", parent=CELL, fontName="Helvetica-Bold")
NOTE = ParagraphStyle("Note", parent=BODY, textColor=colors.HexColor("#92400e"), backColor=colors.HexColor("#fffbeb"),
                      borderColor=colors.HexColor("#fde68a"), borderWidth=0.5, borderPadding=5, spaceBefore=4, spaceAfter=8)


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
    """inspection_EQ-001_2026-10-29.pdf - only letters, digits, dot, dash and underscore."""
    name = "_".join(str(part) for part in parts)
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") + ".pdf"


def text(value):
    """Escape user-entered text for a ReportLab Paragraph and keep line breaks."""
    return escape(str(value or "")).replace("\n", "<br/>")


def local(dt, fmt="%d %b %Y, %H:%M"):
    return timezone.localtime(dt).strftime(fmt) if dt else "-"


def status_text(value, label):
    color = STATUS_COLORS.get(value)
    if color is None:
        return Paragraph(text(label), CELL)
    return Paragraph(f'<font color="{color.hexval()}"><b>{text(label)}</b></font>', CELL)


def table(rows, widths, header=True, zebra=True):
    """A styled table. Long tables split across pages and repeat the header row."""
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
    if zebra:
        for row in range(2 if header else 1, len(rows), 2):
            style.append(("BACKGROUND", (0, row), (-1, row), colors.HexColor("#fafbfc")))
    result.setStyle(TableStyle(style))
    return result


def key_values(pairs, widths=(38 * mm, 52 * mm)):
    """Two-column label/value table (no header)."""
    rows = [[Paragraph(f"<b>{text(k)}</b>", CELL), v if not isinstance(v, str) else Paragraph(text(v), CELL)] for k, v in pairs]
    result = Table(rows, colWidths=widths)
    result.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    return result


def build_pdf(story, title, compress=True):
    """Render a story into PDF bytes with the shared header and footer."""
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

    doc = SimpleDocTemplate(
        buffer, pagesize=A4, title=title, author=settings.APP_NAME, pageCompression=1 if compress else 0,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=24 * mm, bottomMargin=22 * mm,
    )
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate, canvasmaker=NumberedCanvas)
    return buffer.getvalue()


def photo_cell(response, max_width=24 * mm, max_height=18 * mm):
    """A thumbnail, a placeholder when the file is gone, or a dash when there is no photo."""
    if not response.photo:
        return Paragraph("-", SMALL)
    try:
        path = response.photo.path
        with PILImage.open(path) as img:
            width, height = img.size
        scale = min(max_width / width, max_height / height)
        return Image(path, width=width * scale, height=height * scale)
    except (FileNotFoundError, OSError, ValueError):
        return Paragraph("[photo missing]", SMALL)


# --- Inspection report ---------------------------------------------------------------


def build_inspection_report(inspection, compress=True):
    equipment = inspection.equipment
    responses = list(inspection.responses.all())
    counts = inspection.outcome_counts()
    story = [
        Paragraph("Inspection report", TITLE),
        Paragraph(f"Report ID {inspection.reference} · {text(inspection.template.name)}", SMALL),
        Spacer(1, 6),
    ]
    left = key_values([
        ("Equipment", f"{equipment.equipment_code} · {equipment.name}"),
        ("Location", equipment.location),
        ("Type", equipment.get_equipment_type_display()),
        ("Inspector", inspection.inspector.get_full_name() or inspection.inspector.username),
    ])
    right = key_values([
        ("Status", inspection.get_status_display()),
        ("Overall result", status_text(inspection.overall_result, inspection.get_overall_result_display() or "Not submitted")),
        ("Started", local(inspection.started_at)),
        ("Submitted", local(inspection.submitted_at)),
    ], widths=(30 * mm, 54 * mm))
    story.append(Table([[left, right]], colWidths=[90 * mm, 84 * mm], style=[("VALIGN", (0, 0), (-1, -1), "TOP")]))

    story.append(Paragraph("Result counts", H2))
    Outcome = InspectionResponse.Outcome
    story.append(table(
        [["Pass", "Fail", "Needs attention", "N/A", "Items"],
         [counts[Outcome.PASS], counts[Outcome.FAIL], counts[Outcome.NEEDS_ATTENTION], counts[Outcome.NOT_APPLICABLE], len(responses)]],
        [34.8 * mm] * 5, zebra=False,
    ))

    story.append(Paragraph("Checklist", H2))
    rows = [["#", "Item", "Result", "Remarks", "Photo"]]
    for number, response in enumerate(responses, start=1):
        rows.append([
            str(number),
            Paragraph(text(response.item_text), CELL),
            status_text(response.outcome, response.get_outcome_display() or "Not answered"),
            Paragraph(text(response.remarks) or "-", CELL),
            photo_cell(response),
        ])
    if len(rows) == 1:
        rows.append(["", Paragraph("The checklist is copied when the inspection is started.", SMALL), "", "", ""])
    story.append(table(rows, [8 * mm, 62 * mm, 24 * mm, 50 * mm, 30 * mm]))

    if inspection.remarks:
        story += [Paragraph("Overall remarks", H2), Paragraph(text(inspection.remarks), BODY)]

    defects = list(inspection.defects.all())
    story.append(Paragraph("Defects raised", H2))
    if defects:
        story.append(table(
            [["Defect", "Description", "Severity", "Status"]]
            + [[d.reference, Paragraph(text(d.description), CELL), status_text(d.severity, d.get_severity_display()),
                d.get_status_display()] for d in defects],
            [22 * mm, 102 * mm, 24 * mm, 26 * mm],
        ))
    else:
        story.append(Paragraph("None.", BODY))

    story.append(Spacer(1, 10))
    story.append(KeepTogether([
        Paragraph(f"Overall result: <b>{text(inspection.get_overall_result_display() or 'Not submitted')}</b>", BODY),
        Paragraph(f"Generated {timezone.localtime().strftime('%d %b %Y, %H:%M:%S')} by {settings.APP_NAME}.", SMALL),
    ]))
    return build_pdf(story, f"Inspection report {inspection.reference}", compress=compress)


# --- Equipment health report -------------------------------------------------------------


def clamp_days(value):
    """Report period in days: default 7, between 1 and 90."""
    try:
        days = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return max(1, min(days, MAX_DAYS))


def build_health_report(equipment, user, days=DEFAULT_DAYS, compress=True):
    days = clamp_days(days)
    end = timezone.now()
    start = end - timedelta(days=days)
    readings = equipment.readings.filter(recorded_at__gte=start, recorded_at__lte=end)
    # min / max / average per metric, computed by the database in one query
    aggregate_args = {"count": Count("id")}
    for metric in METRICS:
        aggregate_args[f"{metric.key}_min"] = Min(metric.reading_field)
        aggregate_args[f"{metric.key}_max"] = Max(metric.reading_field)
        aggregate_args[f"{metric.key}_avg"] = Avg(metric.reading_field)
    aggregates = readings.aggregate(**aggregate_args)
    simulated = readings.filter(source=Source.SIMULATED).exists()

    story = [
        Paragraph("Equipment health report", TITLE),
        Paragraph(f"{text(equipment.equipment_code)} · {text(equipment.name)} · period {local(start, '%d %b %Y')} "
                  f"to {local(end, '%d %b %Y')} ({days} day{'s' if days != 1 else ''})", SMALL),
        Spacer(1, 6),
    ]
    if simulated:
        story.append(Paragraph("Contains SIMULATED sensor data. Values come from the built-in simulator, not real sensors.", NOTE))
    story.append(key_values([
        ("Equipment", f"{equipment.equipment_code} · {equipment.name}"),
        ("Type", equipment.get_equipment_type_display()),
        ("Location", equipment.location),
        ("Installed", equipment.installed_on.strftime("%d %b %Y") if equipment.installed_on else "-"),
        ("Current status", status_text(equipment.display_status, equipment.display_status_label)),
        ("Last reading", local(equipment.last_reading_at)),
    ], widths=(38 * mm, 136 * mm)))

    story.append(Paragraph("Readings in the period", H2))
    rows = [["Metric", "Min", "Max", "Average", "Warning at", "Critical at"]]
    for metric, warn, crit in equipment.threshold_rows():
        values = [aggregates[f"{metric.key}_{name}"] for name in ("min", "max", "avg")]
        rows.append([f"{metric.label} ({metric.unit})"] + ["-" if v is None else f"{v:.2f}" for v in values] + [str(warn), str(crit)])
    story.append(table(rows, [44 * mm, 24 * mm, 24 * mm, 26 * mm, 28 * mm, 28 * mm]))
    story.append(Paragraph(f"{aggregates['count']} readings recorded in this period.", SMALL))

    alerts = list(equipment.alerts.filter(created_at__gte=start).order_by("-created_at"))
    story.append(Paragraph(f"Alert history ({len(alerts)})", H2))
    if alerts:
        story.append(table(
            [["Alert", "Metric", "Severity", "Status", "Peak / limit", "Raised", "Resolved"]]
            + [[a.reference, a.metric_info.label, status_text(a.severity, a.get_severity_display()),
                status_text(a.status, a.get_status_display()), f"{a.peak_value} / {a.threshold_value}",
                local(a.created_at, "%d %b %H:%M"), local(a.resolved_at, "%d %b %H:%M")] for a in alerts],
            [20 * mm, 22 * mm, 20 * mm, 26 * mm, 26 * mm, 30 * mm, 30 * mm],
        ))
    else:
        story.append(Paragraph("No alerts in this period.", BODY))

    inspections = list(
        Inspection.objects.visible_to(user).filter(equipment=equipment, created_at__gte=start).select_related("inspector")
    )
    story.append(Paragraph(f"Inspections ({len(inspections)})", H2))
    if inspections:
        story.append(table(
            [["Inspection", "Inspector", "Status", "Result", "Submitted"]]
            + [[i.reference, i.inspector.get_full_name() or i.inspector.username, i.get_status_display(),
                status_text(i.overall_result, i.get_overall_result_display() or "-"), local(i.submitted_at)] for i in inspections],
            [26 * mm, 44 * mm, 30 * mm, 34 * mm, 40 * mm],
        ))
    else:
        story.append(Paragraph("No inspections in this period.", BODY))

    tasks = list(MaintenanceTask.objects.visible_to(user).open().filter(equipment=equipment).select_related("assigned_to"))
    story.append(Paragraph(f"Open maintenance tasks ({len(tasks)})", H2))
    if tasks:
        story.append(table(
            [["Task", "Title", "Priority", "Status", "Assigned to", "Due"]]
            + [[t.task_code, Paragraph(text(t.title), CELL), status_text(t.priority, t.get_priority_display()),
                t.get_status_display(), (t.assigned_to.get_full_name() or t.assigned_to.username) if t.assigned_to else "-",
                t.due_date.strftime("%d %b %Y") if t.due_date else "-"] for t in tasks],
            [20 * mm, 60 * mm, 20 * mm, 24 * mm, 30 * mm, 20 * mm],
        ))
    else:
        story.append(Paragraph("No open tasks.", BODY))

    story.append(Spacer(1, 8))
    story.append(Paragraph(f"Generated {timezone.localtime().strftime('%d %b %Y, %H:%M:%S')} by {settings.APP_NAME}.", SMALL))
    return build_pdf(story, f"Health report {equipment.equipment_code}", compress=compress)

