import calendar
import hashlib
import secrets
import uuid
from collections import defaultdict
from django.conf import settings
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.models import Campus, Church
from apps.events.models import EventAttendance, EventOccurrence
from apps.members.models import Member
from apps.ministries.models import MinistryAssignment, TribeAssignment
from apps.people.models import Person
from infrastructure.tenancy import TenantContext

from .models import (
    AbsenceExcuse,
    AttendanceAlert,
    AttendanceAlertNote,
    AttendanceAuditLog,
    AttendanceCorrection,
    AttendanceRecord,
    AttendancePolicy,
    AttendanceSession,
    AttendanceSessionType,
    AttendanceStatus,
    AttendanceVisitor,
    ExcuseStatus,
    ExpectedParticipant,
    PopulationRule,
    RecurrenceFrequency,
    SessionLifecycle,
    SessionMethod,
)


def token_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def record_audit(organization, action, actor=None, session=None, detail=None):
    return AttendanceAuditLog.objects.create(
        organization=organization,
        session=session,
        actor=actor,
        action=action,
        detail=detail or {},
    )


def _validate_methods(methods):
    if not isinstance(methods, list) or not methods:
        raise ValidationError({'allowed_methods': 'Au moins une méthode de pointage est requise.'})
    valid = {choice for choice, _ in SessionMethod.choices}
    invalid = sorted(set(methods) - valid)
    if invalid:
        raise ValidationError({'allowed_methods': f'Méthodes de pointage inconnues : {", ".join(invalid)}.'})
    return list(dict.fromkeys(methods))


def _eligible_members(session, population_rule, member_ids=None):
    if session.church_id is None:
        raise ValidationError({'church_id': 'Cette règle nécessite une séance rattachée à une église.'})
    members = Member.objects.filter(
        organization=session.organization,
        church=session.church,
        status=Member.Status.ACTIVE,
        user__is_active=True,
    )
    if session.campus_id:
        members = members.filter(campus_id=session.campus_id)
    if population_rule == PopulationRule.MINISTRY:
        if session.ministry_id is None:
            raise ValidationError({'ministry_id': 'Un ministère est requis pour cette règle de population.'})
        members = members.filter(
            ministry_assignments__ministry=session.ministry,
            ministry_assignments__left_at__isnull=True,
        )
    elif population_rule == PopulationRule.TRIBE:
        if session.tribe_id is None:
            raise ValidationError({'tribe_id': 'Une tribu est requise pour cette règle de population.'})
        members = members.filter(
            tribe_assignments__tribe=session.tribe,
            tribe_assignments__left_at__isnull=True,
        )
    elif population_rule == PopulationRule.MANUAL:
        if member_ids is None:
            raise ValidationError({'member_ids': 'Sélectionnez les membres attendus.'})
        members = members.filter(pk__in=member_ids)
        found_ids = set(members.values_list('id', flat=True))
        missing_ids = set(member_ids) - found_ids
        if missing_ids:
            raise ValidationError({'member_ids': 'Un ou plusieurs membres sont absents, inactifs ou hors périmètre.'})
    elif population_rule != PopulationRule.ALL_MEMBERS:
        raise ValidationError({'population_rule': 'Règle de population non prise en charge.'})
    return members.distinct().order_by('last_name', 'first_name')


def member_is_in_session_scope(session, member):
    if member.organization_id != session.organization_id or member.status != Member.Status.ACTIVE:
        return False
    if session.church_id and member.church_id != session.church_id:
        return False
    if session.campus_id and member.campus_id != session.campus_id:
        return False
    if session.event_occurrence_id:
        from apps.events.services import member_is_in_event_scope

        return member_is_in_event_scope(session.event_occurrence.event, member)
    return True


