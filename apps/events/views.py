import secrets
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.events.models import (
    AttendanceState,
    ChurchEventAdministrator,
    CheckInMode,
    Event,
    EventAttendance,
    EventOccurrence,
    EventRegistration,
    EventScope,
    EventStatus,
    RegistrationStatus,
)
from apps.events.serializers import (
    AttendanceCorrectionSerializer,
    AttendanceSerializer,
    AuditLogSerializer,
    CheckInSerializer,
    EventOccurrenceSerializer,
    EventOccurrenceWriteSerializer,
    EventPatchSerializer,
    EventSeriesUpdateSerializer,
    EventSerializer,
    EventWriteSerializer,
    PublicRegistrationSerializer,
    RegistrationSerializer,
    ScanSerializer,
    SelfCheckInSerializer,
    StatusChangeSerializer,
)
from apps.events.services import (
    cancel_registration,
    checkin_registration,
    checkin_self,
    correct_attendance,
    create_registration,
    event_statistics,
    generate_occurrences,
    member_is_in_event_scope,
    record_event,
    respond_to_waitlist_offer,
    token_hash,
    transition_event,
    transition_occurrence,
)
from apps.members.models import Member
from apps.members.permissions import IsTenantAdmin
from apps.events.permissions import (
    IsEventManager,
    can_manage_event,
    is_organization_event_admin,
    managed_church_ids,
)
from apps.core.services.configuration_access import (
    configuration_church_ids,
    has_configuration_permission,
)
from infrastructure.tenancy import TenantContext


def _event_queryset(user=None):
    queryset = Event.objects.filter(organization=TenantContext.get()).select_related('church', 'campus').prefetch_related(
        'included_churches',
        'occurrences',
    )
    if user is not None and not is_organization_event_admin(user):
        if not has_configuration_permission(user, 'events.read', TenantContext.get()):
            church_ids = set(managed_church_ids(user)) | configuration_church_ids(
                user,
                TenantContext.get(),
                'events',
            )
            queryset = queryset.filter(
                Q(scope=EventScope.CHURCH, church_id__in=church_ids)
                | Q(scope=EventScope.CAMPUS, campus__church_id__in=church_ids)
            )
    return queryset


def _authorized_event(event_id, user=None):
    event = get_object_or_404(
        Event.objects.filter(organization=TenantContext.get()).select_related('church', 'campus').prefetch_related(
            'included_churches',
            'occurrences',
        ),
        pk=event_id,
    )
    if user is not None and not can_manage_event(user, event):
        organization = TenantContext.get()
        has_scoped_configuration_access = any(
            has_configuration_permission(
                user,
                permission_code,
                organization,
                church=event.church or (event.campus.church if event.campus_id else None),
                campus=event.campus,
            )
            for permission_code in ('events.read', 'events.update', 'events.cancel')
        )
        if not has_scoped_configuration_access:
            from django.http import Http404

            raise Http404
    return event


class EventListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]

    def get(self, request):
        queryset = _event_queryset(request.user)
        status_filter = request.query_params.get('status')
        scope_filter = request.query_params.get('scope')
        search = request.query_params.get('search', '').strip()
        starts_after = request.query_params.get('starts_after')
        starts_before = request.query_params.get('starts_before')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if scope_filter:
            queryset = queryset.filter(scope=scope_filter)
        if search:
            queryset = queryset.filter(Q(title__icontains=search) | Q(category__icontains=search))
        category = request.query_params.get('category')
        if category:
            queryset = queryset.filter(category__iexact=category)
        if starts_after:
            parsed_date = parse_date(starts_after)
            if parsed_date is None:
                raise ValidationError({'starts_after': 'Utilisez une date au format AAAA-MM-JJ.'})
            queryset = queryset.filter(occurrences__starts_at__date__gte=parsed_date)
        if starts_before:
            parsed_date = parse_date(starts_before)
            if parsed_date is None:
                raise ValidationError({'starts_before': 'Utilisez une date au format AAAA-MM-JJ.'})
            queryset = queryset.filter(occurrences__starts_at__date__lte=parsed_date)
        return Response(EventSerializer(queryset.distinct(), many=True).data)

    @transaction.atomic
    def post(self, request):
        serializer = EventWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        event = Event.objects.create(
            organization=TenantContext.get(),
            scope=data['scope'],
            church=data['church'],
            campus=data['campus'],
            title=data['title'],
            description=data.get('description', ''),
            category=data.get('category', ''),
            location=data.get('location', ''),
            timezone=data.get('timezone', 'UTC'),
            recurrence_frequency=data['recurrence_frequency'],
            recurrence_interval=data['recurrence_interval'],
            recurrence_until=data.get('recurrence_until'),
            created_by=request.user,
            updated_by=request.user,
        )
        event.included_churches.set(data['included_churches_objects'])
        occurrence = EventOccurrence.objects.create(
            event=event,
            starts_at=data['starts_at'],
            ends_at=data['ends_at'],
            location=data.get('occurrence_location') or data.get('location', ''),
            registration_mode=data['registration_mode'],
            capacity=data.get('capacity'),
            waitlist_enabled=data['waitlist_enabled'],
            checkin_mode=data['checkin_mode'],
            checkin_opens_at=data.get('checkin_opens_at'),
            checkin_closes_at=data.get('checkin_closes_at'),
        )
        record_event(event, 'EVENT_CREATED', actor=request.user, occurrence=occurrence)
        return Response(EventSerializer(event).data, status=status.HTTP_201_CREATED)


class EventManagerAccessView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = TenantContext.get()
        if organization is None:
            return Response({'detail': 'Aucune organisation active.'}, status=status.HTTP_400_BAD_REQUEST)
        organization_admin = is_organization_event_admin(request.user, organization)
        organization_reader = has_configuration_permission(request.user, 'events.read', organization)
        if organization_admin or organization_reader:
            churches = list(organization.churches.filter(is_active=True).values('church_id', 'name'))
        else:
            church_ids = set(managed_church_ids(request.user, organization)) | configuration_church_ids(
                request.user,
                organization,
                'events',
            )
            churches = list(organization.churches.filter(
                church_id__in=church_ids,
                is_active=True,
            ).values('church_id', 'name'))
        return Response({
            'can_manage': organization_admin or organization_reader or bool(churches),
            'is_organization_admin': organization_admin,
            'churches': churches,
        })


