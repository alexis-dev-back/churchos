from django.urls import path

from .views import TaskDetailView, TaskListCreateView

urlpatterns = [
    path('', TaskListCreateView.as_view(), name='task-list-create'),
    path('<uuid:task_id>/', TaskDetailView.as_view(), name='task-detail'),
]
