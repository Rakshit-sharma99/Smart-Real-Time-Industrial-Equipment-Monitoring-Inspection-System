"""Template filters for status badges and table tiles."""
from django import template

register = template.Library()

# Status value -> colour tone (see .tone-* in app.css). Table-status colours
# are reserved for table statuses; other workflows use the neutral tones.
STATUS_TONES = {
    # table status: four colours only (green free, blue seated, orange needs cleaning, grey unavailable)
    "READY": "ready",
    "OCCUPIED": "occupied",
    "RESERVED": "blocked",
    "VACANT_NEEDS_CLEARING": "clearing",
    "CLEANING_REQUIRED": "clearing",
    "CLEANING_IN_PROGRESS": "clearing",
    "BLOCKED": "blocked",
    "INACTIVE": "blocked",
    # display states
    "free": "ready",
    "seated": "occupied",
    "dirty": "clearing",
    "unavailable": "blocked",
    # waiter tasks
    "OPEN": "neutral",
    "ASSIGNED": "info",
    "IN_PROGRESS": "progress",
    "COMPLETED": "success",
    # priorities
    "LOW": "neutral",
    "NORMAL": "neutral",
    "HIGH": "warning",
    "URGENT": "danger",
    # reservations
    "PENDING": "neutral",
    "CONFIRMED": "reserved",
    "ARRIVED": "info",
    "SEATED": "occupied",
    "CANCELLED": "blocked",
    "NO_SHOW": "danger",
}

TONE_ICONS = {
    "ready": "bi-check-circle-fill",
    "occupied": "bi-people-fill",
    "reserved": "bi-bookmark-fill",
    "clearing": "bi-cup-straw",
    "dirty": "bi-exclamation-circle-fill",
    "progress": "bi-arrow-repeat",
    "blocked": "bi-slash-circle",
    "success": "bi-check-circle-fill",
    "info": "bi-person-check",
    "warning": "bi-exclamation-triangle-fill",
    "danger": "bi-x-octagon-fill",
    "neutral": "bi-circle",
}


@register.filter
def status_tone(value):
    return STATUS_TONES.get(str(value), "neutral")


@register.filter
def status_icon(value):
    return TONE_ICONS[status_tone(value)]


@register.filter
def humanize_status(value):
    """'VACANT_NEEDS_CLEARING' -> 'Vacant needs clearing'."""
    return str(value).replace("_", " ").capitalize()


@register.filter
def lookup(mapping, key):
    """{{ my_dict|lookup:key }} - dictionary access with a variable key."""
    return mapping.get(key, "") if mapping else ""


@register.filter
def person(user, fallback="—"):
    """Display name of a user that may be None: {{ task.assigned_to|person:"Unassigned" }}."""
    if user is None:
        return fallback
    return user.get_full_name() or user.username


@register.filter
def person_short(user, fallback="—"):
    """First name (or username) of a user that may be None."""
    if user is None:
        return fallback
    return user.first_name or user.username


def format_minutes(minutes):
    """42 -> '42 min', 125 -> '2 h 5 min', 3000 -> '2 days'. Mirrors RS.formatMinutes in app.js."""
    minutes = max(0, int(minutes))
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 24 * 60:
        hours, rest = divmod(minutes, 60)
        return f"{hours} h {rest} min" if rest else f"{hours} h"
    days = minutes // (24 * 60)
    return f"{days} day{'s' if days != 1 else ''}"


@register.filter
def minutes_label(value):
    return format_minutes(value or 0)


@register.filter
def ago_label(value):
    """A datetime -> '8 days', '3 h 5 min', '42 min' (time elapsed since, without 'ago')."""
    if not value:
        return ""
    from django.utils import timezone
    return format_minutes((timezone.now() - value).total_seconds() // 60)


@register.filter
def due_label(task):
    """'Due 14:30', 'Due Tue 18 Nov 14:30' or 'Overdue by 8 days' - deadlines always carry their day."""
    from django.utils import timezone
    due = getattr(task, "due_at", None)
    if not due:
        return ""
    now = timezone.now()
    if getattr(task, "status", None) != "COMPLETED" and due < now:
        return f"Overdue by {format_minutes((now - due).total_seconds() // 60)}"
    local_due, today = timezone.localtime(due), timezone.localdate()
    if local_due.date() == today:
        return f"Due {clock(due)}"
    return f"Due {local_due:%a} {local_due.day} {local_due:%b}, {clock(due)}"


def clock(value):
    """A datetime -> '8:30 PM' in local time."""
    from django.utils import timezone
    return timezone.localtime(value).strftime("%I:%M %p").lstrip("0")


@register.filter(name="clock")
def clock_filter(value):
    return clock(value) if value else ""


@register.filter
def when(value):
    """A datetime -> '9 Nov, 11:44 PM' (or 'Today, 11:44 PM'). Used instead of '1 week, 1 day ago'."""
    if not value:
        return ""
    from django.utils import timezone
    local = timezone.localtime(value)
    day = "Today" if local.date() == timezone.localdate() else f"{local.day} {local:%b}"
    return f"{day}, {clock(value)}"


@register.filter
def timer_text(timer):
    """'Seated 42 min' / 'Dirty for 6 min' - the main line of a table card."""
    if not timer:
        return ""
    length = format_minutes(timer["minutes"])
    return f"Seated · {length}" if timer["kind"] == "seated" else f"Dirty · {length}"