def _monthly_next(value):
    month_index = value.year * 12 + value.month
    year, month = divmod(month_index, 12)
    month += 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def create_sessions(actor, data):
    organization = TenantContext.get()
    if organization is None:
        raise PermissionDenied('Aucune organisation active.')
    church = Church.objects.filter(
        church_id=data['church_id'],
        organization=organization,
        is_active=True,
    ).first()
    if church is None:
        raise ValidationError({'church_id': 'Église introuvable ou hors de votre organisation.'})
    campus = None
    if data.get('campus_id'):
        campus = Campus.objects.filter(
            campus_id=data['campus_id'],
            church=church,
            is_active=True,
        ).first()
        if campus is None:
            raise ValidationError({'campus_id': 'Campus introuvable ou hors de cette église.'})

    session_type = None
    if data.get('session_type_id'):
        session_type = AttendanceSessionType.objects.filter(
            pk=data['session_type_id'],
            organization=organization,
            is_active=True,
        ).first()
        if session_type is None:
            raise ValidationError({'session_type_id': 'Type de séance introuvable.'})
    ministry = None
    tribe = None
    if data.get('ministry_id'):
        from apps.ministries.models import Ministry

        ministry = Ministry.objects.filter(pk=data['ministry_id'], church=church).first()
        if ministry is None:
            raise ValidationError({'ministry_id': 'Ministère introuvable dans cette église.'})
    if data.get('tribe_id'):
        from apps.ministries.models import Tribe

        tribe = Tribe.objects.filter(pk=data['tribe_id'], church=church).first()
        if tribe is None:
            raise ValidationError({'tribe_id': 'Tribu introuvable dans cette église.'})
    rule = data.get('population_rule') or (
        session_type.population_rule if session_type else PopulationRule.ALL_MEMBERS
    )
    allowed_methods = _validate_methods(
        data.get('allowed_methods') or (session_type.default_methods if session_type else [SessionMethod.MANUAL, SessionMethod.LIST])
    )
    tolerance = data.get('tolerance_minutes')
    if tolerance is None:
        tolerance = session_type.tolerance_minutes if session_type else 15
    if tolerance > 240:
        raise ValidationError({'tolerance_minutes': 'La tolérance ne peut pas dépasser 240 minutes.'})
    if data.get('event_occurrence_id'):
        event_occurrence = EventOccurrence.objects.select_related('event').filter(
            pk=data['event_occurrence_id'],
            event__organization=organization,
        ).first()
        if event_occurrence is None:
            raise ValidationError({'event_occurrence_id': 'Occurrence d’événement introuvable dans cette organisation.'})
        if event_occurrence.event.church_id and event_occurrence.event.church_id != church.church_id:
            raise ValidationError({'event_occurrence_id': 'L’événement ne correspond pas à cette église.'})
        if event_occurrence.event.campus_id and event_occurrence.event.campus_id != (campus.campus_id if campus else None):
            raise ValidationError({'event_occurrence_id': 'L’événement ne correspond pas à ce campus.'})
        linked_session = get_or_create_event_attendance_session(event_occurrence)
        if linked_session is None:
            raise ValidationError({'event_occurrence_id': 'Cette occurrence ne possède pas un périmètre pris en charge.'})
        return [linked_session]
    else:
        event_occurrence = None

    frequency = data.get('recurrence_frequency', RecurrenceFrequency.NONE)
    if frequency not in RecurrenceFrequency.values:
        raise ValidationError({'recurrence_frequency': 'Fréquence de récurrence invalide.'})
    recurrence_until = data.get('recurrence_until')
    horizon_weeks = settings.ATTENDANCE_RECURRENCE_HORIZON_WEEKS
    if not 1 <= horizon_weeks <= 52:
        raise ValidationError('ATTENDANCE_RECURRENCE_HORIZON_WEEKS doit être compris entre 1 et 52.')
    if frequency != RecurrenceFrequency.NONE and recurrence_until is None:
        recurrence_until = data['starts_at'].date() + timedelta(weeks=horizon_weeks)
    if frequency == RecurrenceFrequency.NONE and recurrence_until:
        raise ValidationError({'recurrence_until': 'Une date de fin exige une fréquence de récurrence.'})
    if recurrence_until and recurrence_until < data['starts_at'].date():
        raise ValidationError({'recurrence_until': 'La date de fin doit suivre la date de la séance.'})

    series_id = uuid.uuid4() if frequency != RecurrenceFrequency.NONE else None
    end_delta = data['ends_at'] - data['starts_at']
    occurrences = []
    starts_at = data['starts_at']
    with transaction.atomic():
        while True:
            session = AttendanceSession.objects.create(
                organization=organization,
                church=church,
                campus=campus,
                session_type=session_type,
                ministry=ministry,
                tribe=tribe,
                event_occurrence=event_occurrence if not occurrences else None,
                title=data['title'],
                starts_at=starts_at,
                ends_at=starts_at + end_delta,
                allowed_methods=allowed_methods,
                tolerance_minutes=tolerance,
                recurrence_frequency=frequency,
                recurrence_series_id=series_id,
                recurrence_until=recurrence_until if frequency != RecurrenceFrequency.NONE else None,
                created_by=actor,
            )
            population = _eligible_members(session, rule, data.get('member_ids'))
            ExpectedParticipant.objects.bulk_create([
                ExpectedParticipant(
                    session=session,
                    member=member,
                    first_name=member.first_name,
                    last_name=member.last_name,
                )
                for member in population
            ])
            record_audit(organization, 'SESSION_CREATED', actor, session, {
                'population_rule': rule,
                'expected_count': len(population),
                'allowed_methods': allowed_methods,
            })
            occurrences.append(session)
            if frequency == RecurrenceFrequency.NONE or starts_at.date() >= recurrence_until:
                break
            next_start = starts_at + timedelta(days=7) if frequency == RecurrenceFrequency.WEEKLY else _monthly_next(starts_at)
            if next_start.date() > recurrence_until or len(occurrences) >= horizon_weeks + 1:
                break
            starts_at = next_start
    return occurrences