class ChurchEventAdministratorsView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get_church(self, church_id):
        return get_object_or_404(
            TenantContext.get().churches.filter(is_active=True),
            church_id=church_id,
        )

    def get(self, request, church_id):
        church = self.get_church(church_id)
        administrators = ChurchEventAdministrator.objects.filter(
            church=church,
            member__status=Member.Status.ACTIVE,
        ).select_related('member').order_by('member__last_name', 'member__first_name')
        return Response([{
            'member_id': str(item.member_id),
            'name': str(item.member),
            'email': item.member.email,
        } for item in administrators])

    def post(self, request, church_id):
        church = self.get_church(church_id)
        member_id = request.data.get('member_id')
        member = get_object_or_404(
            Member.objects.filter(
                pk=member_id,
                organization=church.organization,
                church=church,
                status=Member.Status.ACTIVE,
                user__organization_memberships__organization=church.organization,
            ),
        )
        assignment, _ = ChurchEventAdministrator.objects.get_or_create(
            church=church,
            member=member,
            defaults={'assigned_by': request.user},
        )
        return Response({
            'member_id': str(assignment.member_id),
            'name': str(member),
            'email': member.email,
        }, status=status.HTTP_201_CREATED)

    def delete(self, request, church_id):
        church = self.get_church(church_id)
        member_id = request.data.get('member_id')
        deleted, _ = ChurchEventAdministrator.objects.filter(
            church=church,
            member_id=member_id,
        ).delete()
        if not deleted:
            return Response({'detail': 'Cet administrateur d’église est introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EventDetailView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]

    def get(self, request, event_id):
        return Response(EventSerializer(_authorized_event(event_id, request.user)).data)

    @transaction.atomic
    def patch(self, request, event_id):
        event = _authorized_event(event_id, request.user)
        serializer = EventPatchSerializer(data=request.data, partial=True, context={'event': event})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        included_churches = data.pop('included_church_ids', None)
        changed_fields = []
        for field, value in data.items():
            setattr(event, field, value)
            changed_fields.append(field)
        event.updated_by = request.user
        event.save(update_fields=changed_fields + ['updated_by', 'updated_at'])
        if included_churches is not None:
            event.included_churches.set(included_churches)
        record_event(event, 'EVENT_UPDATED', actor=request.user, detail={'fields': changed_fields})
        return Response(EventSerializer(event).data)


class EventStatusView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]

    def post(self, request, event_id):
        event = _authorized_event(event_id, request.user)
        serializer = StatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = transition_event(
            event,
            serializer.validated_data['status'],
            request.user,
            confirm=serializer.validated_data['confirm'],
        )
        return Response(EventSerializer(event).data)


class EventSeriesUpdateView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]

    @transaction.atomic
    def post(self, request, event_id):
        event = _authorized_event(event_id, request.user)
        if event.status in (EventStatus.CANCELLED, EventStatus.ARCHIVED):
            raise ValidationError('Une série annulée ou archivée ne peut plus être modifiée.')
        serializer = EventSeriesUpdateSerializer(
            data=request.data,
            context={'event': event},
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        event_changes = dict(data.get('event_changes', {}))
        included_churches = event_changes.pop('included_church_ids', None)
        updated_event_fields = []
        for field, value in event_changes.items():
            setattr(event, field, value)
            updated_event_fields.append(field)
        if updated_event_fields:
            event.updated_by = request.user
            event.save(update_fields=updated_event_fields + ['updated_by', 'updated_at'])
        if included_churches is not None:
            event.included_churches.set(included_churches)

        occurrence_changes = data.get('occurrence_changes')
        updated_occurrences = []
        if occurrence_changes:
            target = data['target_occurrence']
            future = list(event.occurrences.select_for_update().filter(
                starts_at__gte=timezone.now(),
                status__in=[EventStatus.DRAFT, EventStatus.PUBLISHED],
            ).order_by('starts_at'))
            start_shift = timedelta(0)
            end_shift = timedelta(0)
            if 'starts_at' in occurrence_changes:
                start_shift = occurrence_changes['starts_at'] - target.starts_at
            if 'ends_at' in occurrence_changes:
                end_shift = occurrence_changes['ends_at'] - target.ends_at
            elif 'starts_at' in occurrence_changes:
                end_shift = start_shift
            opens_shift = None
            closes_shift = None
            if 'checkin_opens_at' in occurrence_changes:
                opens_shift = (
                    occurrence_changes['checkin_opens_at'] - target.checkin_opens_at
                    if occurrence_changes['checkin_opens_at'] and target.checkin_opens_at
                    else None
                )
            if 'checkin_closes_at' in occurrence_changes:
                closes_shift = (
                    occurrence_changes['checkin_closes_at'] - target.checkin_closes_at
                    if occurrence_changes['checkin_closes_at'] and target.checkin_closes_at
                    else None
                )
            moved_starts = [item.starts_at + start_shift for item in future]
            if len(set(moved_starts)) != len(moved_starts):
                raise ValidationError({'occurrence': 'Le déplacement créerait des occurrences en doublon.'})
            future_ids = [item.id for item in future]
            for moved_start in moved_starts:
                if event.occurrences.filter(starts_at=moved_start).exclude(id__in=future_ids).exists():
                    raise ValidationError({'occurrence': 'Le déplacement entrerait en conflit avec une autre occurrence.'})
            for occurrence in future:
                if 'starts_at' in occurrence_changes:
                    occurrence.starts_at += start_shift
                if 'ends_at' in occurrence_changes:
                    occurrence.ends_at += end_shift
                elif 'starts_at' in occurrence_changes:
                    occurrence.ends_at += end_shift
                for field, value in occurrence_changes.items():
                    if field in ('starts_at', 'ends_at'):
                        continue
                    if field == 'checkin_opens_at':
                        if value is None:
                            occurrence.checkin_opens_at = None
                        elif opens_shift is not None and occurrence.checkin_opens_at:
                            occurrence.checkin_opens_at += opens_shift
                        else:
                            occurrence.checkin_opens_at = occurrence.starts_at + (value - target.starts_at)
                    elif field == 'checkin_closes_at':
                        if value is None:
                            occurrence.checkin_closes_at = None
                        elif closes_shift is not None and occurrence.checkin_closes_at:
                            occurrence.checkin_closes_at += closes_shift
                        else:
                            occurrence.checkin_closes_at = occurrence.starts_at + (value - target.starts_at)
                    else:
                        setattr(occurrence, field, value)
                if start_shift and 'checkin_opens_at' not in occurrence_changes and occurrence.checkin_opens_at:
                    occurrence.checkin_opens_at += start_shift
                if start_shift and 'checkin_closes_at' not in occurrence_changes and occurrence.checkin_closes_at:
                    occurrence.checkin_closes_at += start_shift
                occurrence.save()
                updated_occurrences.append(occurrence)
        record_event(
            event,
            'SERIES_FUTURE_UPDATED',
            actor=request.user,
            detail={
                'event_fields': updated_event_fields,
                'occurrences_updated': len(updated_occurrences),
                'confirmed_future': True,
            },
        )
        return Response({
            'event': EventSerializer(event).data,
            'occurrences_updated': len(updated_occurrences),
        })


class EventOccurrenceListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]

    def get(self, request, event_id):
        event = _authorized_event(event_id, request.user)
        occurrences = event.occurrences.order_by('starts_at')
        return Response(EventOccurrenceSerializer(occurrences, many=True).data)

    def post(self, request, event_id):
        event = _authorized_event(event_id, request.user)
        serializer = EventOccurrenceWriteSerializer(data=request.data, context={'timezone': event.timezone})
        serializer.is_valid(raise_exception=True)
        occurrence = serializer.save(event=event, status=EventStatus.DRAFT)
        record_event(event, 'OCCURRENCE_CREATED', actor=request.user, occurrence=occurrence)
        return Response(EventOccurrenceSerializer(occurrence).data, status=status.HTTP_201_CREATED)


