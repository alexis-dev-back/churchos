import hashlib
import secrets
import calendar
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.models import Campus, Church
from apps.events.models import (
    AttendanceState,
    CheckInMode,
    Event,
    EventAttendance,
    EventAttendanceCorrection,
    EventAuditLog,
    EventOccurrence,
    EventRegistration,
    EventScope,
    EventStatus,
    RecurrenceFrequency,
    RegistrationMode,
    RegistrationStatus,
)
from apps.members.models import Member
from apps.people.models import Person


REGISTRATION_ACTIVE_STATUSES = (RegistrationStatus.CONFIRMED, RegistrationStatus.OFFERED)
EVENT_TRANSITIONS = {
    EventStatus.DRAFT: {EventStatus.PUBLISHED, EventStatus.CANCELLED},
    EventStatus.PUBLISHED: {EventStatus.IN_PROGRESS, EventStatus.COMPLETED, EventStatus.CANCELLED, EventStatus.ARCHIVED},
    EventStatus.IN_PROGRESS: {EventStatus.COMPLETED, EventStatus.CANCELLED},
    EventStatus.COMPLETED: {EventStatus.ARCHIVED},
    EventStatus.CANCELLED: {EventStatus.ARCHIVED},
    EventStatus.ARCHIVED: set(),
}


def token_hash(raw_token):
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def _new_verification_code(occurrence):
    for _ in range(100):
        code = f'{secrets.randbelow(100000):05d}'
        if not EventRegistration.objects.filter(
            occurrence=occurrence,
            verification_code_hash=token_hash(code),
        ).exists():
            return code
    raise ValidationError('Impossible de générer un code de vérification disponible pour cette séance.')


def record_event(event, action, actor=None, occurrence=None, detail=None):
    return EventAuditLog.objects.create(
        event=event,
        occurrence=occurrence,
        actor=actor,
        action=action,
        detail=detail or {},
    )


def validate_location(organization, scope, church_id=None, campus_id=None):
    if scope == EventScope.ORGANIZATION:
        if church_id or campus_id:
            raise ValidationError('Un événement d’organisation ne peut pas avoir une église ou un campus principal.')
        return None, None
    if scope == EventScope.CHURCH:
        if not church_id or campus_id:
            raise ValidationError({'church_id': 'Une église est requise et le campus doit être absent.'})
        church = Church.objects.filter(church_id=church_id, organization=organization, is_active=True).first()
        if church is None:
            raise ValidationError({'church_id': 'Église introuvable ou hors de votre organisation.'})
        return church, None
    if scope == EventScope.CAMPUS:
        if not campus_id or church_id:
            raise ValidationError({'campus_id': 'Un campus est requis et l’église principale doit être absente.'})
        campus = Campus.objects.filter(
            campus_id=campus_id,
            church__organization=organization,
            church__is_active=True,
            is_active=True,
        ).select_related('church').first()
        if campus is None:
            raise ValidationError({'campus_id': 'Campus introuvable ou hors de votre organisation.'})
        return None, campus
    raise ValidationError({'scope': 'Périmètre d’événement invalide.'})


def member_is_in_event_scope(event, member):
    if member.organization_id != event.organization_id or member.status != Member.Status.ACTIVE:
        return False
    if event.scope == EventScope.ORGANIZATION:
        selected_church_ids = set(event.included_churches.values_list('church_id', flat=True))
        return not selected_church_ids or member.church_id in selected_church_ids
    if event.scope == EventScope.CHURCH:
        return member.church_id == event.church_id
    return event.scope == EventScope.CAMPUS and member.campus_id == event.campus_id


def _active_registration_count(occurrence, now):
    return occurrence.registrations.filter(status=RegistrationStatus.CONFIRMED).count() + occurrence.registrations.filter(
        status=RegistrationStatus.OFFERED,
        offer_expires_at__gt=now,
    ).count()


