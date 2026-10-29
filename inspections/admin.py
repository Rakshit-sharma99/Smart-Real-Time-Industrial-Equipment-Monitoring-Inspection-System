from django.contrib import admin

from .models import ChecklistItem, Defect, Inspection, InspectionResponse, InspectionTemplate


class ChecklistItemInline(admin.TabularInline):
    model = ChecklistItem
    extra = 1


@admin.register(InspectionTemplate)
class InspectionTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "equipment_type", "is_active", "created_at")
    list_filter = ("is_active", "equipment_type")
    search_fields = ("name", "description")
    inlines = [ChecklistItemInline]


@admin.register(ChecklistItem)
class ChecklistItemAdmin(admin.ModelAdmin):
    list_display = ("template", "order", "text")
    list_filter = ("template",)
    search_fields = ("text",)


class InspectionResponseInline(admin.TabularInline):
    model = InspectionResponse
    extra = 0
    fields = ("order", "item_text", "outcome", "remarks", "photo")


@admin.register(Inspection)
class InspectionAdmin(admin.ModelAdmin):
    list_display = ("id", "equipment", "inspector", "status", "overall_result", "due_date", "submitted_at")
    list_filter = ("status", "overall_result", "template")
    search_fields = ("equipment__equipment_code", "equipment__name", "inspector__username", "remarks")
    date_hierarchy = "created_at"
    raw_id_fields = ("inspector", "created_by")
    inlines = [InspectionResponseInline]


@admin.register(InspectionResponse)
class InspectionResponseAdmin(admin.ModelAdmin):
    list_display = ("inspection", "order", "item_text", "outcome")
    list_filter = ("outcome",)
    search_fields = ("item_text", "remarks")


@admin.register(Defect)
class DefectAdmin(admin.ModelAdmin):
    list_display = ("id", "equipment", "severity", "status", "inspection", "created_at")
    list_filter = ("status", "severity")
    search_fields = ("equipment__equipment_code", "description")
