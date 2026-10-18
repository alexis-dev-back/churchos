from datetime import date

from django.db import transaction
from django.db.utils import IntegrityError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ecodim.models import (
    EcodimAuditLog,
    EcodimChild,
    EcodimChildGuardian,
    EcodimClass,
    EcodimEnrollment,
    EcodimPickupAuthorization,
    EcodimSession,
    EcodimStaffAssignment,
)
from apps.ecodim.permissions import (
    HasEcodimAccess,
    HasEcodimChildSafetyAccess,
    HasEcodimManagementAccess,
    accessible_ecodim_churches,
    has_ecodim_management_access,
    is_ecodim_organization_admin,
)
from apps.ecodim.serializers import (
    EcodimChildEnrollmentSerializer,
    EcodimChildDetailSerializer,
    EcodimChildGuardianSerializer,
    EcodimChildHandoffCreateSerializer,
    EcodimChildHandoffSerializer,
    EcodimChildSerializer,
    EcodimClassSerializer,
    EcodimLinkableMemberSerializer,
    EcodimPickupAuthorizationSerializer,
    EcodimSessionSerializer,
    EcodimStaffAssignmentSerializer,
)
from apps.ecodim.services import (
    create_child_handoff,
    create_ecodim_child,
    enroll_child,
    scoped_church,
    update_ecodim_child,
)
from apps.families.models import Family
from apps.members.models import Member
from infrastructure.tenancy import TenantContext


class EcodimContextView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimAccess]

    def get(self, request):
        organization = TenantContext.get()
        churches = accessible_ecodim_churches(request.user, organization).order_by('name')
        church_data = [{'church_id': str(church.church_id), 'name': church.name} for church in churches]
        default_church_id = None
        if len(church_data) == 1:
            default_church_id = church_data[0]['church_id']
        else:
            from apps.members.models import Member

            member_church_id = Member.objects.filter(
                user=request.user,
                organization=organization,
                status=Member.Status.ACTIVE,
                church_id__in=[church['church_id'] for church in church_data],
            ).values_list('church_id', flat=True).first()
            if member_church_id:
                default_church_id = str(member_church_id)
        return Response({
            'churches': church_data,
            'default_church_id': default_church_id,
            'is_organization_admin': is_ecodim_organization_admin(request.user, organization),
            'can_manage': has_ecodim_management_access(request.user, organization),
        })


