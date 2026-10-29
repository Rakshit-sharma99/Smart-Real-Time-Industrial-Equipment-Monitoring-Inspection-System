from django.contrib import admin

from .models import Alert, SensorReading, SimulatorSettings


@admin.register(SensorReading)
class SensorReadingAdmin(admin.ModelAdmin):
    list_display = ("equipment", "recorded_at", "temperature_c", "vibration_mm_s", "pressure_bar", "source")
    list_filter = ("source", "equipment")
    search_fields = ("equipment__equipment_code", "equipment__name")
    date_hierarchy = "recorded_at"
    list_select_related = ("equipment",)


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = (
        "id", "equipment", "metric", "severity", "status", "peak_value",
        "threshold_value", "created_at", "resolved_at",
    )
    list_filter = ("status", "severity", "metric", "equipment")
    search_fields = ("equipment__equipment_code", "equipment__name", "description", "resolution_notes")
    date_hierarchy = "created_at"
    list_select_related = ("equipment",)
    raw_id_fields = ("acknowledged_by", "resolved_by")


@admin.register(SimulatorSettings)
class SimulatorSettingsAdmin(admin.ModelAdmin):
    list_display = ("__str__", "interval_seconds", "noise_pct", "last_ingest_at")
    search_fields = ()
    list_filter = ()

    def has_add_permission(self, request):
        return not SimulatorSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
