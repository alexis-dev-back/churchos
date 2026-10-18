from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.members.serializers import AcceptInvitationSerializer
from apps.members.services.member_service import MemberService


class AcceptInvitationView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'invite_accept'

    def post(self, request):
        serializer = AcceptInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = MemberService.accept_invitation(
            serializer.validated_data['token'],
            serializer.validated_data['email'],
            serializer.validated_data['username'],
            serializer.validated_data['password'],
        )
        return Response({
            'detail': 'Invitation acceptée avec succès.',
            'member_id': str(member.id),
            'status': member.status,
        }, status=status.HTTP_200_OK)
