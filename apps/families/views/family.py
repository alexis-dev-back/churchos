from django.db.models import CharField, Count, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, Concat
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.families.models import Family, FamilyMember
from apps.families.serializers import FamilySerializer
from apps.families.services.family_service import FamilyService
from infrastructure.tenancy import TenantContext
from apps.members.permissions import IsTenantAdmin


def family_summary_queryset(queryset):
    primary_contacts = FamilyMember.objects.filter(
        Q(is_primary_contact=True) | Q(relationship_type=FamilyMember.RelationshipType.HEAD),
        family_id=OuterRef('pk'),
        left_at__isnull=True,
    ).order_by('-is_primary_contact', '-joined_at')
    return queryset.annotate(
        member_count=Count(
            'memberships',
            filter=Q(memberships__left_at__isnull=True),
            distinct=True,
        ),
        primary_contact_name=Subquery(
            primary_contacts.annotate(
                contact_name=Concat(
                    Coalesce('member__first_name', 'person__first_name', output_field=CharField()),
                    Value(' '),
                    Coalesce('member__last_name', 'person__last_name', output_field=CharField()),
                    output_field=CharField(),
                ),
            ).values('contact_name')[:1],
        ),
        primary_contact_phone=Subquery(
            primary_contacts.annotate(
                contact_phone=Coalesce('member__phone', 'person__phone', output_field=CharField()),
            ).values('contact_phone')[:1],
        ),
    )


class FamilyListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get(self, request):
        organization = TenantContext.get()
        families = family_summary_queryset(Family.objects.filter(organization=organization))
        return Response(FamilySerializer(families, many=True).data)

    def post(self, request):
        serializer = FamilySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        family = FamilyService.create_family(serializer.validated_data, request.user)
        return Response(FamilySerializer(family).data, status=status.HTTP_201_CREATED)


class FamilyDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get_object(self, pk):
        organization = TenantContext.get()
        return Family.objects.filter(pk=pk, organization=organization).first()

    def get(self, request, pk):
        family = self.get_object(pk)
        if family is None:
            return Response({'detail': 'Famille introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        family = family_summary_queryset(Family.objects.filter(pk=family.pk)).get()
        return Response(FamilySerializer(family).data)

    def patch(self, request, pk):
        family = self.get_object(pk)
        if family is None:
            return Response({'detail': 'Famille introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = FamilySerializer(family, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        family = family_summary_queryset(Family.objects.filter(pk=family.pk)).get()
        return Response(FamilySerializer(family).data)

    def delete(self, request, pk):
        family = self.get_object(pk)
        if family is None:
            return Response({'detail': 'Famille introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        family.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
