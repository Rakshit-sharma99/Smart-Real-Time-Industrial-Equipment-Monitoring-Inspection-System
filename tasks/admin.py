from django.contrib import admin

from .models import WaiterTask, WaiterTaskHistory


class HistoryInline(admin.TabularInline):
    model = WaiterTaskHistory
    extra = 0
    readonly_fields = ("action", "from_status", "to_status", "changed_by", "note", "created_at")
    can_delete = False


@admin.register(WaiterTask)
class WaiterTaskAdmin(admin.ModelAdmin):
    list_display = ("task_code", "table", "task_type", "priority", "status", "assigned_to", "due_at", "completed_at")
    list_filter = ("status", "priority", "task_type", "restaurant")
    search_fields = ("task_code", "description", "table__number", "assigned_to__username")
    date_hierarchy = "created_at"
    raw_id_fields = ("assigned_to", "created_by")
    readonly_fields = ("task_code",)
    inlines = [HistoryInline]


@admin.register(WaiterTaskHistory)
class WaiterTaskHistoryAdmin(admin.ModelAdmin):
    list_display = ("task", "action", "from_status", "to_status", "changed_by", "created_at")
    list_filter = ("action",)
    search_fields = ("task__task_code", "note")
