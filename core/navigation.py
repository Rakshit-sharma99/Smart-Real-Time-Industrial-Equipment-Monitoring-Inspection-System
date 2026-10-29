"""Sidebar items and the logic that decides which one is highlighted."""
from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    icon: str  # Bootstrap Icons class
    url_name: str
    admin_only: bool = False
    badge_kpi: str = ""  # optional live count shown next to the label (a summary key)


# Sidebar order from the spec. Both roles can use every section; what they can
# do inside a section is limited by the views themselves.
NAV_ITEMS = [
    NavItem("overview", "Overview", "bi-grid-1x2", "core:dashboard"),
    NavItem("equipment", "Equipment", "bi-hdd-stack", "equipment:list"),
    NavItem("live", "Live Monitoring", "bi-broadcast", "monitoring:live"),
    NavItem("alerts", "Alerts", "bi-bell", "alerts:list", badge_kpi="active_alerts"),
    NavItem("inspections", "Inspections", "bi-clipboard-check", "inspections:list"),
    NavItem("scanner", "QR Scanner", "bi-qr-code-scan", "inspections:scanner"),
    NavItem("settings", "Settings", "bi-gear", "core:settings"),
]

# URL namespace -> sidebar key.
SECTION_BY_NAMESPACE = {
    "core": "overview",
    "accounts": "settings",
    "equipment": "equipment",
    "monitoring": "live",
    "alerts": "alerts",
    "inspections": "inspections",
    "maintenance": "maintenance",
    "reports": "reports",
}

# Individual views that live in one app but belong to another sidebar section.
SECTION_BY_VIEW = {
    "core:settings": "settings",
    "monitoring:simulator_settings": "settings",
    "monitoring:set_sim_mode": "settings",
    "inspections:scanner": "scanner",
}


def active_section(request):
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    if match.view_name.startswith("inspections:template"):
        return "settings"
    return SECTION_BY_VIEW.get(match.view_name) or SECTION_BY_NAMESPACE.get(match.namespace, "")


def nav_items_for(role_is_admin):
    return [item for item in NAV_ITEMS if role_is_admin or not item.admin_only]
