from django.contrib import admin

from .models import Reservation, ReservationHistory


class HistoryInline(admin.TabularInline):
    model = ReservationHistory
    extra = 0
    readonly_fields = ("from_status", "to_status", "changed_by", "note", "created_at")
    can_delete = False


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ("reference", "customer_name", "party_size", "table", "start_at", "end_at", "status", "source")
    list_filter = ("status", "source", "restaurant")
    search_fields = ("reference", "customer_name", "phone")
    date_hierarchy = "start_at"
    readonly_fields = ("reference",)
    inlines = [HistoryInline]


@admin.register(ReservationHistory)
class ReservationHistoryAdmin(admin.ModelAdmin):
    list_display = ("reservation", "from_status", "to_status", "changed_by", "created_at")
    list_filter = ("to_status",)
    search_fields = ("reservation__reference", "note")
