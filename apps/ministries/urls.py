from django.urls import path

from apps.ministries.views import (
    MinistryAssignmentCloseView,
    MinistryAssignmentListView,
    MinistryDetailView,
    MinistryLeadershipCloseView,
    MinistryLeadershipListView,
    MinistryListView,
    TribeAssignmentCloseView,
    TribeAssignmentListView,
    TribeDetailView,
    TribeLeadershipCloseView,
    TribeLeadershipListView,
    TribeListView,
)

urlpatterns = [
    path('ministries/', MinistryListView.as_view(), name='ministry-list-create'),
    path('ministries/<uuid:pk>/', MinistryDetailView.as_view(), name='ministry-detail'),
    path('ministries/<uuid:pk>/assignments/', MinistryAssignmentListView.as_view(), name='ministry-assignments'),
    path('ministries/assignments/<uuid:assignment_id>/close/', MinistryAssignmentCloseView.as_view(), name='ministry-assignment-close'),
    path('ministries/<uuid:pk>/leadership/', MinistryLeadershipListView.as_view(), name='ministry-leadership'),
    path('ministries/leadership/<uuid:leadership_id>/close/', MinistryLeadershipCloseView.as_view(), name='ministry-leadership-close'),
    path('tribes/', TribeListView.as_view(), name='tribe-list-create'),
    path('tribes/<uuid:pk>/', TribeDetailView.as_view(), name='tribe-detail'),
    path('tribes/<uuid:pk>/assignments/', TribeAssignmentListView.as_view(), name='tribe-assignments'),
    path('tribes/assignments/<uuid:assignment_id>/close/', TribeAssignmentCloseView.as_view(), name='tribe-assignment-close'),
    path('tribes/<uuid:pk>/leadership/', TribeLeadershipListView.as_view(), name='tribe-leadership'),
    path('tribes/leadership/<uuid:leadership_id>/close/', TribeLeadershipCloseView.as_view(), name='tribe-leadership-close'),
]