class EventOccurrenceDetailView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]

    def get_occurrence(self, event_id, occurrence_id):
        return get_object_or_404(
            EventOccurrence.objects.select_related('event'),
            pk=occurrence_id,
            event_id=event_id,
            event__organization=TenantContext.get(),
        )

    @transaction.atomic
    def patch(self, request, event_id, occurrence_id):
        occurrence = self.get_occurrence(event_id, occurrence_id)
        if occurrence.event.status in (EventStatus.CANCELLED, EventStatus.ARCHIVED):
            return Response({'detail': 'Une série annulée ou archivée ne peut plus être modifiée.'}, status=status.HTTP_400_BAD_REQUEST)
        if occurrence.status in (EventStatus.COMPLETED, EventStatus.CANCELLED, EventStatus.ARCHIVED):
            return Response({'detail': 'Une séance terminée, annulée ou archivée ne peut plus être modifiée.'}, status=status.HTTP_400_BAD_REQUEST)
        serializer = EventOccurrenceWriteSerializer(occurrence, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        occurrence = serializer.save()
        record_event(occurrence.event, 'OCCURRENCE_UPDATED', actor=request.user, occurrence=occurrence, detail={'fields': list(serializer.validated_data)})
        return Response(EventOccurrenceSerializer(occurrence).data)


class EventOccurrenceStatusView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]

    def post(self, request, event_id, occurrence_id):
        occurrence = get_object_or_404(
            EventOccurrence.objects.select_related('event'),
            pk=occurrence_id,
            event_id=event_id,
            event__organization=TenantContext.get(),
        )
        serializer = StatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        occurrence = transition_occurrence(
            occurrence,
            serializer.validated_data['status'],
            request.user,
            confirm=serializer.validated_data['confirm'],
        )
        return Response(EventOccurrenceSerializer(occurrence).data)