class EcodimDashboardView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimAccess]

    def get(self, request):
        organization = TenantContext.get()
        church = scoped_church(request, request.query_params.get('church_id'))
        children = EcodimChild.objects.filter(organization=organization, church=church)
        today = timezone.localdate()
        active_children = children.filter(status=EcodimChild.Status.ACTIVE)
        known_ages = list(active_children.exclude(date_of_birth__isnull=True).values_list('date_of_birth', flat=True))
        age_counts = {'3-5': 0, '6-11': 0, '12-14': 0, '15-17': 0, 'other': 0, 'unknown': active_children.filter(date_of_birth__isnull=True).count()}
        for birth_date in known_ages:
            age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
            if 3 <= age <= 5:
                age_counts['3-5'] += 1
            elif 6 <= age <= 11:
                age_counts['6-11'] += 1
            elif 12 <= age <= 14:
                age_counts['12-14'] += 1
            elif 15 <= age <= 17:
                age_counts['15-17'] += 1
            else:
                age_counts['other'] += 1

        first_month = _month_shift(today.replace(day=1), -5)
        monthly_counts = {}
        recent_birth_records = active_children.filter(
            created_at__date__gte=first_month,
            date_of_birth__isnull=False,
        ).values_list('date_of_birth', 'created_at')
        for birth_date, created_at in recent_birth_records:
            age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
            month_key = _month_key(created_at)
            counts = monthly_counts.setdefault(month_key, {'children': 0, 'adolescents': 0})
            if 3 <= age <= 11:
                counts['children'] += 1
            elif 12 <= age <= 17:
                counts['adolescents'] += 1
        months = [_month_shift(today.replace(day=1), offset) for offset in range(-5, 1)]

        active_classes = EcodimClass.objects.filter(
            organization=organization,
            church=church,
            status=EcodimClass.Status.ACTIVE,
        ).annotate(
            active_children_count=Count(
                'enrollments',
                filter=Q(enrollments__ended_at__isnull=True),
                distinct=True,
            ),
        )
        now = timezone.now()
        upcoming_sessions = EcodimSession.objects.filter(
            organization=organization,
            church=church,
            status__in=[EcodimSession.Status.PLANNED, EcodimSession.Status.IN_PROGRESS],
            starts_at__gte=now,
        ).select_related('ecodim_class').order_by('starts_at')
        upcoming_session_count = upcoming_sessions.count()
        upcoming_sessions = upcoming_sessions[:5]
        in_progress_session_count = EcodimSession.objects.filter(
            organization=organization,
            church=church,
            status=EcodimSession.Status.IN_PROGRESS,
        ).count()
        recent_children = children.order_by('-created_at')[:6]
        responsible_keys = set()
        for member_id, person_id, name in EcodimChildGuardian.objects.filter(
            child__in=children,
        ).values_list('member_id', 'person_id', 'name'):
            identity = ('member', member_id) if member_id else (
                ('person', person_id) if person_id else ('name', name.strip().casefold())
            )
            responsible_keys.add(identity)
        data = {
            'summary': {
                'active_children': active_children.count(),
                'children_3_11': age_counts['6-11'] + age_counts['3-5'],
                'adolescents_12_17': age_counts['12-14'] + age_counts['15-17'],
                'new_children_this_month': children.filter(created_at__year=today.year, created_at__month=today.month).count(),
                'active_classes': active_classes.count(),
                'responsibles': len(responsible_keys),
                'activities_in_progress': in_progress_session_count,
                'upcoming_sessions': upcoming_session_count,
            },
            'age_distribution': [
                {'label': label, 'count': count}
                for label, count in age_counts.items()
            ],
            'registrations_by_month': [
                {
                    'label': f'{_MONTHS_FR[month.month - 1]} {month.year}',
                    'children': monthly_counts.get(month.strftime('%Y-%m-01'), {}).get('children', 0),
                    'adolescents': monthly_counts.get(month.strftime('%Y-%m-01'), {}).get('adolescents', 0),
                }
                for month in months
            ],
            'classes': EcodimClassSerializer(active_classes.order_by('name')[:5], many=True).data,
            'upcoming_sessions': [
                {
                    'id': str(session.pk),
                    'title': session.title,
                    'starts_at': session.starts_at,
                    'location': session.location,
                    'class_name': session.ecodim_class.name if session.ecodim_class else None,
                }
                for session in upcoming_sessions
            ],
            'recent_children': EcodimChildSerializer(recent_children, many=True).data,
        }
        return Response(data)


class EcodimChildListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimAccess]

    def get_permissions(self):
        if self.request.method == 'POST':
            return [permissions.IsAuthenticated(), HasEcodimManagementAccess()]
        return super().get_permissions()

    def get(self, request):
        organization = TenantContext.get()
        church = scoped_church(request, request.query_params.get('church_id'))
        queryset = EcodimChild.objects.filter(organization=organization, church=church).select_related('family')
        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(reference__icontains=search)
            )
        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        paginator = PageNumberPagination()
        paginator.page_size = 25
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(EcodimChildSerializer(page, many=True).data)

    def post(self, request):
        organization = TenantContext.get()
        church = scoped_church(request, request.data.get('church_id'))
        payload = request.data.copy()
        payload.pop('church_id', None)
        child = create_ecodim_child(
            data=payload,
            request=request,
            organization=organization,
            church=church,
        )
        return Response(EcodimChildSerializer(child).data, status=status.HTTP_201_CREATED)


class EcodimChildDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimAccess]

    def get_permissions(self):
        if self.request.method == 'PATCH':
            return [permissions.IsAuthenticated(), HasEcodimManagementAccess()]
        return super().get_permissions()

    def get_object(self, request, child_id):
        churches = accessible_ecodim_churches(request.user, TenantContext.get())
        return get_object_or_404(
            EcodimChild.objects.select_related('family').filter(
                organization=TenantContext.get(),
                church__in=churches,
            ),
            pk=child_id,
        )

    def get(self, request, child_id):
        child = self.get_object(request, child_id)
        return Response(EcodimChildDetailSerializer(child).data)

    def patch(self, request, child_id):
        child = self.get_object(request, child_id)
        child = update_ecodim_child(
            child=child,
            data=request.data,
            request=request,
            organization=TenantContext.get(),
            church=child.church,
        )
        return Response(EcodimChildSerializer(child).data)


class EcodimClassListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimAccess]

    def get_permissions(self):
        if self.request.method == 'POST':
            return [permissions.IsAuthenticated(), HasEcodimManagementAccess()]
        return super().get_permissions()

    def get(self, request):
        organization = TenantContext.get()
        church = scoped_church(request, request.query_params.get('church_id'))
        queryset = EcodimClass.objects.filter(
            organization=organization,
            church=church,
        ).annotate(
            active_children_count=Count(
                'enrollments',
                filter=Q(enrollments__ended_at__isnull=True),
                distinct=True,
            ),
        )
        return Response(EcodimClassSerializer(queryset, many=True).data)

    def post(self, request):
        organization = TenantContext.get()
        church = scoped_church(request, request.data.get('church_id'))
        payload = request.data.copy()
        payload.pop('church_id', None)
        serializer = EcodimClassSerializer(data=payload, context={'church': church})
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                ecodim_class = serializer.save(
                    organization=organization,
                    church=church,
                    created_by=request.user,
                )
                EcodimAuditLog.objects.create(
                    organization=organization,
                    church=church,
                    actor=request.user,
                    action='CLASS_CREATED',
                    detail={'class_id': str(ecodim_class.pk), 'class_name': ecodim_class.name},
                )
        except IntegrityError:
            return Response(
                {'name': ['Une classe portant ce nom existe déjà dans cette église.']},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(EcodimClassSerializer(ecodim_class, context={'request': request}).data, status=status.HTTP_201_CREATED)


class EcodimClassDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimManagementAccess]

    def patch(self, request, class_id):
        ecodim_class = get_object_or_404(
            EcodimClass.objects.filter(
                organization=TenantContext.get(),
                church__in=accessible_ecodim_churches(request.user, TenantContext.get()),
            ),
            pk=class_id,
        )
        serializer = EcodimClassSerializer(
            ecodim_class,
            data=request.data,
            partial=True,
            context={'church': ecodim_class.church},
        )
        serializer.is_valid(raise_exception=True)
        ecodim_class = serializer.save()
        EcodimAuditLog.objects.create(
            organization=ecodim_class.organization,
            church=ecodim_class.church,
            actor=request.user,
            action='CLASS_UPDATED',
            detail={'class_id': str(ecodim_class.pk), 'fields': sorted(serializer.validated_data.keys())},
        )
        return Response(EcodimClassSerializer(ecodim_class).data)


class EcodimChildEnrollmentView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimManagementAccess]

    def post(self, request, child_id):
        child = get_object_or_404(
            EcodimChild.objects.filter(
                organization=TenantContext.get(),
                church__in=accessible_ecodim_churches(request.user, TenantContext.get()),
            ),
            pk=child_id,
        )
        serializer = EcodimChildEnrollmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ecodim_class = get_object_or_404(
            EcodimClass.objects.filter(
                organization=TenantContext.get(),
                church__in=accessible_ecodim_churches(request.user, TenantContext.get()),
            ),
            pk=serializer.validated_data['class_id'],
        )
        enrollment = enroll_child(child=child, ecodim_class=ecodim_class, request=request)
        return Response({
            'id': str(enrollment.pk),
            'child_id': str(enrollment.child_id),
            'class_id': str(enrollment.ecodim_class_id),
            'started_at': enrollment.started_at,
        }, status=status.HTTP_201_CREATED)


class EcodimMemberListView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimAccess]

    def get(self, request):
        organization = TenantContext.get()
        church = scoped_church(request, request.query_params.get('church_id'))
        members = Member.objects.filter(
            organization=organization,
            church=church,
            status=Member.Status.ACTIVE,
            user__is_active=True,
        ).order_by('last_name', 'first_name')
        return Response(EcodimLinkableMemberSerializer(members, many=True).data)


class EcodimFamilyListView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimAccess]

    def get(self, request):
        organization = TenantContext.get()
        church = scoped_church(request, request.query_params.get('church_id'))
        families = Family.objects.filter(organization=organization).filter(
            Q(memberships__member__church=church, memberships__left_at__isnull=True)
            | Q(ecodim_children__church=church),
        ).distinct().order_by('name')
        return Response([{'id': str(family.pk), 'name': family.name} for family in families])


class EcodimChildGuardiansView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimChildSafetyAccess]

    def get_child(self, request, child_id):
        return get_object_or_404(
            EcodimChild.objects.filter(
                organization=TenantContext.get(),
                church__in=accessible_ecodim_churches(request.user, TenantContext.get()),
            ),
            pk=child_id,
        )

    def get(self, request, child_id):
        child = self.get_child(request, child_id)
        return Response(EcodimChildGuardianSerializer(child.guardians.all(), many=True).data)

    def post(self, request, child_id):
        child = self.get_child(request, child_id)
        serializer = EcodimChildGuardianSerializer(
            data=request.data,
            context={'child': child},
        )
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            guardian = serializer.save(child=child)
            EcodimAuditLog.objects.create(
                organization=child.organization,
                church=child.church,
                child=child,
                actor=request.user,
                action='GUARDIAN_ADDED',
                detail={'guardian_id': str(guardian.pk)},
            )
        return Response(EcodimChildGuardianSerializer(guardian).data, status=status.HTTP_201_CREATED)

    def get_permissions(self):
        if self.request.method == 'POST':
            return [permissions.IsAuthenticated(), HasEcodimManagementAccess()]
        return super().get_permissions()


