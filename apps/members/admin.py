from django.contrib import admin

from apps.members.models import Member, MemberInvitation


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ('first_name', 'last_name', 'email', 'organization', 'church', 'campus', 'status')
    search_fields = ('first_name', 'last_name', 'email')
    list_filter = ('status', 'organization')


@admin.register(MemberInvitation)
class MemberInvitationAdmin(admin.ModelAdmin):
    list_display = ('member', 'status', 'expires_at', 'used_at', 'created_at')
    search_fields = ('member__email', 'token_hash')
    list_filter = ('status',)