@transaction.atomic
def transition_session(session, action, actor, reason=''):
    locked = AttendanceSession.objects.select_for_update().select_related('organization').get(pk=session.pk)
    if action == 'open':
        if locked.status != SessionLifecycle.PLANNED:
            raise ValidationError('Seule une séance planifiée peut être ouverte.')
        locked.status = SessionLifecycle.IN_PROGRESS
        locked.opened_at = timezone.now()
        if SessionMethod.QR in locked.allowed_methods:
            raw_token = secrets.token_urlsafe(32)
            locked.qr_token_hash = token_hash(raw_token)
        else:
            raw_token = None
    elif action == 'close':
        if locked.status != SessionLifecycle.IN_PROGRESS:
            if locked.status == SessionLifecycle.CLOSED:
                return locked, None
            raise ValidationError('Seule une séance en cours peut être clôturée.')
        timestamp = timezone.now()
        expected = locked.expected_participants.filter(expected_for_absence=True).select_related('attendance')
        for participant in expected:
            if not hasattr(participant, 'attendance'):
                AttendanceRecord.objects.create(expected_participant=participant, status=AttendanceStatus.ABSENT)
        locked.status = SessionLifecycle.CLOSED
        locked.closed_at = timestamp
        raw_token = None
    elif action == 'cancel':
        if locked.status == SessionLifecycle.CANCELLED:
            return locked, None
        if locked.status == SessionLifecycle.CLOSED and not reason.strip():
            raise ValidationError({'reason': 'Le motif est obligatoire pour annuler une séance clôturée.'})
        if locked.status not in (SessionLifecycle.PLANNED, SessionLifecycle.IN_PROGRESS, SessionLifecycle.CLOSED):
            raise ValidationError('Transition vers Annulée interdite.')
        locked.status = SessionLifecycle.CANCELLED
        raw_token = None
    elif action == 'reopen':
        if locked.status != SessionLifecycle.CLOSED or not reason.strip():
            raise ValidationError({'reason': 'Seule une séance clôturée peut être rouverte et un motif est obligatoire.'})
        locked.status = SessionLifecycle.IN_PROGRESS
        locked.closed_at = None
        raw_token = None
    else:
        raise ValidationError('Action de séance invalide.')
    locked.save()
    record_audit(locked.organization, f'SESSION_{action.upper()}', actor, locked, {'reason': reason.strip()} if reason else {})
    return locked, raw_token


