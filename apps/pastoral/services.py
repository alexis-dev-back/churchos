from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.core.models import Campus, Church, OrganizationMembership, UserRoleAssignment
from apps.members.models import Member
from apps.people.models import Person

from .models import (
    PastoralAssignmentHistory,
    PastoralAuditEvent,
    PastoralCase,
    PastoralCaseAccess,
    PastoralInteraction,
    PastoralInteractionShare,
    PastoralNote,
    PastoralNoteRevision,
    PastoralNoteShare,
    PastoralStatusHistory,
    PastoralWorkflow,
    PastoralTask,
    PrayerRequest,
    PrayerRequestHistory,
    PrayerRequestShare,
    DEFAULT_STATUSES,
    DEFAULT_TRANSITIONS,
)


def has_scoped_permission(user, organization, church, campus, permission_code):
    if not user or not user.is_authenticated or organization is None or church is None:
        return False
    now = timezone.now()
    assignments = UserRoleAssignment.objects.filter(
        organization=organization,
        membership__organization=organization,
        membership__user=user,
        membership__is_active=True,
        role__organization=organization,
        role__is_active=True,
        role__permission_links__permission__code=permission_code,
        role__permission_links__permission__is_active=True,
        starts_at__lte=now,
        revoked_at__isnull=True,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    return assignments.filter(scope=UserRoleAssignment.Scope.ORGANIZATION).exists() or assignments.filter(
        scope=UserRoleAssignment.Scope.CHURCH,
        church=church,
    ).exists() or (
        campus is not None and assignments.filter(
            scope=UserRoleAssignment.Scope.CAMPUS,
            church=church,
            campus=campus,
        ).exists()
    )


def has_organization_permission(user, organization, permission_code):
    if not user or not user.is_authenticated or organization is None:
        return False
    now = timezone.now()
    return UserRoleAssignment.objects.filter(
        organization=organization,
        membership__organization=organization,
        membership__user=user,
        membership__is_active=True,
        role__organization=organization,
        role__is_active=True,
        role__permission_links__permission__code=permission_code,
        role__permission_links__permission__is_active=True,
        scope=UserRoleAssignment.Scope.ORGANIZATION,
        starts_at__lte=now,
        revoked_at__isnull=True,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now)).exists()


def has_any_scoped_permission(user, organization, permission_code):
    churches = organization.churches.filter(is_active=True)
    if any(
        has_scoped_permission(user, organization, church, None, permission_code)
        for church in churches
    ):
        return True
    return any(
        has_scoped_permission(user, organization, campus.church, campus, permission_code)
        for campus in Campus.objects.filter(
            church__organization=organization,
            church__is_active=True,
            is_active=True,
        ).select_related('church')
    )


def user_in_organization(user, organization):
    return bool(user and user.is_authenticated and OrganizationMembership.objects.filter(
        user=user,
        organization=organization,
        is_active=True,
        organization__is_active=True,
    ).exists())


