from django.utils import timezone
from rest_framework import serializers

from .models import Task

CLOSED_STATUSES = {Task.Status.DONE, Task.Status.CANCELLED}


class TaskSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    priority_label = serializers.CharField(source='get_priority_display', read_only=True)
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            'id',
            'title',
            'description',
            'status',
            'status_label',
            'priority',
            'priority_label',
            'due_date',
            'completed_at',
            'is_overdue',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'status_label',
            'priority_label',
            'completed_at',
            'is_overdue',
            'created_at',
            'updated_at',
        ]

    def validate_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Le titre est obligatoire.')
        return value

    def validate_description(self, value):
        return value.strip()

    def create(self, validated_data):
        if validated_data.get('status') == Task.Status.DONE:
            validated_data['completed_at'] = timezone.now()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        next_status = validated_data.get('status', instance.status)
        if next_status == Task.Status.DONE and instance.status != Task.Status.DONE:
            validated_data['completed_at'] = timezone.now()
        elif next_status != Task.Status.DONE and instance.status == Task.Status.DONE:
            validated_data['completed_at'] = None
        return super().update(instance, validated_data)

    def get_is_overdue(self, task):
        return bool(
            task.due_date
            and task.status not in CLOSED_STATUSES
            and task.due_date < timezone.localdate()
        )
