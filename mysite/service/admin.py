from django.contrib import admin
from .models import OwnerUser, Member, Comment


@admin.register(OwnerUser)
class OwnerUserAdmin(admin.ModelAdmin):
    list_display = ['email', 'program_name', 'is_owner', 'is_active', 'created_at']
    list_filter = ['is_owner', 'is_active', 'created_at']
    search_fields = ['email', 'program_name']
    ordering = ['-created_at']


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ['reg_number', 'program_name', 'owner', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['reg_number', 'program_name', 'owner__email']
    ordering = ['-created_at']


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ['member', 'owner', 'status', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['member__reg_number', 'owner__email', 'content']
    ordering = ['-created_at']