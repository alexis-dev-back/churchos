from django.urls import path

from apps.communication.views import AnnouncementDetailView, AnnouncementListCreateView, CommunicationContextView

urlpatterns = [
    path('context/', CommunicationContextView.as_view(), name='communication-context'),
    path('announcements/', AnnouncementListCreateView.as_view(), name='announcement-list-create'),
    path('announcements/<uuid:announcement_id>/', AnnouncementDetailView.as_view(), name='announcement-detail'),
]
