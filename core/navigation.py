"""Sidebar items per role, and which item is highlighted on the current page."""
from dataclasses import dataclass

from accounts.models import Role


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    icon: str  # Bootstrap Icons class
    url_name: str


# Each role gets a short menu that starts with its own floor/home. Setup screens
# (tables, staff, sections, cameras) live under Settings, not in the menu.
NAV_BY_ROLE = {
    Role.MANAGER: [
        NavItem("floor", "Floor", "bi-grid-1x2", "core:manager_dashboard"),
        NavItem("tasks", "Tasks", "bi-list-check", "tasks:list"),
        NavItem("cctv", "Cameras", "bi-camera-video", "cctv:console"),
        NavItem("reports", "Reports", "bi-bar-chart-line", "reports:home"),
        NavItem("settings", "Settings", "bi-gear", "core:settings"),
    ],
    Role.WAITER: [
        NavItem("waiter", "My work", "bi-person-check", "tasks:waiter_home"),
        NavItem("scanner", "Scan QR", "bi-qr-code-scan", "inspections:scanner"),
        NavItem("settings", "Settings", "bi-gear", "core:settings"),
    ],
    Role.RECEPTIONIST: [
        NavItem("floor", "Floor", "bi-grid-1x2", "reservations:desk"),
        NavItem("bookings", "Bookings", "bi-calendar-event", "reservations:list"),
        NavItem("settings", "Settings", "bi-gear", "core:settings"),
    ],
}

# URL namespace -> sidebar key, per role.
SECTION_BY_NAMESPACE = {
    Role.MANAGER: {"core": "floor", "cctv": "cctv", "tables": "floor", "inspections": "floor", "tasks": "tasks",
                   "reservations": "floor", "accounts": "settings", "reports": "reports", "notifications": "floor"},
    Role.WAITER: {"tasks": "waiter", "tables": "waiter", "inspections": "waiter", "notifications": "waiter"},
    Role.RECEPTIONIST: {"reservations": "floor", "tables": "floor", "notifications": "floor"},
}
SETTINGS_VIEWS = {"core:settings", "core:restaurant_settings", "core:sections", "core:section_create", "core:section_edit",
                  "accounts:profile", "accounts:password_change", "cctv:camera_list", "cctv:camera_create",
                  "cctv:camera_edit", "cctv:camera_delete", "tables:manage", "tables:create", "tables:edit",
                  "tables:qr_sheet"}


def nav_items_for(role):
    return NAV_BY_ROLE.get(role, [])


def active_section(request):
    match = getattr(request, "resolver_match", None)
    role = getattr(request, "role", None)
    if match is None or role is None:
        return ""
    if match.view_name in SETTINGS_VIEWS or (role == Role.MANAGER and match.view_name.startswith("accounts:staff")):
        return "settings"
    if match.view_name == "inspections:scanner" and role == Role.WAITER:
        return "scanner"
    if match.view_name.startswith("reservations:") and match.view_name not in (
            "reservations:desk", "reservations:desk_board_fragment", "reservations:upcoming_fragment")             and role == Role.RECEPTIONIST:
        return "bookings"
    return SECTION_BY_NAMESPACE.get(role, {}).get(match.namespace, "")


def nav_badges(request):
    """Small red counts on the menu: things waiting on the manager, open tasks, offline cameras."""
    from cctv.models import Camera
    from tables import services as table_services
    from tasks.models import WaiterTask

    from . import services

    restaurant = request.restaurant
    tables = table_services.with_status(table_services.live_tables(restaurant).filter(is_active=True))
    return {
        "floor": len(services.attention_items(restaurant, tables)),
        "tasks": WaiterTask.objects.for_restaurant(restaurant).active().count(),
        "cctv": sum(1 for c in Camera.objects.filter(restaurant=restaurant, is_active=True) if not c.is_receiving),
    }
