from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.members.models import MemberInvitation


def create_invitation(member, created_by):
    return MemberInvitation.create_for_member(member=member, created_by=created_by, ttl_days=7)


def accept_invitation(raw_token, email, username, password):
    from apps.members.services.member_service import MemberService

    return MemberService.accept_invitation(raw_token, email, username, password)
