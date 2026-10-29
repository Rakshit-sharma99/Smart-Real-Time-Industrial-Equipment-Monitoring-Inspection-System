from django.contrib import admin

from .models import Equipment


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = (
        "equipment_code", "name", "equipment_type", "location",
        "health_status", "is_active", "sim_mode", "last_reading_at",
    )
    list_filter = ("equipment_type", "health_status", "is_active", "sim_mode", "location")
    search_fields = ("equipment_code", "name", "location", "public_id")
    readonly_fields = ("public_id", "health_status", "last_reading_at", "created_at", "updated_at")