class OccurrenceGenerationView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]

    def post(self, request, event_id):
        event = _authorized_event(event_id, request.user)
        try:
            limit = int(request.data.get('limit', 50))
        except (TypeError, ValueError) as error:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'limit': 'Ce champ doit être un entier.'}) from error
        occurrences = generate_occurrences(event, request.user, limit)
        return Response(EventOccurrenceSerializer(occurrences, many=True).data, status=status.HTTP_201_CREATED)


class PublicEventDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, event_id):
        event = get_object_or_404(
            Event.objects.select_related('church', 'campus').prefetch_related('occurrences'),
            pk=event_id,
            status=EventStatus.PUBLISHED,
        )
        occurrences = event.occurrences.filter(status=EventStatus.PUBLISHED).order_by('starts_at')
        return Response({
            'id': str(event.id),
            'title': event.title,
            'description': event.description,
            'category': event.category,
            'scope': event.scope,
            'church_name': event.church.name if event.church_id else None,
            'campus_name': event.campus.name if event.campus_id else None,
            'location': event.location,
            'timezone': event.timezone,
            'occurrences': EventOccurrenceSerializer(occurrences, many=True).data,
        })


class PublicRegistrationCreateView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, event_id, occurrence_id):
        event = get_object_or_404(Event, pk=event_id, status=EventStatus.PUBLISHED)
        occurrence = get_object_or_404(
            EventOccurrence.objects.select_related('event'),
            pk=occurrence_id,
            event=event,
            status=EventStatus.PUBLISHED,
        )
        serializer = PublicRegistrationSerializer(data=request.data, context={'request': request, 'event': event})
        serializer.is_valid(raise_exception=True)
        member = serializer.validated_data.get('member')
        registration, qr_token, manage_token, verification_code = create_registration(
            occurrence,
            actor=request.user if request.user.is_authenticated else None,
            member=member,
            visitor_data=None if member else serializer.validated_data,
        )
        return Response({
            'registration': RegistrationSerializer(registration).data,
            'qr_token': qr_token,
            'verification_code': verification_code,
            'manage_token': manage_token,
            'status': registration.status,
        }, status=status.HTTP_201_CREATED)


class RegistrationListView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]

    def get(self, request, event_id, occurrence_id):
        occurrence = get_object_or_404(
            EventOccurrence.objects.select_related('event'),
            pk=occurrence_id,
            event_id=event_id,
            event__organization=TenantContext.get(),
        )
        queryset = occurrence.registrations.select_related('member', 'person').order_by('created_at')
        if request.query_params.get('history') != '1':
            queryset = queryset.exclude(status__in=[RegistrationStatus.CANCELLED, RegistrationStatus.DECLINED, RegistrationStatus.EXPIRED])
        return Response(RegistrationSerializer(queryset, many=True).data)


class RegistrationSelfServiceView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, registration_id):
        registration = get_object_or_404(
            EventRegistration.objects.select_related('occurrence__event', 'member', 'person'),
            pk=registration_id,
        )
        user_owns = (
            request.user.is_authenticated
            and registration.member is not None
            and registration.member.user_id == request.user.id
            and TenantContext.get() == registration.occurrence.event.organization
        )
        token = request.query_params.get('manage_token')
        token_owns = bool(
            token
            and registration.manage_token_hash
            and secrets.compare_digest(registration.manage_token_hash, token_hash(token))
        )
        if not (user_owns or token_owns):
            return Response({'detail': 'Inscription introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(RegistrationSerializer(registration).data)


class RegistrationCancelView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, registration_id):
        registration = get_object_or_404(
            EventRegistration.objects.select_related('occurrence__event', 'member'),
            pk=registration_id,
        )
        can_manage = can_manage_event(request.user, registration.occurrence.event)
        registration = cancel_registration(
            registration,
            actor=request.user if request.user.is_authenticated else None,
            manage_token=request.data.get('manage_token'),
            can_manage=can_manage,
        )
        return Response(RegistrationSerializer(registration).data)


class WaitlistOfferResponseView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, registration_id):
        registration = get_object_or_404(
            EventRegistration.objects.select_related('occurrence__event', 'member'),
            pk=registration_id,
        )
        accept = request.data.get('accept')
        if not isinstance(accept, bool):
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'accept': 'Ce champ booléen est obligatoire.'})
        can_manage = can_manage_event(request.user, registration.occurrence.event)
        registration, qr_token, verification_code = respond_to_waitlist_offer(
            registration,
            accept=accept,
            actor=request.user if request.user.is_authenticated else None,
            manage_token=request.data.get('manage_token'),
            can_manage=can_manage,
        )
        return Response({
            'registration': RegistrationSerializer(registration).data,
            'qr_token': qr_token,
            'verification_code': verification_code,
        })