@transaction.atomic
def record_attendance(session, participant, actor, method, *, raw_qr_token='', now=None):
    locked_session = AttendanceSession.objects.select_for_update().get(pk=session.pk)
    if locked_session.status != SessionLifecycle.IN_PROGRESS:
        raise ValidationError('Le pointage est autorisé uniquement pendant une séance en cours.')
    if method not in locked_session.allowed_methods:
        raise ValidationError('Cette méthode de pointage n’est pas autorisée pour la séance.')
    if method == SessionMethod.QR:
        provided = token_hash(raw_qr_token) if raw_qr_token else ''
        if not locked_session.qr_token_hash or not secrets.compare_digest(locked_session.qr_token_hash, provided):
            raise PermissionDenied('Le code QR de cette séance est invalide.')
    expected = ExpectedParticipant.objects.select_related('member', 'visitor').filter(
        pk=participant.pk,
        session=locked_session,
    ).first()
    if expected is None:
        raise ValidationError('Cette personne ne figure pas dans la liste attendue de la séance.')
    if not expected.expected_for_absence:
        raise ValidationError('Cette personne n’est plus attendue à cette séance.')
    if expected.member_id and not member_is_in_session_scope(locked_session, expected.member):
        raise PermissionDenied('Le participant est hors du périmètre autorisé.')
    prior = AttendanceRecord.objects.filter(expected_participant=expected).first()
    if prior:
        return prior, False
    now = now or timezone.now()
    status = (
        AttendanceStatus.LATE
        if now > locked_session.starts_at + timedelta(minutes=locked_session.tolerance_minutes)
        else AttendanceStatus.PRESENT
    )
    try:
        with transaction.atomic():
            attendance = AttendanceRecord.objects.create(
                expected_participant=expected,
                status=status,
                checked_in_at=now,
                method=method,
                recorded_by=actor,
            )
    except IntegrityError:
        attendance = AttendanceRecord.objects.get(expected_participant=expected)
        return attendance, False
    record_audit(locked_session.organization, 'ATTENDANCE_RECORDED', actor, locked_session, {
        'participant_id': str(expected.id),
        'method': method,
        'status': status,
    })
    return attendance, True


@transaction.atomic
def create_visitor(session, actor, data):
    locked_session = AttendanceSession.objects.select_for_update().get(pk=session.pk)
    if locked_session.status != SessionLifecycle.PLANNED:
        raise ValidationError('Les visiteurs ne peuvent être ajoutés qu’avant l’ouverture de la séance.')
    organization = locked_session.organization
    if data.get('visitor_id'):
        visitor = AttendanceVisitor.objects.filter(
            pk=data['visitor_id'],
            organization=organization,
            reusable=True,
        ).select_related('person').first()
        if visitor is None:
            raise ValidationError({'visitor_id': 'Fiche visiteur réutilisable introuvable.'})
    else:
        person = Person.objects.create(
            organization=organization,
            first_name=data['first_name'].strip(),
            last_name=data['last_name'].strip(),
            email=data.get('email') or None,
            phone=data.get('phone') or None,
        )
        visitor = AttendanceVisitor.objects.create(
            organization=organization,
            person=person,
            reusable=bool(data.get('reusable', False)),
            created_by=actor,
        )
    try:
        expected = ExpectedParticipant.objects.create(
            session=locked_session,
            visitor=visitor,
            first_name=visitor.person.first_name,
            last_name=visitor.person.last_name,
            added_by=actor,
            addition_reason=data.get('reason', '').strip(),
        )
    except IntegrityError as error:
        raise ValidationError('Ce visiteur figure déjà parmi les personnes attendues.') from error
    record_audit(organization, 'VISITOR_ADDED', actor, locked_session, {'participant_id': str(expected.id)})
    return expected


@transaction.atomic
def correct_attendance(attendance, actor, new_status, reason):
    if not reason or not reason.strip():
        raise ValidationError({'reason': 'Le motif de correction est obligatoire.'})
    locked = AttendanceRecord.objects.select_for_update().select_related(
        'expected_participant__session',
    ).get(pk=attendance.pk)
    session = locked.expected_participant.session
    if session.status not in (SessionLifecycle.CLOSED, SessionLifecycle.IN_PROGRESS):
        raise ValidationError('Le statut ne peut pas être corrigé pour cette séance.')
    if new_status not in AttendanceStatus.values:
        raise ValidationError({'status': 'Statut d’assiduité invalide.'})
    if locked.status == new_status:
        raise ValidationError('La présence possède déjà ce statut.')
    AttendanceCorrection.objects.create(
        attendance=locked,
        previous_status=locked.status,
        new_status=new_status,
        reason=reason.strip(),
        actor=actor,
    )
    locked.status = new_status
    locked.save(update_fields=['status', 'updated_at'])
    record_audit(session.organization, 'ATTENDANCE_CORRECTED', actor, session, {
        'attendance_id': str(locked.id),
        'previous_status': AttendanceCorrection.objects.filter(attendance=locked).latest('created_at').previous_status,
        'new_status': new_status,
        'reason': reason.strip(),
    })
    return locked


