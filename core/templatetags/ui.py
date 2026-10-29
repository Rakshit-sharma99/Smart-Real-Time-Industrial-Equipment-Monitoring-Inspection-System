"""Small template filters for status badges."""
from django import template

register = template.Library()

# Status value -> colour tone. Status colours are only used for statuses.
STATUS_TONES = {
    # equipment health
    "HEALTHY": "healthy",
    "WARNING": "warning",
    "CRITICAL": "critical",
    "OFFLINE": "offline",
    "INACTIVE": "offline",
    # alerts
    "ACTIVE": "critical",
    "ACKNOWLEDGED": "warning",
    "RESOLVED": "healthy",
    # inspections
    "PASS": "healthy",
    "FAIL": "critical",
    "ATTENTION": "warning",
    "NEEDS_ATTENTION": "warning",
    "NOT_APPLICABLE": "offline",
    "ASSIGNED": "neutral",
    "IN_PROGRESS": "info",
    "SUBMITTED": "healthy",
    # defects and tasks
    "OPEN": "neutral",
    "IN_MAINTENANCE": "info",
    "CLOSED": "healthy",
    "COMPLETED": "healthy",
    # priorities / severities
    "LOW": "neutral",
    "MEDIUM": "warning",
    "HIGH": "critical",
}

STATUS_ICONS = {
    "healthy": "bi-check-circle-fill",
    "warning": "bi-exclamation-triangle-fill",
    "critical": "bi-x-octagon-fill",
    "offline": "bi-dash-circle-fill",
    "info": "bi-arrow-repeat",
    "neutral": "bi-circle",
}


@register.filter
def status_tone(value):
    return STATUS_TONES.get(str(value), "neutral")


@register.filter
def status_icon(value):
    return STATUS_ICONS[status_tone(value)]


@register.filter
def humanize_status(value):
    """'NEEDS_ATTENTION' -> 'Needs attention'."""
    return str(value).replace("_", " ").capitalize()


@register.filter
def lookup(mapping, key):
    """{{ my_dict|lookup:key }} - dictionary access with a variable key."""
    return mapping.get(key, "") if mapping else ""
