from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User

EXTRA = ('Extra Info', {'fields': ('phone', 'gender', 'date_of_birth')})


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (EXTRA,)
    add_fieldsets = UserAdmin.add_fieldsets + (EXTRA,)
    list_display = ('username', 'email', 'first_name', 'last_name', 'phone', 'is_staff', 'is_active')