@transaction.atomic
def submit_excuse(attendance, actor, reason):
    if not reason or not reason.strip():
        raise ValidationError({'reason': 'Le motif de justification est obligatoire.'})
    locked = AttendanceRecord.objects.select_for_update().select_related('expected_participant__session').get(pk=attendance.pk)
    if locked.status != AttendanceStatus.ABSENT:
        raise ValidationError('Une justification ne peut être soumise que pour une absence.')
    if locked.excuses.filter(status=ExcuseStatus.PENDING).exists():
        raise ValidationError('Une justification est déjà en attente pour cette absence.')
    excuse = AbsenceExcuse.objects.create(attendance=locked, reason=reason.strip(), submitted_by=actor)
    record_audit(locked.expected_participant.session.organization, 'EXCUSE_SUBMITTED', actor, locked.expected_participant.session)
    return excuse


@transaction.atomic
def decide_excuse(excuse, actor, approve):
    locked = AbsenceExcuse.objects.select_for_update().select_related('attendance__expected_participant__session').get(pk=excuse.pk)
    if locked.status != ExcuseStatus.PENDING:
        raise ValidationError('Cette justification a déjà été traitée.')
    locked.status = ExcuseStatus.APPROVED if approve else ExcuseStatus.REJECTED
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.save(update_fields=['status', 'decided_by', 'decided_at'])
    if approve:
        locked.attendance.status = AttendanceStatus.EXCUSED
        locked.attendance.save(update_fields=['status', 'updated_at'])
    session = locked.attendance.expected_participant.session
    record_audit(session.organization, 'EXCUSE_APPROVED' if approve else 'EXCUSE_REJECTED', actor, session)
    return locked


@transaction.atomic
def record_early_departure(attendance, actor, departed_at, reason=''):
    locked = AttendanceRecord.objects.select_for_update().select_related('expected_participant__session').get(pk=attendance.pk)
    if locked.status not in (AttendanceStatus.PRESENT, AttendanceStatus.LATE):
        raise ValidationError('Le départ anticipé requiert une présence enregistrée.')
    if departed_at < locked.checked_in_at:
        raise ValidationError({'departed_at': 'L’heure de départ doit suivre le pointage.'})
    locked.status = AttendanceStatus.EARLY_DEPARTURE
    locked.departed_at = departed_at
    locked.departure_reason = reason.strip()
    locked.recorded_by = actor
    locked.save(update_fields=['status', 'departed_at', 'departure_reason', 'recorded_by', 'updated_at'])
    record_audit(
        locked.expected_participant.session.organization,
        'EARLY_DEPARTURE_RECORDED',
        actor,
        locked.expected_participant.session,
        {'attendance_id': str(locked.id), 'departed_at': departed_at.isoformat(), 'reason': reason.strip()},
    )
    return locked


