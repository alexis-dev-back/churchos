from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models.organizations import Organization, OrganizationMembership
from apps.core.permissions import IsSuperuser
from apps.core.serializers import OrganizationMemberSerializer, OrganizationSerializer
from infrastructure.tenancy import TenantContext
from infrastructure.tenancy.permissions import HasActiveTenant


class OrganizationMemberCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasActiveTenant]

    def post(self, request, tenant_id):
        organization = get_object_or_404(
            Organization,
            tenant_id=tenant_id,
            tenant_id__exact=TenantContext.get().tenant_id,
        )
        requester_membership = OrganizationMembership.objects.filter(
            organization=organization,
            user=request.user,
        ).first()
        if requester_membership is None or requester_membership.role not in (
            OrganizationMembership.Role.OWNER,
            OrganizationMembership.Role.ADMIN,
        ):
            return Response(
                {'detail': 'Seuls le propriétaire ou un administrateur peuvent ajouter des membres.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = OrganizationMemberSerializer(
            data=request.data,
            context={'organization': organization},
        )
        serializer.is_valid(raise_exception=True)
        if (
            requester_membership.role == OrganizationMembership.Role.ADMIN
            and serializer.validated_data['role'] == OrganizationMembership.Role.ADMIN
        ):
            return Response(
                {'role': 'Seul le propriétaire peut nommer un autre administrateur.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        membership = serializer.save()
        return Response(
            {
                'username': membership.user.get_username(),
                'organization_id': str(organization.tenant_id),
                'role': membership.role,
            },
            status=status.HTTP_201_CREATED,
        )


class OrganizationView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        organizations = Organization.objects.filter(memberships__user=request.user).distinct()
        active_tenant = TenantContext.get()
        if active_tenant is not None:
            organizations = organizations.filter(tenant_id=active_tenant.tenant_id)
        serializer = OrganizationSerializer(organizations, many=True)
        return Response(serializer.data)

class OrganisationCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsSuperuser]

    def post(self, request):
        serializer = OrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = serializer.save(owner=None)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

class OrganisationDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, HasActiveTenant]

    def get(self, request, tenant_id):
        organization = get_object_or_404(
            Organization.objects.filter(
                memberships__user=request.user,
                tenant_id=TenantContext.get().tenant_id,
            ),
            tenant_id=tenant_id,
        )
        serializer = OrganizationSerializer(organization)
        return Response(serializer.data)

class OrganisationDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsSuperuser]

    def delete(self, request, tenant_id):
        organization = get_object_or_404(Organization, tenant_id=tenant_id)
        organization.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

class OrganisationUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsSuperuser]

    def put(self, request, tenant_id):
        organization = get_object_or_404(Organization, tenant_id=tenant_id)
        serializer = OrganizationSerializer(organization, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)