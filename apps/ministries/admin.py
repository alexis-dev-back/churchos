from django.contrib import admin

from apps.ministries.models import (
    Ministry,
    MinistryAssignment,
    MinistryLeadership,
    Tribe,
    TribeAssignment,
    TribeLeadership,
)


@admin.register(Ministry)
class MinistryAdmin(admin.ModelAdmin):
    list_display = ('name', 'church', 'status', 'created_at')
    list_filter = ('status', 'church')
    search_fields = ('name', 'church__name')


@admin.register(Tribe)
class TribeAdmin(admin.ModelAdmin):
    list_display = ('name', 'church', 'status', 'created_at')
    list_filter = ('status', 'church')
    search_fields = ('name', 'church__name')


@admin.register(MinistryAssignment)
class MinistryAssignmentAdmin(admin.ModelAdmin):
    list_display = ('member', 'ministry', 'joined_at', 'left_at')
    list_filter = ('ministry__church',)
    search_fields = ('member__first_name', 'member__last_name', 'ministry__name')


@admin.register(TribeAssignment)
class TribeAssignmentAdmin(admin.ModelAdmin):
    list_display = ('member', 'tribe', 'joined_at', 'left_at')
    list_filter = ('tribe__church',)
    search_fields = ('member__first_name', 'member__last_name', 'tribe__name')


@admin.register(MinistryLeadership)
class MinistryLeadershipAdmin(admin.ModelAdmin):
    list_display = ('member', 'ministry', 'role', 'started_at', 'ended_at')
    list_filter = ('role', 'ministry__church')
    search_fields = ('member__first_name', 'member__last_name', 'ministry__name')


@admin.register(TribeLeadership)
class TribeLeadershipAdmin(admin.ModelAdmin):
    list_display = ('member', 'tribe', 'role', 'started_at', 'ended_at')
    list_filter = ('role', 'tribe__church')
    search_fields = ('member__first_name', 'member__last_name', 'tribe__name')