def get_or_create_event_attendance_session(occurrence):
    event = occurrence.event
    church = event.church
    campus = event.campus
    if church is None and campus is not None:
        church = campus.church
    if church is None and event.scope != 'ORGANIZATION':
        return None
    starts_at, ends_at = occurrence.starts_at, occurrence.ends_at
    with transaction.atomic():
        session, created = AttendanceSession.objects.get_or_create(
            event_occurrence=occurrence,
            defaults={
                'organization': event.organization,
                'church': church,
                'campus': campus,
                'title': event.title,
                'starts_at': starts_at,
                'ends_at': ends_at,
                'status': (
                    SessionLifecycle.CANCELLED if occurrence.status == 'CANCELLED'
                    else SessionLifecycle.CLOSED if occurrence.status == 'COMPLETED'
                    else SessionLifecycle.IN_PROGRESS if occurrence.status == 'IN_PROGRESS'
                    else SessionLifecycle.PLANNED
                ),
                'allowed_methods': [SessionMethod.QR, SessionMethod.SELF, SessionMethod.LIST],
            },
        )
        if created:
            from apps.events.models import EventRegistration, RegistrationMode, RegistrationStatus
            from apps.events.services import member_is_in_event_scope

            members = Member.objects.filter(organization=event.organization, status=Member.Status.ACTIVE)
            if event.scope == 'CHURCH':
                members = members.filter(church=event.church)
            elif event.scope == 'CAMPUS':
                members = members.filter(campus=event.campus)
            elif event.included_churches.exists():
                members = members.filter(church__in=event.included_churches.all())
            registrations = EventRegistration.objects.filter(
                occurrence=occurrence,
                status=RegistrationStatus.CONFIRMED,
            ).select_related('member', 'person')
            if occurrence.registration_mode == RegistrationMode.REQUIRED:
                members = members.filter(
                    id__in=registrations.filter(member__isnull=False).values_list('member_id', flat=True),
                )
            expected_by_member = {}
            for member in members.distinct():
                if member_is_in_event_scope(event, member):
                    expected_by_member[member.id] = ExpectedParticipant(
                        session=session,
                        member=member,
                        first_name=member.first_name,
                        last_name=member.last_name,
                    )
            ExpectedParticipant.objects.bulk_create(expected_by_member.values())
            for registration in registrations:
                if registration.member_id:
                    participant = session.expected_participants.filter(member=registration.member).first()
                    if participant is None:
                        continue
                elif registration.person_id:
                    visitor, _ = AttendanceVisitor.objects.get_or_create(
                        organization=event.organization,
                        person=registration.person,
                        defaults={'reusable': False},
                    )
                    participant, _ = ExpectedParticipant.objects.get_or_create(
                        session=session,
                        visitor=visitor,
                        defaults={
                            'first_name': registration.person.first_name,
                            'last_name': registration.person.last_name,
                        },
                    )
            record_audit(event.organization, 'EVENT_OCCURRENCE_LINKED', session=session, detail={'occurrence_id': str(occurrence.id)})
        return session


@transaction.atomic
def sync_event_attendance(event_attendance, actor=None):
    occurrence = EventOccurrence.objects.select_related(
        'event__church', 'event__campus', 'event__organization',
    ).get(pk=event_attendance.occurrence_id)
    session = get_or_create_event_attendance_session(occurrence)
    if session is None:
        return None
    attendance = EventAttendance.objects.select_related('member', 'registration__person').get(pk=event_attendance.pk)
    prior = AttendanceRecord.objects.filter(event_attendance=attendance).first()
    if attendance.state != 'PRESENT':
        if prior and prior.status != AttendanceStatus.ABSENT:
            AttendanceCorrection.objects.create(
                attendance=prior,
                previous_status=prior.status,
                new_status=AttendanceStatus.ABSENT,
                reason='Pointage événement révoqué.',
                actor=actor,
            )
            prior.status = AttendanceStatus.ABSENT
            prior.save(update_fields=['status', 'updated_at'])
        return prior
    if attendance.member_id:
        participant = session.expected_participants.filter(member_id=attendance.member_id).first()
    elif attendance.registration_id and attendance.registration.person_id:
        visitor, _ = AttendanceVisitor.objects.get_or_create(
            organization=session.organization,
            person=attendance.registration.person,
            defaults={'reusable': False},
        )
        participant, _ = ExpectedParticipant.objects.get_or_create(
            session=session,
            visitor=visitor,
            defaults={
                'first_name': attendance.registration.person.first_name,
                'last_name': attendance.registration.person.last_name,
            },
        )
    else:
        return None
    if participant is None:
        return None
    record, _ = AttendanceRecord.objects.get_or_create(
        expected_participant=participant,
        defaults={
            'status': AttendanceStatus.PRESENT,
            'checked_in_at': attendance.checked_in_at,
            'method': SessionMethod.QR if attendance.source == 'REGISTRATION' else SessionMethod.SELF,
            'recorded_by': actor or attendance.checked_in_by,
            'event_attendance': attendance,
        },
    )
    return record


