from django.core.management.base import BaseCommand

from apps.core.models import Organization
from apps.attendance.services import evaluate_attendance_alerts


class Command(BaseCommand):
    help = 'Évalue les règles d’assiduité et crée les alertes dédupliquées.'

    def handle(self, *args, **options):
        total = 0
        for organization in Organization.objects.filter(is_active=True).iterator():
            total += evaluate_attendance_alerts(organization)
        self.stdout.write(self.style.SUCCESS(f'{total} nouvelle(s) alerte(s) créée(s).'))
