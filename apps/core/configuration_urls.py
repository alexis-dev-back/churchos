from django.urls import path

from apps.core.views.configuration_views import (
    ConfigurationAuditLogView,
    ConfigurationAccessView,
    ConfigurationChurchSettingsView,
    ConfigurationPermissionCatalogView,
    ConfigurationUserDetailView,
    OrganizationRoleDetailView,
    OrganizationRoleListCreateView,
    OrganizationRolePermissionsView,
    UserRoleAssignmentListCreateView,
    UserRoleAssignmentRevokeView,
    ConfigurationUsersView,
)


urlpatterns = [
    path('access/', ConfigurationAccessView.as_view(), name='configuration-access'),
    path('church/', ConfigurationChurchSettingsView.as_view(), name='configuration-church'),
    path('permissions/', ConfigurationPermissionCatalogView.as_view(), name='configuration-permissions'),
    path('roles/', OrganizationRoleListCreateView.as_view(), name='configuration-roles'),
    path('roles/<uuid:role_id>/', OrganizationRoleDetailView.as_view(), name='configuration-role-detail'),
    path('roles/<uuid:role_id>/permissions/', OrganizationRolePermissionsView.as_view(), name='configuration-role-permissions'),
    path('users/', ConfigurationUsersView.as_view(), name='configuration-users'),
    path('users/<int:membership_id>/', ConfigurationUserDetailView.as_view(), name='configuration-user-detail'),
    path('user-roles/', UserRoleAssignmentListCreateView.as_view(), name='configuration-user-roles'),
    path('user-roles/<uuid:assignment_id>/revoke/', UserRoleAssignmentRevokeView.as_view(), name='configuration-user-role-revoke'),
    path('audit-logs/', ConfigurationAuditLogView.as_view(), name='configuration-audit-logs'),
]
