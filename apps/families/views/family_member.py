from django.db.models import Q
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.families.models import Family, FamilyMember
from apps.families.serializers import FamilyMemberSerializer
from apps.families.services.family_member_service import FamilyMemberService
from apps.members.models import Member
from apps.people.models import Person
from apps.members.permissions import IsTenantAdmin
from infrastructure.tenancy import TenantContext


class FamilyMemberView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get(self, request, pk):
        family = Family.objects.filter(pk=pk, organization=TenantContext.get()).first()
        if family is None:
            return Response({'detail': 'Famille introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        memberships = FamilyMember.objects.filter(family=family).filter(
            Q(member__organization=family.organization)
            | Q(person__organization=family.organization),
        ).select_related('person', 'member')
        return Response(FamilyMemberSerializer(memberships, many=True).data)

    def post(self, request, pk):
        family = Family.objects.filter(pk=pk, organization=TenantContext.get()).first()
        if family is None:
            return Response({'detail': 'Famille introuvable.'}, status=status.HTTP_404_NOT_FOUND)

        payload = request.data.copy()
        payload['family'] = str(family.id)
        serializer = FamilyMemberSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        relationship_type = serializer.validated_data.get('relationship_type', FamilyMember.RelationshipType.OTHER)
        is_primary_contact = serializer.validated_data.get('is_primary_contact', False)
        if serializer.validated_data.get('member'):
            membership = FamilyMemberService.add_member_to_family(
                family,
                serializer.validated_data['member'],
                relationship_type=relationship_type,
                is_primary_contact=is_primary_contact,
            )
        else:
            membership = FamilyMemberService.add_person_to_family(
                family,
                serializer.validated_data['person'],
                relationship_type=relationship_type,
                is_primary_contact=is_primary_contact,
            )
        return Response(FamilyMemberSerializer(membership).data, status=status.HTTP_201_CREATED)
