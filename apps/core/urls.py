from django.urls import include, path

from apps.members.views import AcceptInvitationView
from .views.auth_views import AuthTokenRefreshView, LoginView, LogoutView, MeView, RegistrationView, SelectOrganizationView
from .views.campus_views import *
from .views.church_views import *
from .views.organization_views import OrganizationMemberCreateView, OrganizationView, OrganisationCreateView, OrganisationDeleteView, OrganisationDetailView, OrganisationUpdateView
from .views.superadmin_views import SuperAdminChurchDetailView, SuperAdminChurchesView, SuperAdminOrganizationDetailView, SuperAdminOrganizationsView, SuperAdminOrganizationUsersView, SuperAdminUserCreateView

urlpatterns = [
    path('auth/register/', RegistrationView.as_view(), name='auth-register'),
    path('auth/login/', LoginView.as_view(), name='auth-login'),
    path('auth/refresh/', AuthTokenRefreshView.as_view(), name='auth-refresh'),
    path('auth/logout/', LogoutView.as_view(), name='auth-logout'),
    path('auth/me/', MeView.as_view(), name='auth-me'),
    path('auth/select-organization/', SelectOrganizationView.as_view(), name='auth-select-organization'),
    path('auth/accept-invitation/', AcceptInvitationView.as_view(), name='auth-accept-invitation'),

    path('organizations/', OrganizationView.as_view(), name='organization-list'),
    path('organizations/create/', OrganisationCreateView.as_view(), name='organization-create'),
    path('organizations/<uuid:tenant_id>/members/', OrganizationMemberCreateView.as_view(), name='organization-member-create'),
    path('organizations/<uuid:tenant_id>/', OrganisationDetailView.as_view(), name='organization-detail'),
    path('organizations/<uuid:tenant_id>/delete/', OrganisationDeleteView.as_view(), name='organization-delete'),
    path('organizations/<uuid:tenant_id>/update/', OrganisationUpdateView.as_view(), name='organization-update'),

    path('churches/', ChurchView.as_view(), name='church-list'),
    path('churches/context/', ChurchContextView.as_view(), name='church-context'),
    path('churches/create/', ChurchCreateView.as_view(), name='church-create'),
    path('churches/<uuid:church_id>/', ChurchDetailView.as_view(), name='church-detail'),
    path('churches/<uuid:church_id>/delete/', ChurchDeleteView.as_view(), name='church-delete'),
    path('churches/<uuid:church_id>/update/', ChurchUpdateView.as_view(), name='church-update'),

    path('campuses/', CampusView.as_view(), name='campus-list'),
    path('campuses/create/', CampusCreateView.as_view(), name='campus-create'),
    path('campuses/<uuid:campus_id>/', CampusDetailView.as_view(), name='campus-detail'),
    path('campuses/<uuid:campus_id>/delete/', CampusDeleteView.as_view(), name='campus-delete'),
    path('campuses/<uuid:campus_id>/update/', CampusUpdateView.as_view(), name='campus-update'),
    path('superadmin/organizations/', SuperAdminOrganizationsView.as_view(), name='superadmin-organizations'),
    path('superadmin/organizations/<uuid:tenant_id>/', SuperAdminOrganizationDetailView.as_view(), name='superadmin-organization-detail'),
    path('superadmin/organizations/<uuid:tenant_id>/users/', SuperAdminOrganizationUsersView.as_view(), name='superadmin-organization-users'),
    path('superadmin/organizations/<uuid:tenant_id>/churches/', SuperAdminChurchesView.as_view(), name='superadmin-organization-churches'),
    path('superadmin/churches/<uuid:church_id>/', SuperAdminChurchDetailView.as_view(), name='superadmin-church-detail'),
    path('superadmin/users/', SuperAdminUserCreateView.as_view(), name='superadmin-user-create'),
    path('members/', include('apps.members.urls')),
]