@transaction.atomic
def offer_waitlist_places(occurrence, actor=None, now=None):
    now = now or timezone.now()
    occurrence = EventOccurrence.objects.select_for_update().select_related('event').get(pk=occurrence.pk)
    if occurrence.capacity is None or not occurrence.waitlist_enabled:
        return

    expired_registrations = EventRegistration.objects.select_for_update().filter(
        occurrence=occurrence,
        status=RegistrationStatus.OFFERED,
        offer_expires_at__lte=now,
    )
    for registration in expired_registrations:
        registration.status = RegistrationStatus.EXPIRED
        registration.offer_expires_at = None
        registration.save(update_fields=['status', 'offer_expires_at', 'updated_at'])
        record_event(
            occurrence.event,
            'WAITLIST_OFFER_EXPIRED',
            actor=actor,
            occurrence=occurrence,
            detail={'registration_id': str(registration.id)},
        )

    available = occurrence.capacity - _active_registration_count(occurrence, now)
    if available <= 0:
        return

    candidates = EventRegistration.objects.filter(
        occurrence=occurrence,
        status=RegistrationStatus.WAITLISTED,
    ).order_by('created_at', 'id')[:available]
    for registration in candidates:
        registration.status = RegistrationStatus.OFFERED
        registration.offer_expires_at = now + timedelta(hours=24)
        registration.save(update_fields=['status', 'offer_expires_at', 'updated_at'])
        record_event(
            occurrence.event,
            'WAITLIST_PLACE_OFFERED',
            actor=actor,
            occurrence=occurrence,
            detail={'registration_id': str(registration.id), 'expires_at': registration.offer_expires_at.isoformat()},
        )


def create_registration(occurrence, *, actor=None, member=None, visitor_data=None):
    now = timezone.now()
    with transaction.atomic():
        locked_occurrence = EventOccurrence.objects.select_for_update().select_related('event').get(pk=occurrence.pk)
        event = locked_occurrence.event
        offer_waitlist_places(locked_occurrence, actor=actor, now=now)

        if event.status != EventStatus.PUBLISHED or locked_occurrence.status != EventStatus.PUBLISHED:
            raise ValidationError('Les inscriptions sont disponibles uniquement pour un événement publié.')
        if locked_occurrence.registration_mode == RegistrationMode.DISABLED:
            raise ValidationError('Les inscriptions ne sont pas activées pour cette séance.')

        if member is not None and not member_is_in_event_scope(event, member):
            raise PermissionDenied('Ce membre ne fait pas partie du périmètre de l’événement.')

        existing = None
        if member is not None:
            existing = EventRegistration.objects.filter(occurrence=locked_occurrence, member=member).first()
            if existing and existing.status not in (RegistrationStatus.CANCELLED, RegistrationStatus.DECLINED, RegistrationStatus.EXPIRED):
                raise ValidationError('Ce membre est déjà inscrit à cette séance.')

        if existing is not None:
            registration = existing
            registration.status = RegistrationStatus.CONFIRMED
            registration.offer_expires_at = None
            registration.cancelled_at = None
            registration.cancelled_by = None
        else:
            person = None
            if member is None:
                if not visitor_data:
                    raise ValidationError('Les coordonnées du visiteur sont obligatoires.')
                person = Person.objects.create(
                    organization=locked_occurrence.event.organization,
                    first_name=visitor_data['first_name'],
                    last_name=visitor_data['last_name'],
                    email=visitor_data.get('email') or None,
                    phone=visitor_data.get('phone') or None,
                )
            registration = EventRegistration(
                occurrence=locked_occurrence,
                member=member,
                person=person,
                created_by=actor,
            )

        occupied = _active_registration_count(locked_occurrence, now)
        is_full = locked_occurrence.capacity is not None and occupied >= locked_occurrence.capacity
        if is_full:
            if not locked_occurrence.waitlist_enabled:
                raise ValidationError('Cette séance a atteint sa capacité maximale.')
            registration.status = RegistrationStatus.WAITLISTED
            registration.offer_expires_at = None
        else:
            registration.status = RegistrationStatus.CONFIRMED
            registration.offer_expires_at = None

        raw_qr_token = secrets.token_urlsafe(32) if registration.status == RegistrationStatus.CONFIRMED else None
        verification_code = _new_verification_code(locked_occurrence) if raw_qr_token else None
        raw_manage_token = secrets.token_urlsafe(32) if member is None else None
        if raw_qr_token:
            registration.qr_token_hash = token_hash(raw_qr_token)
            registration.verification_code_hash = token_hash(verification_code)
        else:
            registration.qr_token_hash = ''
            registration.verification_code_hash = ''
        if raw_manage_token:
            registration.manage_token_hash = token_hash(raw_manage_token)
        registration.save()
        from apps.attendance.services import sync_event_registration

        sync_event_registration(registration)
        record_event(
            event,
            'REGISTRATION_CREATED',
            actor=actor,
            occurrence=locked_occurrence,
            detail={'registration_id': str(registration.id), 'status': registration.status},
        )
        return registration, raw_qr_token, raw_manage_token, verification_code