class RegistrationScanView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'event_scan'

    def post(self, request):
        serializer = ScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = serializer.validated_data['token']
        expected_occurrence_id = serializer.validated_data['expected_occurrence_id']
        registrations = EventRegistration.objects.filter(
            occurrence__event__organization=TenantContext.get(),
        )
        is_verification_code = len(raw_token) == 5 and raw_token.isdigit()
        if is_verification_code:
            registrations = registrations.filter(
                occurrence_id=expected_occurrence_id,
                verification_code_hash=token_hash(raw_token),
            )
        else:
            registrations = registrations.filter(qr_token_hash=token_hash(raw_token))
        if not is_organization_event_admin(request.user):
            church_ids = managed_church_ids(request.user)
            registrations = registrations.filter(
                Q(occurrence__event__scope=EventScope.CHURCH, occurrence__event__church_id__in=church_ids)
                | Q(occurrence__event__scope=EventScope.CAMPUS, occurrence__event__campus__church_id__in=church_ids)
            )
        registration = registrations.select_related(
            'occurrence__event', 'occurrence__event__campus', 'member', 'person',
        ).first()
        if registration is None:
            record_event(
                None,
                'REGISTRATION_QR_REJECTED',
                actor=request.user,
                detail={'tenant_id': str(TenantContext.get().tenant_id), 'result': 'INVALID'},
            )
            return Response({'result': 'INVALID', 'detail': 'Code QR inconnu ou invalide.'}, status=status.HTTP_404_NOT_FOUND)
        if expected_occurrence_id and registration.occurrence_id != expected_occurrence_id:
            record_event(
                registration.occurrence.event,
                'REGISTRATION_QR_WRONG_OCCURRENCE',
                actor=request.user,
                occurrence=registration.occurrence,
                detail={'registration_id': str(registration.id), 'expected_occurrence_id': str(expected_occurrence_id)},
            )
            return Response({
                'result': 'WRONG_OCCURRENCE',
                'detail': 'Ce code QR correspond à une autre séance.',
            }, status=status.HTTP_400_BAD_REQUEST)
        attendee = registration.member or registration.person
        attendance = EventAttendance.objects.filter(registration=registration).first()
        if registration.occurrence.event.status in (EventStatus.CANCELLED, EventStatus.ARCHIVED) or registration.occurrence.status not in (
            EventStatus.PUBLISHED,
            EventStatus.IN_PROGRESS,
        ):
            result = {'result': 'INELIGIBLE', 'detail': 'Cet événement ou cette séance ne permet plus le pointage.'}
        elif registration.status == RegistrationStatus.CANCELLED:
            result = {'result': 'CANCELLED', 'detail': 'Cette inscription a été annulée.'}
        elif registration.status != RegistrationStatus.CONFIRMED:
            result = {'result': 'INELIGIBLE', 'detail': 'Cette inscription ne permet pas l’entrée.'}
        elif attendance and attendance.state == AttendanceState.PRESENT:
            result = {
                'result': 'ALREADY_CHECKED_IN',
                'detail': 'PRÉSENCE DÉJÀ ENREGISTRÉE',
                'checked_in_at': attendance.checked_in_at,
            }
        else:
            result = {'result': 'VALID', 'detail': 'Inscription confirmée; vérifiez l’identité puis confirmez la présence.'}
        record_event(
            registration.occurrence.event,
            'REGISTRATION_QR_SCANNED',
            actor=request.user,
            occurrence=registration.occurrence,
            detail={'registration_id': str(registration.id), 'result': result['result']},
        )
        result['attendee'] = {
            'name': str(attendee),
            'phone': attendee.phone,
            'registered_at': registration.created_at,
            'registration_status': registration.status,
            'event': registration.occurrence.event.title,
            'occurrence_id': str(registration.occurrence_id),
        }
        result['registration_id'] = str(registration.id)
        result['occurrence_status'] = registration.occurrence.status
        return Response(result)


