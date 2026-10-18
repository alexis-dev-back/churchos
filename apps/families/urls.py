from django.urls import path

from apps.families.views import FamilyDetailView, FamilyListCreateView, FamilyMemberView

urlpatterns = [
    path('', FamilyListCreateView.as_view(), name='family-list-create'),
    path('<uuid:pk>/', FamilyDetailView.as_view(), name='family-detail'),
    path('<uuid:pk>/members/', FamilyMemberView.as_view(), name='family-member-create'),
]