def accessible_case_queryset(user, organization, permission_code='pastoral.case.read'):
    assignments = UserRoleAssignment.objects.filter(
        organization=organization,
        membership__organization=organization,
        membership__user=user,
        membership__is_active=True,
        role__is_active=True,
        role__permission_links__permission__code=permission_code,
        role__permission_links__permission__is_active=True,
        starts_at__lte=timezone.now(),
        revoked_at__isnull=True,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
    org_scope = assignments.filter(scope=UserRoleAssignment.Scope.ORGANIZATION).exists()
    church_ids = set(assignments.filter(
        scope=UserRoleAssignment.Scope.CHURCH,
    ).values_list('church_id', flat=True))
    campus_ids = set(assignments.filter(
        scope=UserRoleAssignment.Scope.CAMPUS,
    ).values_list('campus_id', flat=True))
    scoped = Q()
    if org_scope:
        scoped = Q(organization=organization)
    else:
        if church_ids:
            scoped |= Q(church_id__in=church_ids)
        if campus_ids:
            scoped |= Q(campus_id__in=campus_ids)
    return PastoralCase.objects.filter(
        scoped,
        organization=organization,
    ).filter(
        Q(assigned_to=user)
        | Q(access_grants__user=user)
        | Q(tasks__assigned_to=user)
        | Q(assigned_to__isnull=True),
    ).distinct().select_related('church', 'campus', 'member', 'person', 'assigned_to', 'created_by')


def can_access_case(user, case, permission_code='pastoral.case.read', edit=False):
    if not has_scoped_permission(user, case.organization, case.church, case.campus, permission_code):
        return False
    if case.sensitivity == PastoralCase.Sensitivity.RESTRICTED and not has_scoped_permission(
        user, case.organization, case.church, case.campus, 'pastoral.case.restricted.read',
    ):
        return False
    if case.sensitivity == PastoralCase.Sensitivity.VERY_RESTRICTED and not has_scoped_permission(
        user, case.organization, case.church, case.campus, 'pastoral.case.very_restricted.read',
    ):
        return False
    if case.assigned_to_id == user.id:
        return True
    if case.created_by_id == user.id:
        return True
    access = PastoralCaseAccess.objects.filter(case=case, user=user).first()
    if access and (not edit or access.access_level == PastoralCaseAccess.AccessLevel.EDIT):
        return True
    if (
        case.assigned_to_id is None
        and permission_code in {'pastoral.case.read', 'pastoral.case.assign'}
        and has_scoped_permission(user, case.organization, case.church, case.campus, 'pastoral.case.assign')
    ):
        return True
    return not edit and case.tasks.filter(assigned_to=user).exists()


def require_case_access(user, case, permission_code='pastoral.case.read', edit=False):
    if not can_access_case(user, case, permission_code, edit):
        raise NotFound('Dossier pastoral introuvable.')


def get_workflow(organization):
    workflow, _ = PastoralWorkflow.objects.get_or_create(organization=organization)
    return workflow


def record_audit(organization, actor, action, object_type, object_id='', church=None, result='SUCCESS', metadata=None):
    return PastoralAuditEvent.objects.create(
        organization=organization,
        church=church,
        actor=actor,
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        result=result,
        metadata=metadata or {},
    )


def validate_location(organization, church_id, campus_id=None):
    church = Church.objects.filter(
        church_id=church_id,
        organization=organization,
        is_active=True,
    ).first()
    if church is None:
        raise ValidationError({'church_id': 'Église introuvable ou hors de votre organisation.'})
    campus = None
    if campus_id:
        campus = Campus.objects.filter(
            campus_id=campus_id,
            church=church,
            is_active=True,
        ).first()
        if campus is None:
            raise ValidationError({'campus_id': 'Campus introuvable ou hors de cette église.'})
    return church, campus


def validate_subject(organization, church, campus, member_id=None, person_id=None):
    if bool(member_id) == bool(person_id):
        raise ValidationError('Sélectionnez exactement un membre ou une personne.')
    if member_id:
        member = Member.objects.filter(
            pk=member_id,
            organization=organization,
            church=church,
        ).first()
        if member is None or (campus and member.campus_id != campus.campus_id):
            raise ValidationError({'member_id': 'Membre introuvable ou hors du périmètre sélectionné.'})
        return member, None
    person = Person.objects.filter(pk=person_id, organization=organization).first()
    if person is None:
        raise ValidationError({'person_id': 'Personne introuvable dans cette organisation.'})
    return None, person


@transaction.atomic
def create_case(*, actor, organization, data):
    church, campus = validate_location(organization, data['church_id'], data.get('campus_id'))
    if not has_scoped_permission(actor, organization, church, campus, 'pastoral.case.create'):
        raise PermissionDenied('Vous ne pouvez pas créer un dossier dans ce périmètre.')
    member, person = validate_subject(
        organization,
        church,
        campus,
        data.get('member_id'),
        data.get('person_id'),
    )
    status_code = data.get('status', 'NEW')
    workflow = get_workflow(organization)
    if status_code not in {item['code'] for item in workflow.statuses if isinstance(item, dict)}:
        raise ValidationError({'status': 'Statut non configuré pour cette organisation.'})
    assigned_to = data.get('assigned_to')
    if assigned_to and not user_in_organization(assigned_to, organization):
        raise ValidationError({'assigned_to': 'Le responsable doit appartenir à cette organisation.'})
    if assigned_to and not has_scoped_permission(
        assigned_to, organization, church, campus, 'pastoral.case.read',
    ):
        raise ValidationError({'assigned_to': 'Le responsable doit disposer d’un accès pastoral dans ce périmètre.'})
    case = PastoralCase.objects.create(
        organization=organization,
        church=church,
        campus=campus,
        member=member,
        person=person,
        title=data['title'],
        category=data.get('category', ''),
        source=data.get('source', ''),
        status=status_code,
        sensitivity=data.get('sensitivity', PastoralCase.Sensitivity.STANDARD),
        priority=data.get('priority', PastoralCase.Priority.NORMAL),
        assigned_to=assigned_to,
        next_due_at=data.get('next_due_at'),
        created_by=actor,
    )
    if assigned_to:
        PastoralAssignmentHistory.objects.create(case=case, new_user=assigned_to, actor=actor)
    PastoralCaseAccess.objects.create(
        case=case,
        user=actor,
        access_level=PastoralCaseAccess.AccessLevel.EDIT,
        source=PastoralCaseAccess.Source.CREATOR,
        granted_by=actor,
    )
    PastoralStatusHistory.objects.create(
        case=case,
        previous_status='',
        new_status=status_code,
        actor=actor,
        reason='Création du dossier',
    )
    record_audit(organization, actor, 'CASE_CREATED', 'PastoralCase', case.pk, church)
    return case


@transaction.atomic
def assign_case(*, actor, case, assignee, reason):
    if not has_scoped_permission(actor, case.organization, case.church, case.campus, 'pastoral.case.assign'):
        raise NotFound('Dossier pastoral introuvable.')
    if assignee is not None and not user_in_organization(assignee, case.organization):
        raise ValidationError({'assigned_to': 'Le responsable doit appartenir à cette organisation.'})
    if assignee is not None and not has_scoped_permission(
        assignee, case.organization, case.church, case.campus, 'pastoral.case.read',
    ):
        raise ValidationError({'assigned_to': 'Le responsable doit disposer d’un accès pastoral dans ce périmètre.'})
    if case.assigned_to_id == getattr(assignee, 'id', None):
        raise ValidationError({'assigned_to': 'Ce responsable est déjà attribué au dossier.'})
    previous = case.assigned_to
    if previous:
        PastoralCaseAccess.objects.filter(
            case=case,
            user=previous,
            source=PastoralCaseAccess.Source.ASSIGNMENT,
        ).delete()
    case.assigned_to = assignee
    case.save(update_fields=['assigned_to', 'updated_at'])
    PastoralAssignmentHistory.objects.create(
        case=case, previous_user=previous, new_user=assignee, actor=actor, reason=reason,
    )
    if assignee:
        grant, created = PastoralCaseAccess.objects.get_or_create(
            case=case,
            user=assignee,
            defaults={
                'access_level': PastoralCaseAccess.AccessLevel.EDIT,
                'source': PastoralCaseAccess.Source.ASSIGNMENT,
                'granted_by': actor,
            },
        )
        if not created and grant.source == PastoralCaseAccess.Source.ASSIGNMENT:
            grant.access_level = PastoralCaseAccess.AccessLevel.EDIT
            grant.granted_by = actor
            grant.save(update_fields=['access_level', 'granted_by'])
    record_audit(
        case.organization, actor, 'CASE_ASSIGNED', 'PastoralCase', case.pk, case.church,
        metadata={'previous_user_id': str(previous.pk) if previous else None,
                  'new_user_id': str(assignee.pk) if assignee else None},
    )
    return case


@transaction.atomic
def transition_case(*, actor, case, new_status, reason, reopen_due_at=None):
    permission_code = (
        'pastoral.case.reopen' if case.status == 'CLOSED'
        else 'pastoral.case.close' if new_status == 'CLOSED'
        else 'pastoral.case.transition'
    )
    if not can_access_case(actor, case, permission_code, edit=True):
        raise NotFound('Dossier pastoral introuvable.')
    if not reason.strip():
        raise ValidationError({'reason': 'Un motif est requis.'})
    workflow = get_workflow(case.organization)
    statuses = {item['code']: item for item in workflow.statuses if isinstance(item, dict)}
    if new_status not in statuses:
        raise ValidationError({'status': 'Statut non configuré.'})
    if f'{case.status}>{new_status}' not in workflow.transitions:
        raise ValidationError({'status': 'Cette transition n’est pas autorisée.'})
    if case.status == 'CLOSED':
        if new_status != 'IN_PROGRESS':
            raise ValidationError({'status': 'Un dossier clôturé ne peut être réouvert qu’en cours.'})
        if reopen_due_at is None:
            raise ValidationError({'next_due_at': 'Une nouvelle échéance est requise pour la réouverture.'})
    target_terminal = bool(statuses[new_status].get('terminal'))
    if target_terminal and workflow.block_close_with_open_items:
        if case.tasks.exclude(status__in=[PastoralTask.Status.DONE, PastoralTask.Status.CANCELLED]).exists():
            raise ValidationError({'status': 'Terminez ou annulez les tâches ouvertes avant la clôture.'})
        if case.interactions.filter(status__in=[
            PastoralInteraction.Status.PLANNED,
            PastoralInteraction.Status.CONFIRMED,
            PastoralInteraction.Status.RESCHEDULED,
        ]).exists():
            raise ValidationError({'status': 'Terminez ou annulez les rendez-vous ouverts avant la clôture.'})
    previous = case.status
    case.status = new_status
    case.closed_at = timezone.now() if target_terminal else None
    if reopen_due_at is not None:
        case.next_due_at = reopen_due_at
    case.save(update_fields=['status', 'closed_at', 'next_due_at', 'updated_at'])
    PastoralStatusHistory.objects.create(
        case=case,
        previous_status=previous,
        new_status=new_status,
        actor=actor,
        reason=reason,
    )
    record_audit(
        case.organization,
        actor,
        'CASE_REOPENED' if previous == 'CLOSED' else 'CASE_TRANSITIONED',
        'PastoralCase',
        case.pk,
        case.church,
        metadata={'previous_status': previous, 'new_status': new_status},
    )
    return case


def can_access_note(user, note, edit=False):
    case = note.case
    if not can_access_case(user, case, 'pastoral.case.read'):
        return False
    if note.author_id == user.id:
        return not edit or not case.closed_at
    share = PastoralNoteShare.objects.filter(note=note, user=user).first()
    if not share or (edit and not share.can_edit):
        return False
    if note.sensitivity == PastoralNote.Sensitivity.RESTRICTED:
        if not has_scoped_permission(user, case.organization, case.church, case.campus, 'pastoral.note.restricted.read'):
            return False
    return not edit or not case.closed_at


@transaction.atomic
def revise_note(*, actor, note, content, reason):
    if not can_access_note(actor, note, edit=True) or not has_scoped_permission(
        actor, note.case.organization, note.case.church, note.case.campus, 'pastoral.note.update',
    ):
        raise NotFound('Note pastorale introuvable.')
    if not reason.strip():
        raise ValidationError({'reason': 'Un motif de correction est requis.'})
    PastoralNoteRevision.objects.create(note=note, content=note.content, actor=actor, reason=reason)
    note.content = content
    note.save(update_fields=['content', 'updated_at'])
    record_audit(note.case.organization, actor, 'NOTE_UPDATED', 'PastoralNote', note.pk, note.case.church)
    return note


def require_scoped_permission(actor, organization, church, campus, permission_code):
    if not has_scoped_permission(actor, organization, church, campus, permission_code):
        raise PermissionDenied('Vous ne disposez pas de la permission requise pour ce périmètre.')


def accessible_prayer_requests(user, organization):
    return PrayerRequest.objects.filter(
        Q(assigned_to=user) | Q(shares__user=user),
        organization=organization,
    ).distinct().select_related('church', 'campus', 'member', 'person', 'assigned_to')


def can_access_prayer(user, request, permission_code='pastoral.prayer.read'):
    if not has_scoped_permission(user, request.organization, request.church, request.campus, permission_code):
        return False
    if request.sensitivity == PastoralCase.Sensitivity.RESTRICTED and not has_scoped_permission(
        user, request.organization, request.church, request.campus, 'pastoral.prayer.restricted.read',
    ):
        return False
    if request.sensitivity == PastoralCase.Sensitivity.VERY_RESTRICTED and not has_scoped_permission(
        user, request.organization, request.church, request.campus, 'pastoral.prayer.very_restricted.read',
    ):
        return False
    return request.assigned_to_id == user.id or request.shares.filter(user=user).exists()


def can_read_interaction(user, interaction):
    case = interaction.case
    if not can_access_case(user, case, 'pastoral.case.read'):
        return False
    if interaction.sensitivity == PastoralInteraction.Sensitivity.CASE_TEAM:
        return True
    return (
        interaction.created_by_id == user.id
        or interaction.shares.filter(user=user).exists()
        or has_scoped_permission(
            user, case.organization, case.church, case.campus, 'pastoral.interaction.restricted.read',
        )
    )


@transaction.atomic
def create_prayer_request(*, actor, organization, data):
    church, campus = validate_location(organization, data['church_id'], data.get('campus_id'))
    require_scoped_permission(actor, organization, church, campus, 'pastoral.prayer.create')
    anonymous = data.get('is_anonymous', False)
    member = person = None
    if not anonymous:
        member, person = validate_subject(
            organization, church, campus, data.get('member_id'), data.get('person_id'),
        )
    assignee = data.get('assigned_to')
    if assignee and not user_in_organization(assignee, organization):
        raise ValidationError({'assigned_to': 'Le responsable doit appartenir à cette organisation.'})
    linked_case = data.get('linked_case')
    if linked_case and (
        linked_case.organization_id != organization.tenant_id
        or linked_case.church_id != church.church_id
        or not can_access_case(actor, linked_case, 'pastoral.case.read')
    ):
        raise ValidationError({'linked_case': 'Dossier introuvable ou hors du périmètre autorisé.'})
    item = PrayerRequest.objects.create(
        organization=organization, church=church, campus=campus, person=person, member=member,
        is_anonymous=anonymous, category=data.get('category', ''), content=data['content'],
        sensitivity=data.get('sensitivity', PastoralCase.Sensitivity.RESTRICTED),
        assigned_to=assignee, linked_case=linked_case, created_by=actor,
    )
    PrayerRequestShare.objects.create(request=item, user=actor, shared_by=actor)
    if assignee:
        PrayerRequestShare.objects.get_or_create(request=item, user=assignee, defaults={'shared_by': actor})
    PrayerRequestHistory.objects.create(
        request=item,
        new_status=item.status,
        new_assignee=item.assigned_to,
        actor=actor,
        reason='Création de la demande',
    )
    record_audit(organization, actor, 'PRAYER_CREATED', 'PrayerRequest', item.pk, church)
    return item
