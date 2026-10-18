import uuid

from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.ecodim.models import (
    EcodimAuditLog,
    EcodimChild,
    EcodimChildHandoff,
    EcodimClass,
    EcodimEnrollment,
    EcodimPickupAuthorization,
)
from apps.ecodim.serializers import EcodimChildSerializer
from apps.ecodim.validators import validate_identity_link


def scoped_church(request, church_id):
    from apps.ecodim.permissions import accessible_ecodim_churches
    from infrastructure.tenancy import TenantContext

    organization = TenantContext.get()
    churches = accessible_ecodim_churches(request.user, organization)
    if church_id:
        church = churches.filter(church_id=church_id).first()
        if not church:
            raise PermissionDenied('Cette église ne vous est pas accessible.')
        return church
    count = churches.count()
    if count != 1:
        raise ValidationError({'church_id': 'Indiquez une église accessible pour continuer.'})
    return churches.first()


@transaction.atomic
def create_ecodim_child(*, data, request, organization, church):
    serializer = EcodimChildSerializer(
        data=data,
        context={'organization': organization, 'church': church},
    )
    serializer.is_valid(raise_exception=True)
    child = serializer.save(
        organization=organization,
        church=church,
        created_by=request.user,
        reference=f'EC-{uuid.uuid4().hex[:10].upper()}',
    )
    EcodimAuditLog.objects.create(
        organization=organization,
        church=church,
        child=child,
        actor=request.user,
        action='CHILD_CREATED',
        detail={'reference': child.reference},
    )
    return child


@transaction.atomic
def update_ecodim_child(*, child, data, request, organization, church):
    serializer = EcodimChildSerializer(
        child,
        data=data,
        partial=True,
        context={'organization': organization, 'church': church},
    )
    serializer.is_valid(raise_exception=True)
    child = serializer.save()
    EcodimAuditLog.objects.create(
        organization=organization,
        church=church,
        child=child,
        actor=request.user,
        action='CHILD_UPDATED',
        detail={'fields': sorted(serializer.validated_data.keys())},
    )
    return child


@transaction.atomic
def enroll_child(*, child, ecodim_class, request):
    child = EcodimChild.objects.select_for_update().get(pk=child.pk)
    ecodim_class = EcodimClass.objects.select_for_update().get(pk=ecodim_class.pk)
    if child.organization_id != ecodim_class.organization_id or child.church_id != ecodim_class.church_id:
        raise ValidationError({'class_id': 'La classe doit appartenir à la même église que l’enfant.'})
    if child.status != EcodimChild.Status.ACTIVE or ecodim_class.status != EcodimClass.Status.ACTIVE:
        raise ValidationError('Seuls un enfant et une classe actifs peuvent être associés.')
    current = EcodimEnrollment.objects.filter(child=child, ended_at__isnull=True).first()
    if current and current.ecodim_class_id == ecodim_class.pk:
        return current
    enrolled_count = EcodimEnrollment.objects.filter(
        ecodim_class=ecodim_class,
        ended_at__isnull=True,
    ).count()
    if ecodim_class.capacity is not None and enrolled_count >= ecodim_class.capacity:
        raise ValidationError({'class_id': 'Cette classe a atteint sa capacité maximale.'})
    now = timezone.now()
    if current:
        current.ended_at = now
        current.save(update_fields=['ended_at'])
    enrollment = EcodimEnrollment.objects.create(
        child=child,
        ecodim_class=ecodim_class,
        assigned_by=request.user,
        started_at=now,
    )
    EcodimAuditLog.objects.create(
        organization=child.organization,
        church=child.church,
        child=child,
        actor=request.user,
        action='CHILD_ENROLLED',
        detail={'class_id': str(ecodim_class.pk), 'class_name': ecodim_class.name},
    )
    return enrollment


@transaction.atomic
def create_child_handoff(*, child, authorization_id, verification_note, request):
    child = EcodimChild.objects.select_for_update().get(pk=child.pk)
    if child.status != EcodimChild.Status.ACTIVE:
        raise ValidationError({'child': 'Seul un enfant actif peut être remis à un responsable.'})
    authorization = EcodimPickupAuthorization.objects.select_for_update().filter(
        pk=authorization_id,
        child=child,
    ).first()
    if authorization is None:
        raise ValidationError({'authorization_id': 'Cette autorisation ne concerne pas cet enfant.'})
    today = timezone.localdate()
    if (
        authorization.status != EcodimPickupAuthorization.Status.ACTIVE
        or authorization.valid_from > today
        or (authorization.valid_until and authorization.valid_until < today)
    ):
        raise ValidationError({'authorization_id': 'Cette autorisation de récupération n’est pas valide aujourd’hui.'})
    handoff = EcodimChildHandoff.objects.create(
        child=child,
        authorization=authorization,
        picked_up_by_name=authorization.name,
        verification_note=verification_note,
        confirmed_by=request.user,
    )
    EcodimAuditLog.objects.create(
        organization=child.organization,
        church=child.church,
        child=child,
        actor=request.user,
        action='CHILD_HANDED_OFF',
        detail={
            'authorization_id': str(authorization.pk),
            'picked_up_by_name': authorization.name,
        },
    )
    return handoff
