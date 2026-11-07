from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from infrastructure.tenancy import TenantContext
from infrastructure.tenancy.permissions import HasActiveTenant

from .models import Task
from .serializers import CLOSED_STATUSES, TaskSerializer


def _organization():
    organization = TenantContext.get()
    if organization is None:
        raise PermissionDenied('Aucune organisation active dans le contexte de la requête.')
    return organization


def _user_tasks(request):
    return Task.objects.filter(
        organization=_organization(),
        owner=request.user,
    )


def _summary(queryset):
    today = timezone.localdate()
    counts = queryset.aggregate(
        total=Count('id'),
        todo=Count('id', filter=Q(status=Task.Status.TODO)),
        in_progress=Count('id', filter=Q(status=Task.Status.IN_PROGRESS)),
        done=Count('id', filter=Q(status=Task.Status.DONE)),
        cancelled=Count('id', filter=Q(status=Task.Status.CANCELLED)),
        overdue=Count(
            'id',
            filter=Q(due_date__lt=today) & ~Q(status__in=CLOSED_STATUSES),
        ),
    )
    total = counts['total'] or 0
    counts['completion_rate'] = round((counts['done'] / total) * 100) if total else 0
    return counts


class TaskListCreateView(APIView):
    permission_classes = [IsAuthenticated, HasActiveTenant]

    def get(self, request):
        tasks = _user_tasks(request)
        filtered = tasks

        status_filter = request.query_params.get('status', '').strip().upper()
        if status_filter in Task.Status.values:
            filtered = filtered.filter(status=status_filter)

        priority_filter = request.query_params.get('priority', '').strip().upper()
        if priority_filter in Task.Priority.values:
            filtered = filtered.filter(priority=priority_filter)

        search = request.query_params.get('search', '').strip()
        if search:
            filtered = filtered.filter(
                Q(title__icontains=search) | Q(description__icontains=search)
            )

        if request.query_params.get('overdue', '').lower() in ('1', 'true', 'yes'):
            filtered = filtered.filter(
                due_date__lt=timezone.localdate(),
            ).exclude(status__in=CLOSED_STATUSES)

        return Response({
            'results': TaskSerializer(filtered, many=True).data,
            'summary': _summary(tasks),
        })

    def post(self, request):
        serializer = TaskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = serializer.save(
            organization=_organization(),
            owner=request.user,
        )
        return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)


class TaskDetailView(APIView):
    permission_classes = [IsAuthenticated, HasActiveTenant]

    def _task(self, request, task_id):
        return get_object_or_404(_user_tasks(request), pk=task_id)

    def get(self, request, task_id):
        return Response(TaskSerializer(self._task(request, task_id)).data)

    def patch(self, request, task_id):
        task = self._task(request, task_id)
        serializer = TaskSerializer(task, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        task = serializer.save()
        return Response(TaskSerializer(task).data)

    def delete(self, request, task_id):
        task = self._task(request, task_id)
        task.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