class RegistrationCheckInView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'event_scan'

    def post(self, request, registration_id):
        registration = get_object_or_404(
            EventRegistration.objects.select_related('occurrence__event'),
            pk=registration_id,
            occurrence__event__organization=TenantContext.get(),
        )
        serializer = CheckInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        attendance, created = checkin_registration(
            registration,
            request.user,
            serializer.validated_data['token'],
            serializer.validated_data.get('exception_reason', ''),
        )
        return Response({
            'result': 'CHECKED_IN' if created else 'ALREADY_CHECKED_IN',
            'detail': 'Présence confirmée.' if created else 'PRÉSENCE DÉJÀ ENREGISTRÉE',
            'attendance': AttendanceSerializer(attendance).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class SelfCheckInTokenView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]

    def post(self, request, event_id, occurrence_id):
        occurrence = get_object_or_404(
            EventOccurrence.objects.select_related('event'),
            pk=occurrence_id,
            event_id=event_id,
            event__organization=TenantContext.get(),
            checkin_mode=CheckInMode.SELF,
        )
        raw_token = secrets.token_urlsafe(32)
        occurrence.self_checkin_token_hash = token_hash(raw_token)
        occurrence.save(update_fields=['self_checkin_token_hash', 'updated_at'])
        record_event(occurrence.event, 'SELF_CHECKIN_TOKEN_ROTATED', actor=request.user, occurrence=occurrence)
        return Response({'token': raw_token})


class SelfCheckInView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, event_id, occurrence_id):
        member = Member.objects.filter(user=request.user, organization=TenantContext.get()).first()
        if member is None:
            return Response({'detail': 'Un profil membre actif est requis.'}, status=status.HTTP_403_FORBIDDEN)
        occurrence = get_object_or_404(
            EventOccurrence.objects.select_related('event'),
            pk=occurrence_id,
            event_id=event_id,
            event__organization=TenantContext.get(),
        )
        serializer = SelfCheckInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        attendance, created = checkin_self(occurrence, member, serializer.validated_data['token'])
        return Response({
            'result': 'CHECKED_IN' if created else 'ALREADY_CHECKED_IN',
            'detail': 'Présence confirmée.' if created else 'PRÉSENCE DÉJÀ ENREGISTRÉE',
            'attendance': AttendanceSerializer(attendance).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class EventAttendanceListView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]

    def get(self, request, event_id, occurrence_id):
        occurrence = get_object_or_404(
            EventOccurrence.objects.select_related('event'),
            pk=occurrence_id,
            event_id=event_id,
            event__organization=TenantContext.get(),
        )
        queryset = occurrence.attendances.select_related('member', 'registration__member', 'registration__person', 'checked_in_by')
        return Response(AttendanceSerializer(queryset, many=True).data)


class AttendanceCorrectionView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]

    def post(self, request, attendance_id):
        attendance = get_object_or_404(
            EventAttendance.objects.select_related('occurrence__event'),
            pk=attendance_id,
            occurrence__event__organization=TenantContext.get(),
        )
        serializer = AttendanceCorrectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        correction = correct_attendance(
            attendance,
            request.user,
            serializer.validated_data['state'],
            serializer.validated_data['reason'],
        )
        return Response({
            'correction_id': str(correction.id),
            'attendance': AttendanceSerializer(correction.attendance).data,
        })


class EventAuditLogView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]

    def get(self, request, event_id):
        event = _authorized_event(event_id, request.user)
        return Response(AuditLogSerializer(event.audit_logs.select_related('actor', 'occurrence'), many=True).data)


class EventStatisticsView(APIView):
    permission_classes = [IsAuthenticated, IsEventManager]

    def get(self, request, event_id):
        return Response({'event_id': str(event_id), 'occurrences': event_statistics(_authorized_event(event_id, request.user))})
