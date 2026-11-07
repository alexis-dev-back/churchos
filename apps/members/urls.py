from django.urls import path

from apps.members.views import AcceptInvitationView, MemberDetailView, MemberHistoryView, MemberInvitationView, MemberListCreateView, MemberMeView, MemberResendInvitationView, MemberSuspendView, MemberArchiveView

urlpatterns = [
    path('', MemberListCreateView.as_view(), name='member-list-create'),
    path('me/', MemberMeView.as_view(), name='member-me'),
    path('<uuid:pk>/history/', MemberHistoryView.as_view(), name='member-history'),
    path('<uuid:pk>/history/<uuid:entry_id>/', MemberHistoryView.as_view(), name='member-history-detail'),
    path('<uuid:pk>/', MemberDetailView.as_view(), name='member-detail'),
    path('<uuid:pk>/suspend/', MemberSuspendView.as_view(), name='member-suspend'),
    path('<uuid:pk>/archive/', MemberArchiveView.as_view(), name='member-archive'),
    path('<uuid:pk>/invite/', MemberInvitationView.as_view(), name='member-invite'),
    path('<uuid:pk>/resend-invitation/', MemberResendInvitationView.as_view(), name='member-resend-invitation'),
    path('auth/accept-invitation/', AcceptInvitationView.as_view(), name='accept-invitation'),
]