class EcodimPickupAuthorizationsView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimChildSafetyAccess]

    def get_child(self, request, child_id):
        return get_object_or_404(
            EcodimChild.objects.filter(
                organization=TenantContext.get(),
                church__in=accessible_ecodim_churches(request.user, TenantContext.get()),
            ),
            pk=child_id,
        )

    def get(self, request, child_id):
        child = self.get_child(request, child_id)
        return Response(EcodimPickupAuthorizationSerializer(child.pickup_authorizations.all(), many=True).data)

    def post(self, request, child_id):
        child = self.get_child(request, child_id)
        serializer = EcodimPickupAuthorizationSerializer(
            data=request.data,
            context={'child': child},
        )
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            authorization = serializer.save(child=child, authorized_by=request.user)
            EcodimAuditLog.objects.create(
                organization=child.organization,
                church=child.church,
                child=child,
                actor=request.user,
                action='PICKUP_AUTHORIZATION_CREATED',
                detail={'authorization_id': str(authorization.pk)},
            )
        return Response(EcodimPickupAuthorizationSerializer(authorization).data, status=status.HTTP_201_CREATED)

    def get_permissions(self):
        if self.request.method == 'POST':
            return [permissions.IsAuthenticated(), HasEcodimManagementAccess()]
        return super().get_permissions()


class EcodimPickupAuthorizationDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimManagementAccess]

    def patch(self, request, child_id, authorization_id):
        child = get_object_or_404(
            EcodimChild.objects.filter(
                organization=TenantContext.get(),
                church__in=accessible_ecodim_churches(request.user, TenantContext.get()),
            ),
            pk=child_id,
        )
        authorization = get_object_or_404(
            EcodimPickupAuthorization.objects.filter(child=child),
            pk=authorization_id,
        )
        serializer = EcodimPickupAuthorizationSerializer(
            authorization,
            data=request.data,
            partial=True,
            context={'child': child},
        )
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            authorization = serializer.save()
            EcodimAuditLog.objects.create(
                organization=child.organization,
                church=child.church,
                child=child,
                actor=request.user,
                action='PICKUP_AUTHORIZATION_UPDATED',
                detail={
                    'authorization_id': str(authorization.pk),
                    'fields': sorted(serializer.validated_data.keys()),
                },
            )
        return Response(EcodimPickupAuthorizationSerializer(authorization).data)


class EcodimChildHandoffsView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimChildSafetyAccess]

    def get_child(self, request, child_id):
        return get_object_or_404(
            EcodimChild.objects.filter(
                organization=TenantContext.get(),
                church__in=accessible_ecodim_churches(request.user, TenantContext.get()),
            ),
            pk=child_id,
        )

    def get(self, request, child_id):
        child = self.get_child(request, child_id)
        handoffs = child.handoffs.select_related('authorization', 'confirmed_by')
        return Response(EcodimChildHandoffSerializer(handoffs, many=True).data)

    def post(self, request, child_id):
        child = self.get_child(request, child_id)
        serializer = EcodimChildHandoffCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        handoff = create_child_handoff(
            child=child,
            authorization_id=serializer.validated_data['authorization_id'],
            verification_note=serializer.validated_data.get('verification_note', ''),
            request=request,
        )
        return Response(EcodimChildHandoffSerializer(handoff).data, status=status.HTTP_201_CREATED)


class EcodimSessionListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimAccess]

    def get_permissions(self):
        if self.request.method == 'POST':
            return [permissions.IsAuthenticated(), HasEcodimManagementAccess()]
        return super().get_permissions()

    def get(self, request):
        organization = TenantContext.get()
        church = scoped_church(request, request.query_params.get('church_id'))
        sessions = EcodimSession.objects.filter(
            organization=organization,
            church=church,
        ).select_related('ecodim_class')
        return Response(EcodimSessionSerializer(sessions, many=True).data)

    def post(self, request):
        organization = TenantContext.get()
        church = scoped_church(request, request.data.get('church_id'))
        payload = request.data.copy()
        payload.pop('church_id', None)
        serializer = EcodimSessionSerializer(
            data=payload,
            context={'organization': organization, 'church': church},
        )
        serializer.is_valid(raise_exception=True)
        session = serializer.save(
            organization=organization,
            church=church,
            created_by=request.user,
        )
        EcodimAuditLog.objects.create(
            organization=organization,
            church=church,
            actor=request.user,
            action='SESSION_CREATED',
            detail={'session_id': str(session.pk), 'title': session.title},
        )
        return Response(EcodimSessionSerializer(session).data, status=status.HTTP_201_CREATED)


class EcodimSessionDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimManagementAccess]

    def patch(self, request, session_id):
        session = get_object_or_404(
            EcodimSession.objects.filter(
                organization=TenantContext.get(),
                church__in=accessible_ecodim_churches(request.user, TenantContext.get()),
            ),
            pk=session_id,
        )
        serializer = EcodimSessionSerializer(
            session,
            data={key: value for key, value in request.data.items() if key != 'church_id'},
            partial=True,
            context={'organization': session.organization, 'church': session.church},
        )
        serializer.is_valid(raise_exception=True)
        session = serializer.save()
        EcodimAuditLog.objects.create(
            organization=session.organization,
            church=session.church,
            actor=request.user,
            action='SESSION_UPDATED',
            detail={'session_id': str(session.pk), 'fields': sorted(serializer.validated_data.keys())},
        )
        return Response(EcodimSessionSerializer(session).data)


class EcodimStaffListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimManagementAccess]

    def get(self, request):
        organization = TenantContext.get()
        church = scoped_church(request, request.query_params.get('church_id'))
        assignments = EcodimStaffAssignment.objects.filter(
            church=church,
            member__organization=organization,
        ).select_related('member').order_by('member__last_name', 'member__first_name')
        return Response(EcodimStaffAssignmentSerializer(assignments, many=True).data)

    def post(self, request):
        organization = TenantContext.get()
        church = scoped_church(request, request.data.get('church_id'))
        payload = request.data.copy()
        payload.pop('church_id', None)
        serializer = EcodimStaffAssignmentSerializer(
            data=payload,
            context={'organization': organization, 'church': church},
        )
        serializer.is_valid(raise_exception=True)
        role = serializer.validated_data['role']
        if (
            role == EcodimStaffAssignment.Role.DIRECTOR
            and not is_ecodim_organization_admin(request.user, organization)
        ):
            return Response(
                {'detail': 'Seul un administrateur de l’organisation peut désigner un directeur ECODIM.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        assignment, _ = EcodimStaffAssignment.objects.update_or_create(
            church=church,
            member=serializer.validated_data['member'],
            role=role,
            defaults={'is_active': True, 'assigned_by': request.user},
        )
        EcodimAuditLog.objects.create(
            organization=organization,
            church=church,
            actor=request.user,
            action='STAFF_ASSIGNED',
            detail={'assignment_id': str(assignment.pk), 'member_id': str(assignment.member_id), 'role': role},
        )
        return Response(
            EcodimStaffAssignmentSerializer(assignment).data,
            status=status.HTTP_201_CREATED,
        )


class EcodimStaffDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasEcodimManagementAccess]

    def patch(self, request, assignment_id):
        assignment = get_object_or_404(
            EcodimStaffAssignment.objects.select_related('member').filter(
                church__in=accessible_ecodim_churches(request.user, TenantContext.get()),
                member__organization=TenantContext.get(),
            ),
            pk=assignment_id,
        )
        if set(request.data.keys()) != {'is_active'} or not isinstance(request.data['is_active'], bool):
            return Response(
                {'detail': 'Seul le statut actif/inactif de cette affectation peut être modifié.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        assignment.is_active = request.data['is_active']
        assignment.assigned_by = request.user
        assignment.save(update_fields=['is_active', 'assigned_by'])
        EcodimAuditLog.objects.create(
            organization=assignment.member.organization,
            church=assignment.church,
            actor=request.user,
            action='STAFF_ASSIGNMENT_UPDATED',
            detail={'assignment_id': str(assignment.pk), 'is_active': assignment.is_active},
        )
        return Response(EcodimStaffAssignmentSerializer(assignment).data)


def _month_shift(month, offset):
    month_index = month.year * 12 + month.month - 1 + offset
    year, month_index = divmod(month_index, 12)
    return date(year, month_index + 1, 1)


def _month_key(value):
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.strftime('%Y-%m-01')


_MONTHS_FR = [
    'janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin',
    'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.',
]