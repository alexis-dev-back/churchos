from django.urls import path

from apps.people.views import PersonCreateMembershipView, PersonDetailView, PersonInviterListView, PersonListCreateView

urlpatterns = [
    path('', PersonListCreateView.as_view(), name='person-list-create'),
    path('inviter-members/', PersonInviterListView.as_view(), name='person-inviter-members'),
    path('<uuid:pk>/create-membership/', PersonCreateMembershipView.as_view(), name='person-create-membership'),
    path('<uuid:pk>/', PersonDetailView.as_view(), name='person-detail'),
]
