from django.urls import path

from apps.people.views import PersonCreateMembershipView, PersonDetailView, PersonListCreateView

urlpatterns = [
    path('', PersonListCreateView.as_view(), name='person-list-create'),
    path('<uuid:pk>/create-membership/', PersonCreateMembershipView.as_view(), name='person-create-membership'),
    path('<uuid:pk>/', PersonDetailView.as_view(), name='person-detail'),
]
