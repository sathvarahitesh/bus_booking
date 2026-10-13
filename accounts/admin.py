from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import EmailOTP, User

EXTRA = ('Extra Info', {'fields': ('phone', 'gender', 'date_of_birth', 'email_verified')})


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (EXTRA,)
    add_fieldsets = UserAdmin.add_fieldsets + (EXTRA,)
    list_display = ('username', 'email', 'first_name', 'last_name', 'phone',
                    'email_verified', 'is_staff', 'is_active')
    list_filter = UserAdmin.list_filter + ('email_verified',)


@admin.register(EmailOTP)
class EmailOTPAdmin(admin.ModelAdmin):
    list_display = ('user', 'purpose', 'created_at', 'expires_at', 'attempts', 'used_at')
    list_filter = ('purpose',)
    search_fields = ('user__email',)
    exclude = ('code_hash',)
    readonly_fields = ('user', 'purpose', 'created_at', 'expires_at', 'attempts', 'used_at')

    def has_add_permission(self, request):
        return False