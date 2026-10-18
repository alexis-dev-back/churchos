from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models.organizations import Organization, OrganizationMembership
from apps.core.permissions import IsSuperuser
from apps.core.serializers import (
    ChurchSerializer,
    OrganizationMemberSerializer,
    OrganizationSerializer,
    RegistrationSerializer,
)

User = get_user_model()


class SuperAdminOrganizationsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsSuperuser]

    def get(self, request):
        return Response(OrganizationSerializer(Organization.objects.all(), many=True).data)

    def post(self, request):
        serializer = OrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = serializer.save(owner=None)
        return Response(OrganizationSerializer(organization).data, status=status.HTTP_201_CREATED)


class SuperAdminOrganizationDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsSuperuser]

    def get_object(self, tenant_id):
        return get_object_or_404(Organization, tenant_id=tenant_id)

    def get(self, request, tenant_id):
        return Response(OrganizationSerializer(self.get_object(tenant_id)).data)

    def put(self, request, tenant_id):
        organization = self.get_object(tenant_id)
        serializer = OrganizationSerializer(organization, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, tenant_id):
        self.get_object(tenant_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SuperAdminOrganizationUsersView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsSuperuser]

    def post(self, request, tenant_id):
        organization = get_object_or_404(Organization, tenant_id=tenant_id)
        serializer = OrganizationMemberSerializer(data=request.data, context={'organization': organization})
        serializer.is_valid(raise_exception=True)
        membership = serializer.save()
        return Response({
            'username': membership.user.get_username(),
            'organization_id': str(organization.tenant_id),
            'role': membership.role,
        }, status=status.HTTP_201_CREATED)


class SuperAdminUserCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsSuperuser]

    @transaction.atomic
    def post(self, request):
        data = request.data.copy()
        tenant_id = data.pop('tenant_id', None)
        role = data.pop('role', OrganizationMembership.Role.ADMIN)
        if role not in (OrganizationMembership.Role.ADMIN, OrganizationMembership.Role.MEMBER):
            return Response({'role': 'Le rôle doit être admin ou member.'}, status=status.HTTP_400_BAD_REQUEST)

        organization = None
        if tenant_id:
            organization = get_object_or_404(Organization, tenant_id=tenant_id)

        serializer = RegistrationSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        if organization is not None:
            OrganizationMembership.objects.create(user=user, organization=organization, role=role)
        return Response({
            'id': user.pk,
            'username': user.get_username(),
            'email': user.email,
            'organization_id': str(organization.tenant_id) if organization else None,
            'role': role if organization else None,
        }, status=status.HTTP_201_CREATED)


class SuperAdminChurchesView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsSuperuser]

    def get(self, request, tenant_id):
        organization = get_object_or_404(Organization, tenant_id=tenant_id)
        churches = organization.churches.all()
        return Response(ChurchSerializer(churches, many=True).data)

    def post(self, request, tenant_id):
        organization = get_object_or_404(Organization, tenant_id=tenant_id)
        data = request.data.copy()
        data['organization'] = str(organization.tenant_id)
        serializer = ChurchSerializer(data=data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        church = serializer.save()
        return Response(ChurchSerializer(church).data, status=status.HTTP_201_CREATED)


class SuperAdminChurchDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsSuperuser]

    def get_object(self, church_id):
        from apps.core.models import Church
        return get_object_or_404(Church, church_id=church_id)

    def get(self, request, church_id):
        church = self.get_object(church_id)
        return Response(ChurchSerializer(church).data)

    def put(self, request, church_id):
        church = self.get_object(church_id)
        serializer = ChurchSerializer(church, data=request.data, partial=True, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ChurchSerializer(church).data)

    def delete(self, request, church_id):
        self.get_object(church_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