@transaction.atomic
def sync_event_registration(registration):
    from apps.events.models import RegistrationStatus
    from apps.events.services import member_is_in_event_scope

    registration = registration.__class__.objects.select_related(
        'occurrence__event__church',
        'occurrence__event__campus',
        'occurrence__event__organization',
        'member',
        'person',
    ).get(pk=registration.pk)
    session = get_or_create_event_attendance_session(registration.occurrence)
    if session is None:
        return None
    if registration.status != RegistrationStatus.CONFIRMED:
        expected = session.expected_participants.filter(
            Q(member_id=registration.member_id) if registration.member_id else Q(visitor__person_id=registration.person_id),
        ).first()
        if expected and not AttendanceRecord.objects.filter(expected_participant=expected).exists():
            expected.expected_for_absence = False
            expected.save(update_fields=['expected_for_absence'])
        return expected
    if registration.member_id:
        if not member_is_in_event_scope(registration.occurrence.event, registration.member):
            return None
        expected, _ = ExpectedParticipant.objects.get_or_create(
            session=session,
            member=registration.member,
            defaults={
                'first_name': registration.member.first_name,
                'last_name': registration.member.last_name,
            },
        )
        if not expected.expected_for_absence:
            expected.expected_for_absence = True
            expected.save(update_fields=['expected_for_absence'])
        return expected
    if registration.person_id:
        visitor, _ = AttendanceVisitor.objects.get_or_create(
            organization=session.organization,
            person=registration.person,
            defaults={'reusable': False},
        )
        expected, _ = ExpectedParticipant.objects.get_or_create(
            session=session,
            visitor=visitor,
            defaults={
                'first_name': registration.person.first_name,
                'last_name': registration.person.last_name,
            },
        )
        if not expected.expected_for_absence:
            expected.expected_for_absence = True
            expected.save(update_fields=['expected_for_absence'])
        return expected
    return None


def sync_event_occurrence_status(occurrence, actor=None):
    session = get_or_create_event_attendance_session(occurrence)
    if session is None:
        return None
    target = {
        'PUBLISHED': SessionLifecycle.PLANNED,
        'IN_PROGRESS': SessionLifecycle.IN_PROGRESS,
        'COMPLETED': SessionLifecycle.CLOSED,
        'CANCELLED': SessionLifecycle.CANCELLED,
    }.get(occurrence.status)
    if target is None or target == session.status:
        return session
    if target == SessionLifecycle.IN_PROGRESS and session.status == SessionLifecycle.PLANNED:
        session, _ = transition_session(session, 'open', actor)
    elif target == SessionLifecycle.CLOSED and session.status == SessionLifecycle.IN_PROGRESS:
        session, _ = transition_session(session, 'close', actor)
    elif target == SessionLifecycle.CANCELLED and session.status in (SessionLifecycle.PLANNED, SessionLifecycle.IN_PROGRESS):
        session, _ = transition_session(session, 'cancel', actor)
    else:
        session.status = target
        if target == SessionLifecycle.CLOSED:
            session.closed_at = timezone.now()
        session.save(update_fields=['status', 'closed_at', 'updated_at'])
        record_audit(session.organization, 'EVENT_SESSION_STATUS_SYNCED', actor, session)
    return session


def weekly_report(organization, start_date, end_date, church_id=None, session_type_id=None):
    all_sessions = AttendanceSession.objects.filter(
        organization=organization,
        starts_at__date__gte=start_date,
        starts_at__date__lte=end_date,
    )
    if church_id:
        all_sessions = all_sessions.filter(church_id=church_id)
    if session_type_id:
        all_sessions = all_sessions.filter(session_type_id=session_type_id)
    sessions = all_sessions.exclude(status=SessionLifecycle.CANCELLED)
    closed_sessions = sessions.filter(status=SessionLifecycle.CLOSED)
    aggregate = AttendanceRecord.objects.filter(
        expected_participant__session__in=closed_sessions,
    ).aggregate(
        expected=Count('id'),
        present=Count('id', filter=Q(status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE, AttendanceStatus.EARLY_DEPARTURE])),
        absent=Count('id', filter=Q(status=AttendanceStatus.ABSENT)),
        excused=Count('id', filter=Q(status=AttendanceStatus.EXCUSED)),
        late=Count('id', filter=Q(status=AttendanceStatus.LATE)),
        early_departure=Count('id', filter=Q(status=AttendanceStatus.EARLY_DEPARTURE)),
    )
    expected = aggregate['expected'] or 0
    return {
        'period': {'start': start_date, 'end': end_date},
        'sessions': {
            'planned': sessions.filter(status=SessionLifecycle.PLANNED).count(),
            'in_progress': sessions.filter(status=SessionLifecycle.IN_PROGRESS).count(),
            'closed': closed_sessions.count(),
            'cancelled': all_sessions.filter(status=SessionLifecycle.CANCELLED).count(),
        },
        'attendance': {
            **aggregate,
            'attendance_rate': round((aggregate['present'] or 0) / expected * 100, 2) if expected else 0,
            'expected': expected,
        },
    }


