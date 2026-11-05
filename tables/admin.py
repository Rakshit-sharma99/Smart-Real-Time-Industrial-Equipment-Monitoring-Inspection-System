from django.contrib import admin

from .models import Restaurant, RestaurantSection, RestaurantTable, TableStatusHistory


class SectionInline(admin.TabularInline):
    model = RestaurantSection
    extra = 0


@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "opens_at", "closes_at", "reservation_minutes", "reservation_hold_minutes")
    search_fields = ("name", "slug", "address")
    list_filter = ()
    prepopulated_fields = {"slug": ("name",)}
    inlines = [SectionInline]


@admin.register(RestaurantSection)
class RestaurantSectionAdmin(admin.ModelAdmin):
    list_display = ("name", "restaurant", "floor", "sort_order")
    list_filter = ("restaurant",)
    search_fields = ("name", "floor")


@admin.register(RestaurantTable)
class RestaurantTableAdmin(admin.ModelAdmin):
    list_display = ("label", "restaurant", "section", "capacity", "is_occupied", "cleanliness", "is_blocked",
                    "is_active", "assigned_waiter", "status_updated_at")
    list_filter = ("restaurant", "section", "cleanliness", "is_occupied", "is_blocked", "is_active")
    search_fields = ("number", "public_id", "block_reason")
    readonly_fields = ("public_id", "status_updated_at", "last_inspected_at", "created_at", "updated_at")


@admin.register(TableStatusHistory)
class TableStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("table", "from_status", "to_status", "source", "changed_by", "created_at")
    list_filter = ("source", "to_status", "table__restaurant")
    search_fields = ("table__number", "note")
    date_hierarchy = "created_at"
    list_select_related = ("table", "changed_by")
