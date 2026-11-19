from django.contrib import admin

from .models import TableInspection, TableInspectionPhoto


class PhotoInline(admin.TabularInline):
    model = TableInspectionPhoto
    extra = 0


@admin.register(TableInspection)
class TableInspectionAdmin(admin.ModelAdmin):
    list_display = ("reference", "table", "source", "inspector", "customers_present", "resulting_status",
                    "applied", "created_at")
    list_filter = ("source", "resulting_status", "applied", "table__restaurant")
    search_fields = ("table__number", "remarks", "device_id", "inspector__username")
    date_hierarchy = "created_at"
    list_select_related = ("table", "inspector")
    inlines = [PhotoInline]


@admin.register(TableInspectionPhoto)
class TableInspectionPhotoAdmin(admin.ModelAdmin):
    list_display = ("pk", "inspection", "created_at")
    search_fields = ("inspection__table__number",)
    list_filter = ()
