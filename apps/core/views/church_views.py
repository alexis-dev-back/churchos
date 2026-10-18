from django.shortcuts import get_object_or_404
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.core.models.church import Church
from apps.core.models.campus import Campus
from apps.core.models.organizations import OrganizationMembership
from apps.core.serializers import ChurchSerializer
from apps.members.models import Member
from apps.events.permissions import managed_church_ids
from infrastructure.tenancy import TenantContext
from infrastructure.tenancy.permissions import HasActiveTenant, HasTenantAdminRole


class ChurchContextView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasActiveTenant]

    def get(self, request):
        organization = TenantContext.get()
        is_organization_admin = OrganizationMembership.objects.filter(
            user=request.user,
            organization=organization,
            role__in=[OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN],
        ).exists()
        member_church_id = Member.objects.filter(
            user=request.user,
            organization=organization,
            status=Member.Status.ACTIVE,
        ).values_list('church_id', flat=True).first()

        if is_organization_admin:
            churches = Church.objects.filter(organization=organization, is_active=True)
        else:
            managed_ids = managed_church_ids(request.user, organization)
            if managed_ids:
                churches = Church.objects.filter(
                    organization=organization,
                    is_active=True,
                    church_id__in=managed_ids,
                )
            elif member_church_id:
                churches = Church.objects.filter(
                    organization=organization,
                    is_active=True,
                    church_id=member_church_id,
                )
            else:
                churches = Church.objects.none()

        churches = churches.order_by('name')
        church_ids = list(churches.values_list('church_id', flat=True))
        default_church_id = (
            member_church_id if member_church_id in church_ids
            else church_ids[0] if len(church_ids) == 1
            else None
        )
        church_selection_fixed = bool(
            default_church_id
            and (member_church_id == default_church_id or len(church_ids) == 1)
        )
        campuses = Campus.objects.filter(
            church_id__in=church_ids,
            is_active=True,
        ).order_by('name')
        return Response({
            'churches': ChurchSerializer(churches, many=True).data,
            'campuses': [{
                'campus_id': str(campus.campus_id),
                'name': campus.name,
                'church': str(campus.church_id),
            } for campus in campuses],
            'default_church_id': str(default_church_id) if default_church_id else None,
            'church_selection_fixed': church_selection_fixed,
        })


#Affichage de toutes les églises associées à l'utilisateur
class ChurchView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasActiveTenant]

    def get(self, request):
        churches = Church.objects.filter(
            organization=TenantContext.get(),
            organization__memberships__user=request.user,
        ).distinct()
        if not churches.exists():
            return Response(
                {"detail": "Aucune église associée à cet utilisateur."},
                status=404)
        serializer = ChurchSerializer(churches, many=True)
        return Response(serializer.data)

#Création d'une nouvelle église
class ChurchCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasActiveTenant, HasTenantAdminRole]

    def post(self, request):
        serializer = ChurchSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            church = serializer.save()
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

#Détails d'une église
class ChurchDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasActiveTenant]

    def get(self, request, church_id):
        church = get_object_or_404(
            Church.objects.filter(
                organization=TenantContext.get(),
                organization__memberships__user=request.user,
            ),
            church_id=church_id,
        )
        serializer = ChurchSerializer(church)
        return Response(serializer.data)

#Suppression d'une église
class ChurchDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasActiveTenant, HasTenantAdminRole]

    def delete(self, request, church_id):
        church = get_object_or_404(
            Church.objects.filter(
                organization=TenantContext.get(),
                organization__memberships__user=request.user,
            ),
            church_id=church_id,
        )
        church.delete()
        return Response(status=204)

#Mise à jour d'une église
class ChurchUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasActiveTenant, HasTenantAdminRole]

    def put(self, request, church_id):
        church = get_object_or_404(
            Church.objects.filter(
                organization=TenantContext.get(),
                organization__memberships__user=request.user,
            ),
            church_id=church_id,
        )
        serializer = ChurchSerializer(
            church,
            data=request.data,
            partial=True,
            context={'request': request},
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)
    