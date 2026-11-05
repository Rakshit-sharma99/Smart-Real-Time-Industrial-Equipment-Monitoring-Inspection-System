"""Sidebar items per role, and which item is highlighted on the current page."""
from dataclasses import dataclass

from accounts.models import Role

ALL = (Role.MANAGER, Role.WAITER, Role.RECEPTIONIST)


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    icon: str  # Bootstrap Icons class
    url_name: str
    roles: tuple


# Each role gets its own short menu, starting with its own dashboard.
NAV_ITEMS = [
    NavItem("manager", "Dashboard", "bi-speedometer2", "core:manager_dashboard", (Role.MANAGER,)),
    NavItem("waiter", "My tasks", "bi-list-check", "tasks:waiter_home", (Role.WAITER,)),
    NavItem("desk", "Booking desk", "bi-journal-bookmark", "reservations:desk", (Role.RECEPTIONIST, Role.MANAGER)),
    NavItem("floor", "Floor", "bi-grid-3x3-gap", "tables:floor", ALL),
    NavItem("tasks", "Waiter tasks", "bi-clipboard-check", "tasks:list", (Role.MANAGER,)),
    NavItem("reservations", "Reservations", "bi-calendar-event", "reservations:list", (Role.MANAGER, Role.RECEPTIONIST)),
    NavItem("inspections", "Inspections", "bi-clipboard2-pulse", "inspections:list", (Role.MANAGER, Role.WAITER)),
    NavItem("scanner", "Scan table QR", "bi-qr-code-scan", "inspections:scanner", (Role.MANAGER, Role.WAITER)),
    NavItem("reports", "Reports", "bi-bar-chart-line", "reports:home", (Role.MANAGER,)),
    NavItem("tables", "Tables & QR codes", "bi-table", "tables:manage", (Role.MANAGER,)),
    NavItem("staff", "Staff", "bi-people", "accounts:staff_list", (Role.MANAGER,)),
    NavItem("notifications", "Notifications", "bi-bell", "notifications:list", ALL),
    NavItem("settings", "Settings", "bi-gear", "core:settings", ALL),
]

# Exact view names that belong to a different item than their app suggests.
SECTION_BY_VIEW = {
    "core:manager_dashboard": "manager",
    "tasks:waiter_home": "waiter",
    "reservations:desk": "desk",
    "inspections:scanner": "scanner",
    "tables:floor": "floor",
    "tables:detail": "floor",
    "accounts:profile": "settings",
    "accounts:password_change": "settings",
}
SECTION_BY_NAMESPACE = {
    "core": "settings",
    "accounts": "staff",
    "tables": "tables",
    "tasks": "tasks",
    "reservations": "reservations",
    "inspections": "inspections",
    "reports": "reports",
    "notifications": "notifications",
}


def nav_items_for(role):
    return [item for item in NAV_ITEMS if role in item.roles]


def active_section(request):
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    if match.view_name.startswith("tasks:") and getattr(request, "role", None) == Role.WAITER:
        return "waiter"  # waiters reach task pages from "My tasks"
    return SECTION_BY_VIEW.get(match.view_name) or SECTION_BY_NAMESPACE.get(match.namespace, "")
