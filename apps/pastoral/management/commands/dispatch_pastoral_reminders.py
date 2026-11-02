from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import Organization
from apps.pastoral.models import PastoralNotification, PastoralTask
from apps.pastoral.services import can_access_case


class Command(BaseCommand):
    help = 'Crée des notifications pastorales génériques et idempotentes pour les tâches échues.'

    def handle(self, *args, **options):
        now = timezone.now()
        created = 0
        tasks = PastoralTask.objects.filter(
            case__organization__in=Organization.objects.filter(is_active=True),
            due_at__lte=now,
            status__in=[PastoralTask.Status.OPEN, PastoralTask.Status.IN_PROGRESS],
        ).select_related(
            'case', 'case__organization', 'case__church', 'case__campus', 'assigned_to',
        )
        for task in tasks.iterator():
            if not can_access_case(task.assigned_to, task.case):
                continue
            _, was_created = PastoralNotification.objects.get_or_create(
                organization=task.case.organization,
                recipient=task.assigned_to,
                task=task,
                notification_key=f'task:{task.pk}:due:{task.due_at.isoformat()}',
            )
            created += int(was_created)
        self.stdout.write(self.style.SUCCESS(f'{created} notification(s) pastorale(s) créée(s).'))
