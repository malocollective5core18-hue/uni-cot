from django.contrib import admin
from .models import CountdownCard, ExternalTable, ExternalTableRecord, ImagePost, Property, User, UserGroup, SystemSetting, RegistrationFormField

# Register your models here.
@admin.register(ImagePost)
class ImagePostAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'status', 'display_order', 'created_at')
    list_filter = ('category', 'status')
    search_fields = ('title', 'description')
    ordering = ('display_order', '-created_at')

@admin.register(CountdownCard)
class CountdownCardAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'is_published', 'start_time', 'end_time', 'created_at')
    list_filter = ('status', 'is_published')
    search_fields = ('title', 'description')
    ordering = ('-created_at',)

@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ('item_name', 'category', 'status', 'location', 'created_at')
    list_filter = ('category', 'status')
    search_fields = ('item_name', 'description')
    ordering = ('-created_at',)

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'registration_number', 'email', 'status', 'role', 'is_verified', 'created_at')
    list_filter = ('status', 'role', 'is_verified')
    search_fields = ('full_name', 'registration_number', 'email')
    ordering = ('-created_at',)

@admin.register(UserGroup)
class UserGroupAdmin(admin.ModelAdmin):
    list_display = ('group_name', 'group_code', 'current_members', 'max_members', 'is_active', 'is_flagged', 'created_at')
    list_filter = ('is_active', 'is_flagged')
    search_fields = ('group_name', 'group_code')
    ordering = ('group_name',)

@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ('setting_key', 'setting_value', 'setting_type', 'updated_at')
    list_filter = ('setting_type',)
    search_fields = ('setting_key', 'description')
    ordering = ('setting_key',)

@admin.register(RegistrationFormField)
class RegistrationFormFieldAdmin(admin.ModelAdmin):
    list_display = ('field_label', 'field_key', 'field_type', 'is_required', 'display_order', 'is_active')
    list_filter = ('field_type', 'is_required', 'is_active')
    search_fields = ('field_name', 'field_label', 'field_key')
    ordering = ('display_order', 'id')


@admin.register(ExternalTable)
class ExternalTableAdmin(admin.ModelAdmin):
    list_display = ('table_name', 'record_count', 'is_visible', 'is_active', 'created_at', 'updated_at')
    list_filter = ('is_visible', 'is_active')
    search_fields = ('table_name',)
    ordering = ('-created_at',)


@admin.register(ExternalTableRecord)
class ExternalTableRecordAdmin(admin.ModelAdmin):
    list_display = ('id', 'table', 'created_at')
    list_filter = ('table',)
    search_fields = ('data',)
    ordering = ('-created_at',)
