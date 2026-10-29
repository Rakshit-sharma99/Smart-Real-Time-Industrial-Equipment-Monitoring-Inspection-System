from django.contrib import admin

from .models import MaintenanceTask, TaskStatusChange


class TaskStatusChangeInline(admin.TabularInline):
    model = TaskStatusChange
    extra = 0
    readonly_fields = ("from_status", "to_status", "changed_by", "note", "changed_at")
    can_delete = False


@admin.register(MaintenanceTask)
class MaintenanceTaskAdmin(admin.ModelAdmin):
    list_display = ("task_code", "title", "equipment", "priority", "status", "assigned_to", "due_date", "completed_at")
    list_filter = ("status", "priority", "equipment")
    search_fields = ("task_code", "title", "description", "equipment__equipment_code")
    raw_id_fields = ("alert", "defect", "assigned_to", "created_by")
    readonly_fields = ("task_code",)
    inlines = [TaskStatusChangeInline]


@admin.register(TaskStatusChange)
class TaskStatusChangeAdmin(admin.ModelAdmin):
    list_display = ("task", "from_status", "to_status", "changed_by", "changed_at")
    list_filter = ("to_status",)
    search_fields = ("task__task_code", "note")
