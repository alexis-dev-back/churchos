from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.communication.models import Announcement
from apps.communication.permissions import HasCommunicationReadAccess, is_communication_manager
from apps.communication.serializers import AnnouncementSerializer
from apps.core.services.configuration_access import configuration_church_ids, has_configuration_permission
from apps.core.services.church_access import accessible_church_ids
from apps.members.models import Member
from infrastructure.tenancy import TenantContext


def _organization():
    organization = TenantContext.get()
    if organization is None:
        raise PermissionDenied('Aucune organisation active dans le contexte de la requête.')
    return organization


def _visible_announcements(user, organization):
    queryset = Announcement.objects.filter(organization=organization).select_related('church', 'created_by')
    if is_communication_manager(user, organization):
        church_ids = set(organization.churches.filter(is_active=True).values_list('church_id', flat=True))
    else:
        church_ids = set(accessible_church_ids(user, organization))

    if has_configuration_permission(user, 'communication.read', organization):
        return queryset.filter(
            scope=Announcement.Scope.CHURCH,
            church_id__in=church_ids,
        )

    church_ids.update(configuration_church_ids(user, organization, 'communication'))
    church_ids.update(Member.objects.filter(
        organization=organization,
        user=user,
        status=Member.Status.ACTIVE,
        church__is_active=True,
    ).values_list('church_id', flat=True))
    return queryset.filter(
        scope=Announcement.Scope.CHURCH,
        church_id__in=church_ids,
    )


class AnnouncementListCreateView(APIView):
    permission_classes = [IsAuthenticated, HasCommunicationReadAccess]

    def get(self, request):
        organization = _organization()
        queryset = _visible_announcements(request.user, organization)
        can_manage_all = is_communication_manager(request.user, organization)
        if not can_manage_all:
            assigned_church_ids = configuration_church_ids(request.user, organization, 'communication')
            manageable_church_ids = [
                church_id for church_id in assigned_church_ids
                if is_communication_manager(request.user, organization, church=church_id)
            ]
            queryset = queryset.filter(
                (
                    Q(status=Announcement.Status.PUBLISHED)
                    & (Q(publish_at__isnull=True) | Q(publish_at__lte=timezone.now()))
                )
                | Q(church_id__in=manageable_church_ids)
            )

        status_filter = request.query_params.get('status', '').strip().upper()
        if status_filter in Announcement.Status.values:
            queryset = queryset.filter(status=status_filter)
        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(Q(title__icontains=search) | Q(body__icontains=search))
        return Response(AnnouncementSerializer(queryset[:100], many=True).data)

    def post(self, request):
        organization = _organization()
        serializer = AnnouncementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        church = serializer.validated_data.get('church')
        if not is_communication_manager(request.user, organization, church=church):
            raise PermissionDenied('Vous devez disposer du droit de gérer les communications pour diffuser ici.')
        announcement = serializer.save(
            organization=organization,
            created_by=request.user,
            publish_at=(
                serializer.validated_data.get('publish_at') or timezone.now()
                if serializer.validated_data.get('status') == Announcement.Status.PUBLISHED
                else None
            ),
        )
        return Response(AnnouncementSerializer(announcement).data, status=status.HTTP_201_CREATED)


class AnnouncementDetailView(APIView):
    permission_classes = [IsAuthenticated, HasCommunicationReadAccess]

    def _announcement(self, request, announcement_id):
        announcement = get_object_or_404(
            _visible_announcements(request.user, _organization()),
            pk=announcement_id,
        )
        if not is_communication_manager(request.user, announcement.organization, church=announcement.church):
            if announcement.status != Announcement.Status.PUBLISHED or (
                announcement.publish_at and announcement.publish_at > timezone.now()
            ):
                from django.http import Http404

                raise Http404
        return announcement

    def get(self, request, announcement_id):
        return Response(AnnouncementSerializer(self._announcement(request, announcement_id)).data)

    def patch(self, request, announcement_id):
        announcement = self._announcement(request, announcement_id)
        if not is_communication_manager(request.user, announcement.organization, church=announcement.church):
            raise PermissionDenied('Vous devez disposer du droit de gérer les communications.')
        serializer = AnnouncementSerializer(announcement, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        church = serializer.validated_data.get('church', announcement.church)
        if not is_communication_manager(request.user, announcement.organization, church=church):
            raise PermissionDenied('Vous ne pouvez pas gérer les communications de cette église.')

        announcement = serializer.save(
            publish_at=(
                serializer.validated_data.get('publish_at') or timezone.now()
                if serializer.validated_data.get('status', announcement.status) == Announcement.Status.PUBLISHED
                and announcement.status != Announcement.Status.PUBLISHED
                else serializer.validated_data.get('publish_at', announcement.publish_at)
            ),
        )
        return Response(AnnouncementSerializer(announcement).data)

    def delete(self, request, announcement_id):
        announcement = self._announcement(request, announcement_id)
        if not is_communication_manager(request.user, announcement.organization, church=announcement.church):
            raise PermissionDenied('Vous devez disposer du droit de gérer les communications.')
        if announcement.status != Announcement.Status.DRAFT:
            raise PermissionDenied('Seuls les brouillons peuvent être supprimés.')
        announcement.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CommunicationContextView(APIView):
    permission_classes = [IsAuthenticated, HasCommunicationReadAccess]

    def get(self, request):
        organization = _organization()
        org_manage = is_communication_manager(request.user, organization)
        scoped_church_ids = configuration_church_ids(request.user, organization, 'communication')
        manageable_church_ids = [
            str(church_id) for church_id in scoped_church_ids
            if is_communication_manager(request.user, organization, church=church_id)
        ]
        if org_manage:
            active_churches = organization.churches.filter(is_active=True)
            manageable_church_ids = [
                str(church_id) for church_id in active_churches.values_list('church_id', flat=True)
            ]
            churches = active_churches
        else:
            churches = organization.churches.filter(
                is_active=True,
                church_id__in=manageable_church_ids,
            ).filter(
                church_id__in=accessible_church_ids(request.user, organization),
            )
        church_data = list(churches.values('church_id', 'name'))
        return Response({
            'can_manage': org_manage or bool(manageable_church_ids),
            'can_manage_all': org_manage,
            'manageable_church_ids': manageable_church_ids,
            'churches': church_data,
        })