def _verify_manage_token(registration, token):
    return bool(token and registration.manage_token_hash and secrets.compare_digest(
        registration.manage_token_hash,
        token_hash(token),
    ))


def cancel_registration(registration, *, actor=None, manage_token=None, can_manage=False):
    with transaction.atomic():
        locked = EventRegistration.objects.select_for_update().select_related('occurrence__event', 'member').get(pk=registration.pk)
        is_owner = actor is not None and locked.member is not None and locked.member.user_id == actor.id
        if not (can_manage or is_owner or _verify_manage_token(locked, manage_token)):
            raise PermissionDenied('Jeton de gestion invalide.')
        if locked.status in (RegistrationStatus.CANCELLED, RegistrationStatus.DECLINED, RegistrationStatus.EXPIRED):
            raise ValidationError('Cette inscription est déjà clôturée.')
        locked.status = RegistrationStatus.CANCELLED
        locked.cancelled_at = timezone.now()
        locked.cancelled_by = actor
        locked.offer_expires_at = None
        locked.save(update_fields=['status', 'cancelled_at', 'cancelled_by', 'offer_expires_at', 'updated_at'])
        from apps.attendance.services import sync_event_registration

        sync_event_registration(locked)
        record_event(
            locked.occurrence.event,
            'REGISTRATION_CANCELLED',
            actor=actor,
            occurrence=locked.occurrence,
            detail={'registration_id': str(locked.id)},
        )
        offer_waitlist_places(locked.occurrence, actor=actor)
        return locked


def respond_to_waitlist_offer(registration, *, accept, actor=None, manage_token=None, can_manage=False):
    expired = False
    with transaction.atomic():
        locked = EventRegistration.objects.select_for_update().select_related(
            'occurrence__event', 'member',
        ).get(pk=registration.pk)
        is_owner = actor is not None and locked.member is not None and locked.member.user_id == actor.id
        if not (can_manage or is_owner or _verify_manage_token(locked, manage_token)):
            raise PermissionDenied('Jeton de gestion invalide.')
        if locked.status != RegistrationStatus.OFFERED:
            raise ValidationError('Cette inscription ne dispose pas d’une offre de place active.')
        if not locked.offer_expires_at or locked.offer_expires_at <= timezone.now():
            locked.status = RegistrationStatus.EXPIRED
            locked.offer_expires_at = None
            locked.save(update_fields=['status', 'offer_expires_at', 'updated_at'])
            offer_waitlist_places(locked.occurrence, actor=actor)
            expired = True
        else:
            raw_qr_token = None
            verification_code = None
            if accept:
                locked.status = RegistrationStatus.CONFIRMED
                raw_qr_token = secrets.token_urlsafe(32)
                locked.qr_token_hash = token_hash(raw_qr_token)
                verification_code = _new_verification_code(locked.occurrence)
                locked.verification_code_hash = token_hash(verification_code)
            else:
                locked.status = RegistrationStatus.DECLINED
                locked.qr_token_hash = ''
                locked.verification_code_hash = ''
            locked.offer_expires_at = None
            locked.save(update_fields=[
                'status', 'qr_token_hash', 'verification_code_hash', 'offer_expires_at', 'updated_at',
            ])
            if accept:
                from apps.attendance.services import sync_event_registration

                sync_event_registration(locked)
            record_event(
                locked.occurrence.event,
                'WAITLIST_OFFER_ACCEPTED' if accept else 'WAITLIST_OFFER_DECLINED',
                actor=actor,
                occurrence=locked.occurrence,
                detail={'registration_id': str(locked.id)},
            )
            if not accept:
                offer_waitlist_places(locked.occurrence, actor=actor)
    if expired:
        raise ValidationError('L’offre de place a expiré.')
    return locked, raw_qr_token, verification_code


