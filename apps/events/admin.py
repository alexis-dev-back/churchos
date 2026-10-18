from django.contrib import admin

from apps.events.models import (
    ChurchEventAdministrator,
    Event,
    EventAttendance,
    EventAttendanceCorrection,
    EventAuditLog,
    EventOccurrence,
    EventRegistration,
)


class EventOccurrenceInline(admin.TabularInline):
    model = EventOccurrence
    extra = 0


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'scope', 'status', 'organization', 'created_at')
    list_filter = ('scope', 'status', 'recurrence_frequency')
    search_fields = ('title', 'description')
    inlines = [EventOccurrenceInline]


admin.site.register(EventRegistration)
admin.site.register(EventAttendance)
admin.site.register(EventAttendanceCorrection)
admin.site.register(EventAuditLog)
admin.site.register(ChurchEventAdministrator)
