"""Sidebar items per role, and which item is highlighted on the current page."""
from dataclasses import dataclass

from accounts.models import Role


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    icon: str  # Bootstrap Icons class
    url_name: str


# Each role gets a short menu that starts with its own dashboard. Everything else
# (table checks, QR codes, reports, notifications) is reached from inside these pages.
NAV_BY_ROLE = {
    Role.MANAGER: [
        NavItem("overview", "Overview", "bi-speedometer2", "core:manager_dashboard"),
        NavItem("cctv", "Live CCTV", "bi-camera-video", "cctv:console"),
        NavItem("tables", "Tables", "bi-grid-3x3-gap", "tables:manage"),
        NavItem("tasks", "Waiter Tasks", "bi-list-check", "tasks:list"),
        NavItem("reservations", "Reservations", "bi-calendar-event", "reservations:desk"),
        NavItem("staff", "Staff", "bi-people", "accounts:staff_list"),
        NavItem("settings", "Settings", "bi-gear", "core:settings"),
    ],
    Role.WAITER: [
        NavItem("waiter", "My tasks", "bi-list-check", "tasks:waiter_home"),
        NavItem("scanner", "Scan table QR", "bi-qr-code-scan", "inspections:scanner"),
        NavItem("settings", "Settings", "bi-gear", "core:settings"),
    ],
    Role.RECEPTIONIST: [
        NavItem("reservations", "Booking desk", "bi-journal-bookmark", "reservations:desk"),
        NavItem("bookings", "Reservations", "bi-calendar-event", "reservations:list"),
        NavItem("settings", "Settings", "bi-gear", "core:settings"),
    ],
}

# URL namespace -> sidebar key, per role.
SECTION_BY_NAMESPACE = {
    Role.MANAGER: {"core": "overview", "cctv": "cctv", "tables": "tables", "inspections": "tables", "tasks": "tasks",
                   "reservations": "reservations", "accounts": "staff", "reports": "overview", "notifications": "overview"},
    Role.WAITER: {"tasks": "waiter", "tables": "waiter", "inspections": "waiter", "notifications": "waiter"},
    Role.RECEPTIONIST: {"reservations": "reservations", "tables": "reservations", "notifications": "reservations"},
}
SETTINGS_VIEWS = {"core:settings", "core:restaurant_settings", "core:sections", "core:section_create", "core:section_edit",
                  "accounts:profile", "accounts:password_change", "cctv:camera_list", "cctv:camera_create",
                  "cctv:camera_edit", "cctv:camera_delete"}


def nav_items_for(role):
    return NAV_BY_ROLE.get(role, [])


def active_section(request):
    match = getattr(request, "resolver_match", None)
    role = getattr(request, "role", None)
    if match is None or role is None:
        return ""
    if match.view_name in SETTINGS_VIEWS:
        return "settings"
    if match.view_name == "inspections:scanner" and role == Role.WAITER:
        return "scanner"
    if match.view_name == "reservations:list" and role == Role.RECEPTIONIST:
        return "bookings"
    return SECTION_BY_NAMESPACE.get(role, {}).get(match.namespace, "")