def checkin_window_open(occurrence, now=None):
    now = now or timezone.now()
    if occurrence.checkin_opens_at and now < occurrence.checkin_opens_at:
        return False
    if occurrence.checkin_closes_at and now > occurrence.checkin_closes_at:
        return False
    return True


def _validate_attendance_window(occurrence, actor, exception_reason):
    if not checkin_window_open(occurrence):
        if not exception_reason:
            raise ValidationError('Le pointage est en dehors de la fenêtre autorisée; un motif d’exception est requis.')
        record_event(
            occurrence.event,
            'CHECKIN_WINDOW_EXCEPTION',
            actor=actor,
            occurrence=occurrence,
            detail={'reason': exception_reason[:500]},
        )


def checkin_registration(registration, actor, raw_token, exception_reason=''):
    with transaction.atomic():
        locked = EventRegistration.objects.select_for_update().select_related(
            'occurrence__event', 'member', 'person',
        ).get(pk=registration.pk)
        occurrence = locked.occurrence
        token_digest = token_hash(raw_token) if raw_token else ''
        valid_qr = bool(locked.qr_token_hash and secrets.compare_digest(locked.qr_token_hash, token_digest))
        valid_fallback = bool(
            isinstance(raw_token, str)
            and len(raw_token) == 5
            and raw_token.isdigit()
            and locked.verification_code_hash
            and secrets.compare_digest(locked.verification_code_hash, token_digest)
        )
        if not (valid_qr or valid_fallback):
            raise ValidationError('Le QR ou le code de secours de cette inscription est invalide.')
        if locked.status != RegistrationStatus.CONFIRMED:
            raise ValidationError('Cette inscription ne permet pas le pointage.')
        if occurrence.checkin_mode != CheckInMode.REGISTRATION:
            raise ValidationError('Cette séance n’accepte pas le pointage par inscription.')
        if occurrence.event.status not in (EventStatus.PUBLISHED, EventStatus.IN_PROGRESS):
            raise ValidationError('L’événement ne permet plus le pointage.')
        if occurrence.status not in (EventStatus.PUBLISHED, EventStatus.IN_PROGRESS):
            raise ValidationError('Cette séance ne permet plus le pointage.')
        _validate_attendance_window(occurrence, actor, exception_reason)
        prior = EventAttendance.objects.filter(registration=locked).first()
        if prior:
            from apps.attendance.services import sync_event_attendance

            sync_event_attendance(prior, actor=actor)
            return prior, False
        attendance = EventAttendance.objects.create(
            occurrence=occurrence,
            registration=locked,
            checked_in_by=actor,
            source='REGISTRATION',
        )
        record_event(
            occurrence.event,
            'ATTENDANCE_CONFIRMED',
            actor=actor,
            occurrence=occurrence,
            detail={'registration_id': str(locked.id), 'attendance_id': str(attendance.id)},
        )
        from apps.attendance.services import sync_event_attendance

        sync_event_attendance(attendance, actor=actor)
        return attendance, True


def checkin_self(occurrence, member, raw_token):
    with transaction.atomic():
        locked = EventOccurrence.objects.select_for_update().select_related('event').get(pk=occurrence.pk)
        event = locked.event
        if locked.checkin_mode != CheckInMode.SELF:
            raise ValidationError('Le pointage autonome n’est pas activé pour cette séance.')
        if not raw_token or not secrets.compare_digest(locked.self_checkin_token_hash, token_hash(raw_token)):
            raise PermissionDenied('Code de pointage invalide.')
        if not member_is_in_event_scope(event, member):
            raise PermissionDenied('Ce membre ne fait pas partie du périmètre de l’événement.')
        if event.status not in (EventStatus.PUBLISHED, EventStatus.IN_PROGRESS) or locked.status not in (EventStatus.PUBLISHED, EventStatus.IN_PROGRESS):
            raise ValidationError('Cette séance ne permet plus le pointage.')
        _validate_attendance_window(locked, None, '')
        attendance, created = EventAttendance.objects.get_or_create(
            occurrence=locked,
            member=member,
            defaults={'checked_in_by': None, 'source': 'SELF'},
        )
        from apps.attendance.services import sync_event_attendance

        sync_event_attendance(attendance, actor=member.user)
        record_event(
            event,
            'SELF_CHECKIN_CONFIRMED' if created else 'SELF_CHECKIN_DUPLICATE',
            actor=member.user,
            occurrence=locked,
            detail={'member_id': str(member.id), 'attendance_id': str(attendance.id)},
        )
        return attendance, created


