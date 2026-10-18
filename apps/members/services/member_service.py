from __future__ import annotations

import hashlib
import secrets
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.models import Campus, Church, Organization, OrganizationMembership
from apps.members.models import Member, MemberInvitation

User = get_user_model()


class MemberService:
    @staticmethod
    def _generate_username(first_name, last_name):
        base = f'{first_name.lower()}.{last_name.lower()}'.replace(' ', '').strip('.')
        if not base:
            base = 'member'
        candidate = base
        index = 1
        while User.objects.filter(username=candidate).exists():
            candidate = f'{base}{index}'
            index += 1
        return candidate

    @staticmethod
    def _validate_current_context(current_organization):
        if current_organization is None:
            raise ValidationError('Aucune organisation active n’a été sélectionnée.')

    @staticmethod
    def create_member(admin_user, data):
        current_org = getattr(admin_user, 'current_organization', None)
        # support requirement + auth context when the request is tenant-scoped
        from infrastructure.tenancy import TenantContext

        current_org = TenantContext.get() or current_org
        MemberService._validate_current_context(current_org)

        church = data.get('church')
        campus = data.get('campus')
        church_id = getattr(church, 'church_id', church)
        campus_id = getattr(campus, 'campus_id', campus)
        email = (data.get('email') or '').strip().lower()

        if not church_id:
            raise ValidationError('L’église est obligatoire.')

        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('Un compte utilisateur existe déjà avec cet email.')

        if Member.objects.filter(organization=current_org, email__iexact=email).exists():
            raise ValidationError('Un membre existe déjà dans cette organisation pour cet email.')

        try:
            church = Church.objects.get(church_id=church_id)
        except Church.DoesNotExist as exc:
            raise ValidationError('L’église sélectionnée est introuvable.') from exc

        if church.organization_id != current_org.tenant_id:
            raise ValidationError('L’église ne fait pas partie de votre organisation active.')

        campus = None
        if campus_id:
            try:
                campus = Campus.objects.get(campus_id=campus_id)
            except Campus.DoesNotExist as exc:
                raise ValidationError('Le campus sélectionné est introuvable.') from exc
            if campus.church_id != church.church_id:
                raise ValidationError('Le campus ne correspond pas à l’église sélectionnée.')

        with transaction.atomic():
            username = MemberService._generate_username(data.get('first_name', 'Member'), data.get('last_name', 'User'))
            user = User.objects.create_user(
                username=username,
                email=email,
                password=secrets.token_urlsafe(24),
                is_active=False,
            )
            member = Member.objects.create(
                user=user,
                organization=current_org,
                church=church,
                campus=campus,
                first_name=data.get('first_name', '').strip(),
                last_name=data.get('last_name', '').strip(),
                email=email,
                phone=data.get('phone', ''),
                gender=data.get('gender', ''),
                date_of_birth=data.get('date_of_birth'),
                address=data.get('address', ''),
                marital_status=data.get('marital_status', ''),
                status=Member.Status.PENDING,
            )
            OrganizationMembership.objects.get_or_create(
                user=user,
                organization=current_org,
                defaults={'role': OrganizationMembership.Role.MEMBER},
            )
            invitation = MemberInvitation.create_for_member(
                member=member,
                created_by=admin_user,
                ttl_days=7,
            )
            invitation.token = invitation.token or getattr(invitation, 'token', None)
            return member, invitation

    @staticmethod
    @transaction.atomic
    def accept_invitation(raw_token, email, username, password):
        if not raw_token:
            raise ValidationError({'token': 'Le code d’invitation est requis.'})

        token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
        invitation = MemberInvitation.objects.select_for_update().filter(
            token_hash=token_hash,
            member__email__iexact=email,
        ).select_related('member', 'member__user').first()
        if invitation is None:
            raise ValidationError('Le code ou l’adresse e-mail ne correspond pas à une invitation valide.')

        if invitation.status in [MemberInvitation.Status.USED, MemberInvitation.Status.REVOKED, MemberInvitation.Status.EXPIRED]:
            raise ValidationError('Cette invitation ne peut plus être utilisée.')

        if invitation.expires_at <= timezone.now():
            invitation.status = MemberInvitation.Status.EXPIRED
            invitation.save(update_fields=['status'])
            raise ValidationError('Cette invitation a expiré.')

        if invitation.used_at is not None:
            raise ValidationError('Cette invitation a déjà été utilisée.')

        user = invitation.member.user
        if User.objects.filter(username__iexact=username).exclude(pk=user.pk).exists():
            raise ValidationError({'username': 'Ce nom utilisateur est déjà utilisé.'})

        try:
            with transaction.atomic():
                user.username = username
                user.set_password(password)
                user.is_active = True
                user.save(update_fields=['username', 'password', 'is_active'])
        except IntegrityError as exc:
            raise ValidationError({'username': 'Ce nom utilisateur est déjà utilisé.'}) from exc

        OrganizationMembership.objects.get_or_create(
            user=user,
            organization=invitation.member.organization,
            defaults={'role': OrganizationMembership.Role.MEMBER},
        )

        invitation.member.status = Member.Status.ACTIVE
        invitation.member.save(update_fields=['status'])

        invitation.used_at = timezone.now()
        invitation.status = MemberInvitation.Status.USED
        invitation.save(update_fields=['used_at', 'status'])

        return invitation.member

    @staticmethod
    def get_current_member_for_user(user):
        return Member.objects.filter(user=user).select_related('organization', 'church', 'campus', 'user').first()
