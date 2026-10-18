from django.shortcuts import get_object_or_404
from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import OrganizationMembership
from apps.core.services.configuration_access import has_configuration_permission
from apps.members.models import Member, MemberInvitation
from apps.members.permissions import IsMemberOwnerOrAdmin, IsTenantAdmin
from apps.members.serializers import MemberDetailSerializer, MemberWriteSerializer
from apps.members.services.member_service import MemberService
from apps.ministries.services import close_member_church_roles
from infrastructure.tenancy import TenantContext


class MemberListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get(self, request):
        organization = TenantContext.get()
        members = Member.objects.filter(organization=organization).select_related('user', 'church', 'campus', 'organization')
        serializer = MemberDetailSerializer(members, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = MemberWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        member, invitation = MemberService.create_member(request.user, serializer.validated_data)
        payload = MemberDetailSerializer(member).data
        payload['invitation_token'] = invitation.token
        return Response(payload, status=status.HTTP_201_CREATED)


class MemberMeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        member = Member.objects.filter(user=request.user).select_related('organization', 'church', 'campus', 'user').first()
        if member is None:
            return Response({'detail': 'Aucun profil membre n’est associé à ce compte.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(MemberDetailSerializer(member).data)


class MemberDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, request, pk):
        member = Member.objects.filter(pk=pk, organization=TenantContext.get()).select_related('organization', 'church', 'campus', 'user').first()
        if member is None:
            return None

        if request.user == member.user:
            return member

        permission_code = 'members.read' if request.method == 'GET' else 'members.update'
        if has_configuration_permission(
            request.user,
            permission_code,
            member.organization,
            church=member.church,
            campus=member.campus,
        ):
            return member

        return None

    def get(self, request, pk):
        member = self.get_object(request, pk)
        if member is None:
            return Response({'detail': 'Membre introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(MemberDetailSerializer(member).data)

    def patch(self, request, pk):
        member = self.get_object(request, pk)
        if member is None:
            return Response({'detail': 'Membre introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = MemberWriteSerializer(member, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(MemberDetailSerializer(member).data)


class MemberSuspendView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    @transaction.atomic
    def post(self, request, pk):
        organization = TenantContext.get()
        member = Member.objects.filter(pk=pk, organization=organization).select_related('user').first()
        if member is None:
            return Response({'detail': 'Membre introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        if not _suspend_organization_membership(member, organization):
            return Response(
                {'detail': 'Impossible de suspendre le dernier administrateur de cette organisation.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        member.status = Member.Status.SUSPENDED
        member.save(update_fields=['status'])
        return Response({'detail': 'Membre suspendu.', 'status': member.status})


class MemberArchiveView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    @transaction.atomic
    def post(self, request, pk):
        member = Member.objects.filter(pk=pk, organization=TenantContext.get()).select_related('user').first()
        if member is None:
            return Response({'detail': 'Membre introuvable.'}, status=status.HTTP_404_NOT_FOUND)
        if not _suspend_organization_membership(member, TenantContext.get()):
            return Response(
                {'detail': 'Impossible d’archiver le dernier administrateur de cette organisation.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        close_member_church_roles(member, request.user)
        member.status = Member.Status.ARCHIVED
        member.save(update_fields=['status'])
        return Response({'detail': 'Membre archivé.', 'status': member.status})


class MemberInvitationView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def post(self, request, pk):
        member = Member.objects.filter(pk=pk, organization=TenantContext.get()).first()
        if member is None:
            return Response({'detail': 'Membre introuvable.'}, status=status.HTTP_404_NOT_FOUND)

        for invitation in member.invitations.filter(status=MemberInvitation.Status.PENDING):
            invitation.status = MemberInvitation.Status.REVOKED
            invitation.save(update_fields=['status'])

        invitation = MemberInvitation.create_for_member(member=member, created_by=request.user, ttl_days=7)
        return Response({'member_id': str(member.id), 'invitation_token': invitation.token}, status=status.HTTP_201_CREATED)


class MemberResendInvitationView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def post(self, request, pk):
        return MemberInvitationView().post(request, pk)


def _suspend_organization_membership(member, organization):
    membership = OrganizationMembership.objects.select_for_update().filter(
        user=member.user,
        organization=organization,
        is_active=True,
    ).first()
    if membership is None:
        return True
    is_admin = membership.role in {OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN}
    if is_admin and not OrganizationMembership.objects.filter(
        organization=organization,
        is_active=True,
        role__in=[OrganizationMembership.Role.OWNER, OrganizationMembership.Role.ADMIN],
    ).exclude(pk=membership.pk).exists():
        return False
    membership.is_active = False
    membership.save(update_fields=['is_active'])
    return True
