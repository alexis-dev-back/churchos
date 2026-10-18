from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.events.models import EventOccurrence, RegistrationStatus
from apps.events.services import offer_waitlist_places


class Command(BaseCommand):
    help = 'Expire overdue event waitlist offers and offer released places to the next participants.'

    def handle(self, *args, **options):
        now = timezone.now()
        occurrence_ids = EventOccurrence.objects.filter(
            Q(registrations__status=RegistrationStatus.WAITLISTED)
            | Q(registrations__status=RegistrationStatus.OFFERED, registrations__offer_expires_at__lte=now),
        ).values_list('id', flat=True).distinct()
        processed = 0
        for occurrence in EventOccurrence.objects.filter(id__in=occurrence_ids).select_related('event').iterator():
            offer_waitlist_places(occurrence, now=now)
            processed += 1
        self.stdout.write(self.style.SUCCESS(f'Processed waitlist offers for {processed} occurrence(s).'))
