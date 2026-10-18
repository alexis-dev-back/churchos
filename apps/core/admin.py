from django.contrib import admin

from apps.core.models import Campus, Church, Organization, OrganizationMembership


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('tenant_id', 'name', 'slug', 'phone', 'email', 'country', 'device', 'is_active', 'fuseau_horaire', 'date_created', 'update_date')
    search_fields = ('name', 'slug', 'phone', 'email', 'country', 'device')
    list_filter = ('is_active', 'country')


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'organization', 'role', 'date_created')
    search_fields = ('user__username', 'organization__name', 'role')
    list_filter = ('role',)


@admin.register(Church)
class ChurchAdmin(admin.ModelAdmin):
    list_display = ('church_id', 'organization', 'name', 'pastor_name', 'slug', 'phone', 'email', 'country', 'device', 'is_active', 'fuseau_horaire', 'date_created', 'update_date')
    search_fields = ('name', 'pastor_name', 'slug', 'phone', 'email', 'country', 'device')
    list_filter = ('is_active', 'country')


@admin.register(Campus)
class CampusAdmin(admin.ModelAdmin):
    list_display = ('campus_id', 'church', 'name', 'slug', 'phone', 'email', 'country', 'device', 'is_active', 'fuseau_horaire', 'date_created', 'update_date')
    search_fields = ('name', 'slug', 'phone', 'email', 'country', 'device')
    list_filter = ('is_active', 'country')

