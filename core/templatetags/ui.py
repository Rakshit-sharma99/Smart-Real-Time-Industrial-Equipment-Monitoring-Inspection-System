"""Template filters for status badges and table tiles."""
from django import template

register = template.Library()

# Status value -> colour tone (see .tone-* in app.css). Table-status colours
# are reserved for table statuses; other workflows use the neutral tones.
STATUS_TONES = {
    # table status
    "READY": "ready",
    "OCCUPIED": "occupied",
    "RESERVED": "reserved",
    "VACANT_NEEDS_CLEARING": "clearing",
    "CLEANING_REQUIRED": "dirty",
    "CLEANING_IN_PROGRESS": "progress",
    "BLOCKED": "blocked",
    "INACTIVE": "blocked",
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
