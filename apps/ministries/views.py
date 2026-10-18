from django.shortcuts import get_object_or_404
from django.db.models import Count, OuterRef, Q, Subquery, Value
from django.db.models.functions import Concat
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.members.permissions import IsTenantAdmin
from apps.ministries.models import (
    Ministry,
    MinistryAssignment,
    MinistryLeadership,
    Tribe,
    TribeAssignment,
    TribeLeadership,
)
from apps.ministries.serializers import (
    AssignmentCloseSerializer,
    AssignmentCreateSerializer,
    LeadershipCloseSerializer,
    LeadershipCreateSerializer,
    MinistryAssignmentSerializer,
    MinistryLeadershipSerializer,
    MinistrySerializer,
    TribeAssignmentSerializer,
    TribeLeadershipSerializer,
    TribeSerializer,
)
from apps.ministries.services import (
    appoint_leader,
    assign_member,
    close_assignment,
    close_leadership,
)
from infrastructure.tenancy import TenantContext


class StructureListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]
    structure_model = None
    serializer_class = None

    def get(self, request):
        queryset = self.structure_model.objects.filter(church__organization=TenantContext.get()).select_related('church')
        assignment_model = getattr(self, 'assignment_model', None)
        leadership_model = getattr(self, 'leadership_model', None)
        structure_field = getattr(self, 'structure_field', None)
        if assignment_model is not None and leadership_model is not None and structure_field:
            queryset = queryset.annotate(
                active_members_count=Count(
                    'assignments',
                    filter=Q(assignments__left_at__isnull=True),
                    distinct=True,
                ),
                leader_name=Subquery(
                    leadership_model.objects.filter(
                        **{
                            f'{structure_field}_id': OuterRef('pk'),
                            'role': 'LEADER',
                            'ended_at__isnull': True,
                        },
                    ).annotate(
                        full_name=Concat('member__first_name', Value(' '), 'member__last_name'),
                    ).values('full_name')[:1],
                ),
            )
        church_id = request.query_params.get('church_id')
        if church_id:
            try:
                church_id = serializers.UUIDField().run_validation(church_id)
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({'church_id': exc.detail}) from exc
            queryset = queryset.filter(church_id=church_id)
        return Response(self.serializer_class(queryset, many=True).data)

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        structure = serializer.save()
        return Response(self.serializer_class(structure).data, status=status.HTTP_201_CREATED)


class StructureDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]
    structure_model = None
    serializer_class = None

    def get_object(self, pk):
        return get_object_or_404(
            self.structure_model.objects.select_related('church'),
            pk=pk,
            church__organization=TenantContext.get(),
        )

    def get(self, request, pk):
        structure = self.get_object(pk)
        return Response(self.serializer_class(structure).data)

    def patch(self, request, pk):
        structure = self.get_object(pk)
        serializer = self.serializer_class(structure, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(self.serializer_class(structure).data)


class AssignmentListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]
    structure_model = None
    assignment_model = None
    serializer_class = None
    structure_field = ''
    is_tribe = False

    def get_structure(self, pk):
        return get_object_or_404(
            self.structure_model.objects.select_related('church'),
            pk=pk,
            church__organization=TenantContext.get(),
        )

    def get(self, request, pk):
        structure = self.get_structure(pk)
        queryset = self.assignment_model.objects.filter(**{self.structure_field: structure}).select_related(
            'member', self.structure_field, 'created_by', 'ended_by',
        )
        if request.query_params.get('history') != '1':
            queryset = queryset.filter(left_at__isnull=True)
        serializer = self.serializer_class(
            queryset,
            many=True,
            context={'structure_field': self.structure_field},
        )
        return Response(serializer.data)

    def post(self, request, pk):
        structure = self.get_structure(pk)
        serializer = AssignmentCreateSerializer(data=request.data, context={'structure': structure})
        serializer.is_valid(raise_exception=True)
        assignment = assign_member(
            structure,
            serializer.validated_data['member'],
            request.user,
            is_tribe=self.is_tribe,
            replace_active=serializer.validated_data.get('replace_active', False),
        )
        return Response(
            self.serializer_class(assignment, context={'structure_field': self.structure_field}).data,
            status=status.HTTP_201_CREATED,
        )


class AssignmentCloseView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]
    assignment_model = None
    serializer_class = None
    structure_field = ''

    def post(self, request, assignment_id):
        assignment = get_object_or_404(
            self.assignment_model.objects.select_related(self.structure_field),
            pk=assignment_id,
            **{f'{self.structure_field}__church__organization': TenantContext.get()},
        )
        serializer = AssignmentCloseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignment = close_assignment(
            assignment,
            request.user,
            serializer.validated_data.get('ended_reason', ''),
        )
        return Response(self.serializer_class(assignment, context={'structure_field': self.structure_field}).data)


class LeadershipListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]
    structure_model = None
    leadership_model = None
    serializer_class = None
    structure_field = ''
    is_tribe = False

    def get_structure(self, pk):
        return get_object_or_404(
            self.structure_model.objects.select_related('church'),
            pk=pk,
            church__organization=TenantContext.get(),
        )

    def get(self, request, pk):
        structure = self.get_structure(pk)
        queryset = self.leadership_model.objects.filter(**{self.structure_field: structure}).select_related(
            'member', self.structure_field, 'appointed_by', 'ended_by',
        )
        if request.query_params.get('history') != '1':
            queryset = queryset.filter(ended_at__isnull=True)
        serializer = self.serializer_class(
            queryset,
            many=True,
            context={'structure_field': self.structure_field},
        )
        return Response(serializer.data)

    def post(self, request, pk):
        structure = self.get_structure(pk)
        serializer = LeadershipCreateSerializer(data=request.data, context={'structure': structure})
        serializer.is_valid(raise_exception=True)
        leadership = appoint_leader(
            structure,
            serializer.validated_data['member'],
            serializer.validated_data['role'],
            request.user,
            is_tribe=self.is_tribe,
        )
        return Response(
            self.serializer_class(leadership, context={'structure_field': self.structure_field}).data,
            status=status.HTTP_201_CREATED,
        )


class LeadershipCloseView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]
    leadership_model = None
    serializer_class = None
    structure_field = ''

    def post(self, request, leadership_id):
        leadership = get_object_or_404(
            self.leadership_model.objects.select_related(self.structure_field),
            pk=leadership_id,
            **{f'{self.structure_field}__church__organization': TenantContext.get()},
        )
        serializer = LeadershipCloseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        leadership = close_leadership(
            leadership,
            request.user,
            serializer.validated_data.get('ended_reason', ''),
        )
        return Response(self.serializer_class(leadership, context={'structure_field': self.structure_field}).data)


class MinistryListView(StructureListCreateView):
    structure_model = Ministry
    serializer_class = MinistrySerializer
    assignment_model = MinistryAssignment
    leadership_model = MinistryLeadership
    structure_field = 'ministry'


class MinistryDetailView(StructureDetailView):
    structure_model = Ministry
    serializer_class = MinistrySerializer


class MinistryAssignmentListView(AssignmentListCreateView):
    structure_model = Ministry
    assignment_model = MinistryAssignment
    serializer_class = MinistryAssignmentSerializer
    structure_field = 'ministry'


class MinistryAssignmentCloseView(AssignmentCloseView):
    assignment_model = MinistryAssignment
    serializer_class = MinistryAssignmentSerializer
    structure_field = 'ministry'


class MinistryLeadershipListView(LeadershipListCreateView):
    structure_model = Ministry
    leadership_model = MinistryLeadership
    serializer_class = MinistryLeadershipSerializer
    structure_field = 'ministry'


class MinistryLeadershipCloseView(LeadershipCloseView):
    leadership_model = MinistryLeadership
    serializer_class = MinistryLeadershipSerializer
    structure_field = 'ministry'


class TribeListView(StructureListCreateView):
    structure_model = Tribe
    serializer_class = TribeSerializer
    assignment_model = TribeAssignment
    leadership_model = TribeLeadership
    structure_field = 'tribe'


class TribeDetailView(StructureDetailView):
    structure_model = Tribe
    serializer_class = TribeSerializer


class TribeAssignmentListView(AssignmentListCreateView):
    structure_model = Tribe
    assignment_model = TribeAssignment
    serializer_class = TribeAssignmentSerializer
    structure_field = 'tribe'
    is_tribe = True


class TribeAssignmentCloseView(AssignmentCloseView):
    assignment_model = TribeAssignment
    serializer_class = TribeAssignmentSerializer
    structure_field = 'tribe'


class TribeLeadershipListView(LeadershipListCreateView):
    structure_model = Tribe
    leadership_model = TribeLeadership
    serializer_class = TribeLeadershipSerializer
    structure_field = 'tribe'
    is_tribe = True


class TribeLeadershipCloseView(LeadershipCloseView):
    leadership_model = TribeLeadership
    serializer_class = TribeLeadershipSerializer
    structure_field = 'tribe'