def correct_attendance(attendance, actor, new_state, reason):
    if not reason or not reason.strip():
        raise ValidationError({'reason': 'Le motif de correction est obligatoire.'})
    with transaction.atomic():
        locked = EventAttendance.objects.select_for_update().select_related('occurrence__event').get(pk=attendance.pk)
        if locked.state == new_state:
            raise ValidationError('La présence est déjà dans cet état.')
        correction = EventAttendanceCorrection.objects.create(
            attendance=locked,
            previous_state=locked.state,
            corrected_state=new_state,
            reason=reason.strip(),
            actor=actor,
        )
        locked.state = new_state
        locked.save(update_fields=['state'])
        from apps.attendance.services import sync_event_attendance

        sync_event_attendance(locked, actor=actor)
        record_event(
            locked.occurrence.event,
            'ATTENDANCE_CORRECTED',
            actor=actor,
            occurrence=locked.occurrence,
            detail={
                'attendance_id': str(locked.id),
                'previous_state': correction.previous_state,
                'corrected_state': correction.corrected_state,
                'reason': correction.reason,
            },
        )
        return correction


def transition_event(event, new_status, actor, confirm=False):
    with transaction.atomic():
        locked = Event.objects.select_for_update().get(pk=event.pk)
        if new_status not in EVENT_TRANSITIONS.get(locked.status, set()):
            raise ValidationError(f'Transition de statut interdite : {locked.status} → {new_status}.')
        if new_status == EventStatus.CANCELLED and not confirm:
            raise ValidationError({'confirm': 'Confirmez explicitement l’annulation de la série.'})
        previous = locked.status
        locked.status = new_status
        locked.updated_by = actor
        locked.save(update_fields=['status', 'updated_by', 'updated_at'])
        if new_status == EventStatus.PUBLISHED:
            locked.occurrences.filter(status=EventStatus.DRAFT).update(status=EventStatus.PUBLISHED)
            from apps.attendance.services import sync_event_occurrence_status

            for occurrence in locked.occurrences.select_related('event', 'event__church', 'event__campus'):
                sync_event_occurrence_status(occurrence, actor)
        elif new_status == EventStatus.CANCELLED:
            locked.occurrences.filter(starts_at__gte=timezone.now()).exclude(
                status__in=[EventStatus.COMPLETED, EventStatus.ARCHIVED],
            ).update(status=EventStatus.CANCELLED)
            from apps.attendance.services import sync_event_occurrence_status

            for occurrence in locked.occurrences.select_related('event', 'event__church', 'event__campus'):
                sync_event_occurrence_status(occurrence, actor)
        record_event(locked, 'STATUS_CHANGED', actor=actor, detail={'from': previous, 'to': new_status})
        return locked


def transition_occurrence(occurrence, new_status, actor, confirm=False):
    with transaction.atomic():
        locked = EventOccurrence.objects.select_for_update().select_related('event').get(pk=occurrence.pk)
        transitions = dict(EVENT_TRANSITIONS)
        transitions[EventStatus.DRAFT] = {EventStatus.PUBLISHED, EventStatus.CANCELLED}
        if new_status not in transitions.get(locked.status, set()):
            raise ValidationError(f'Transition de statut interdite : {locked.status} → {new_status}.')
        if new_status == EventStatus.CANCELLED and not confirm:
            raise ValidationError({'confirm': 'Confirmez explicitement l’annulation de cette occurrence.'})
        if new_status == EventStatus.PUBLISHED and locked.event.status != EventStatus.PUBLISHED:
            raise ValidationError('Publiez d’abord l’événement avant de publier une occurrence isolée.')
        if locked.event.status in (EventStatus.CANCELLED, EventStatus.ARCHIVED):
            raise ValidationError('Cet événement ne permet plus de modifier ses occurrences.')
        previous = locked.status
        locked.status = new_status
        locked.save(update_fields=['status', 'updated_at'])
        from apps.attendance.services import sync_event_occurrence_status

        sync_event_occurrence_status(locked, actor)
        record_event(locked.event, 'OCCURRENCE_STATUS_CHANGED', actor=actor, occurrence=locked, detail={'from': previous, 'to': new_status})
        return locked