@transaction.atomic
def evaluate_attendance_alerts(organization, church_ids=None):
    today = timezone.localdate()
    created = 0
    policies = AttendancePolicy.objects.select_for_update().filter(organization=organization).select_related('church')
    if church_ids is not None:
        policies = policies.filter(church_id__in=church_ids)
    for policy in policies:
        last_eval_date = timezone.localtime(policy.last_evaluated_at).date() if policy.last_evaluated_at else None
        if policy.evaluation_frequency == AttendancePolicy.EvaluationFrequency.DAILY and last_eval_date == today:
            continue
        if policy.evaluation_frequency == AttendancePolicy.EvaluationFrequency.WEEKLY:
            this_week = today - timedelta(days=today.weekday())
            if last_eval_date and last_eval_date >= this_week:
                continue
        timezone_name = policy.church.fuseau_horaire or organization.fuseau_horaire or 'UTC'
        with timezone.override(ZoneInfo(timezone_name)):
            local_today = timezone.localdate()
            date_from = local_today - timedelta(weeks=policy.observation_weeks)
            sessions = AttendanceSession.objects.filter(
                organization=organization,
                church=policy.church,
                status=SessionLifecycle.CLOSED,
                starts_at__date__gte=date_from,
                starts_at__date__lte=local_today,
            )
            members = list(Member.objects.filter(
                organization=organization,
                church=policy.church,
                status=Member.Status.ACTIVE,
            ).select_related('user'))
            all_records = AttendanceRecord.objects.filter(
                expected_participant__session__in=sessions,
                expected_participant__member__in=members,
            ).select_related(
                'expected_participant__session',
                'expected_participant__member',
            ).order_by('-expected_participant__session__starts_at')
            by_member = defaultdict(list)
            for attendance in all_records:
                by_member[attendance.expected_participant.member_id].append(attendance)
            for member in members:
                records = by_member[member.id]
                considered = [
                    record for record in records
                    if policy.count_excused_absences or record.status != AttendanceStatus.EXCUSED
                ]
                consecutive = 0
                for record in considered:
                    if record.status == AttendanceStatus.ABSENT or (
                        policy.count_excused_absences and record.status == AttendanceStatus.EXCUSED
                    ):
                        consecutive += 1
                    else:
                        break
                presence_statuses = {
                    AttendanceStatus.PRESENT,
                    AttendanceStatus.LATE,
                    AttendanceStatus.EARLY_DEPARTURE,
                }
                denominator = len(considered)
                rate = (
                    sum(record.status in presence_statuses for record in considered) / denominator * 100
                    if denominator else None
                )
                rules = []
                if consecutive >= policy.consecutive_absences:
                    rules.append(('CONSECUTIVE_ABSENCES', {'count': consecutive, 'threshold': policy.consecutive_absences}))
                if rate is not None and rate < float(policy.minimum_attendance_rate):
                    rules.append(('LOW_ATTENDANCE_RATE', {
                        'rate': round(rate, 2),
                        'threshold': float(policy.minimum_attendance_rate),
                        'observation_weeks': policy.observation_weeks,
                    }))
                for rule_key, details in rules:
                    active = AttendanceAlert.objects.filter(
                        organization=organization,
                        member=member,
                        rule_key=rule_key,
                    ).exclude(status='RESOLVED').exists()
                    if active:
                        continue
                    previous_resolution = AttendanceAlert.objects.filter(
                        organization=organization,
                        member=member,
                        rule_key=rule_key,
                        status='RESOLVED',
                    ).order_by('-resolved_at').first()
                    if previous_resolution and previous_resolution.resolved_at and not any(
                        record.expected_participant.session.starts_at > previous_resolution.resolved_at
                        for record in records
                    ):
                        continue
                    try:
                        with transaction.atomic():
                            AttendanceAlert.objects.create(
                                organization=organization,
                                member=member,
                                rule_key=rule_key,
                                trigger_detail=details,
                            )
                    except IntegrityError:
                        continue
                    record_audit(organization, 'ALERT_CREATED', detail={
                        'member_id': str(member.id),
                        'rule_key': rule_key,
                    })
                    created += 1
        policy.last_evaluated_at = timezone.now()
        policy.save(update_fields=['last_evaluated_at', 'updated_at'])
    return created
