from django.contrib import admin

from .models import Camera, TableZone


class ZoneInline(admin.TabularInline):
    model = TableZone
    extra = 0


@admin.register(Camera)
class CameraAdmin(admin.ModelAdmin):
    list_display = ("name", "restaurant", "source_type", "masked_source", "is_active", "status", "last_frame_at", "detector_name")
    list_filter = ("restaurant", "source_type", "status", "is_active")
    search_fields = ("name",)
    readonly_fields = ("status", "last_frame_at", "last_error", "detector_name", "detects_dishes", "frame_width", "frame_height")
    inlines = [ZoneInline]


@admin.register(TableZone)
class TableZoneAdmin(admin.ModelAdmin):
    list_display = ("camera", "table", "updated_at")
    list_filter = ("camera",)
    search_fields = ("table__number",)
