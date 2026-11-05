from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

from .models import StaffProfile


class StaffProfileInline(admin.StackedInline):
    model = StaffProfile
    can_delete = False


class UserWithProfileAdmin(UserAdmin):
    inlines = [StaffProfileInline]
    list_display = ("username", "first_name", "last_name", "role", "restaurant", "is_active", "is_superuser")

    @admin.display(description="Role")
    def role(self, user):
        return user.profile.get_role_display() if hasattr(user, "profile") else "-"

    @admin.display(description="Restaurant")
    def restaurant(self, user):
        return getattr(getattr(user, "profile", None), "restaurant", None) or "-"


admin.site.unregister(User)
admin.site.register(User, UserWithProfileAdmin)


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "restaurant", "phone")
    list_filter = ("role", "restaurant")
    search_fields = ("user__username", "user__first_name", "user__last_name", "phone")
