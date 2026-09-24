from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "display_name", "is_staff", "is_superuser", "last_login")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Blog", {"fields": ("display_name",)}),
    )