def generate_occurrences(event, actor, limit=50):
    if limit < 1 or limit > 50:
        raise ValidationError({'limit': 'La génération est limitée à 50 occurrences par requête.'})
    if event.recurrence_frequency == RecurrenceFrequency.NONE or event.recurrence_until is None:
        raise ValidationError('Une fréquence et une date de fin de récurrence sont requises.')
    template = event.occurrences.order_by('starts_at').first()
    if template is None:
        raise ValidationError('L’événement doit avoir une première occurrence.')
    if template.starts_at.date() > event.recurrence_until:
        raise ValidationError('La première occurrence est postérieure à la fin de récurrence.')

    current = event.occurrences.order_by('-starts_at').first()
    start = current.starts_at
    duration = current.ends_at - current.starts_at
    opens_offset = template.checkin_opens_at - template.starts_at if template.checkin_opens_at else None
    closes_offset = template.checkin_closes_at - template.starts_at if template.checkin_closes_at else None
    created = []
    for _ in range(limit):
        if event.recurrence_frequency == RecurrenceFrequency.DAILY:
            next_start = start + timedelta(days=event.recurrence_interval)
        elif event.recurrence_frequency == RecurrenceFrequency.WEEKLY:
            next_start = start + timedelta(weeks=event.recurrence_interval)
        else:
            month_index = start.year * 12 + start.month - 1 + event.recurrence_interval
            year, zero_based_month = divmod(month_index, 12)
            month = zero_based_month + 1
            day = min(start.day, calendar.monthrange(year, month)[1])
            next_start = start.replace(year=year, month=month, day=day)
        if next_start.date() > event.recurrence_until:
            break
        start = next_start
        if event.occurrences.filter(starts_at=start).exists():
            continue
        occurrence = EventOccurrence.objects.create(
            event=event,
            starts_at=start,
            ends_at=start + duration,
            status=EventStatus.PUBLISHED if event.status == EventStatus.PUBLISHED else EventStatus.DRAFT,
            location=template.location,
            registration_mode=template.registration_mode,
            capacity=template.capacity,
            waitlist_enabled=template.waitlist_enabled,
            checkin_mode=template.checkin_mode,
            checkin_opens_at=start + opens_offset if opens_offset is not None else None,
            checkin_closes_at=start + closes_offset if closes_offset is not None else None,
        )
        created.append(occurrence)
        if occurrence.status == EventStatus.PUBLISHED:
            from apps.attendance.services import sync_event_occurrence_status

            sync_event_occurrence_status(occurrence, actor)
    record_event(event, 'OCCURRENCES_GENERATED', actor=actor, detail={'count': len(created)})
    return created


def event_statistics(event):
    occurrences = event.occurrences.annotate(
        confirmed_count=Count('registrations', filter=Q(registrations__status=RegistrationStatus.CONFIRMED), distinct=True),
        offered_count=Count('registrations', filter=Q(
            registrations__status=RegistrationStatus.OFFERED,
            registrations__offer_expires_at__gt=timezone.now(),
        ), distinct=True),
        waitlisted_count=Count('registrations', filter=Q(registrations__status=RegistrationStatus.WAITLISTED), distinct=True),
        attendance_count=Count('attendances', filter=Q(attendances__state=AttendanceState.PRESENT), distinct=True),
    )
    return [{
        'occurrence_id': str(item.id),
        'starts_at': item.starts_at,
        'status': item.status,
        'capacity': item.capacity,
        'confirmed': item.confirmed_count,
        'waitlisted': item.waitlisted_count,
        'remaining': None if item.capacity is None else max(item.capacity - item.confirmed_count - item.offered_count, 0),
        'offered': item.offered_count,
        'attendance': item.attendance_count,
    } for item in occurrences]
