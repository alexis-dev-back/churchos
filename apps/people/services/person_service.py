import secrets

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from apps.core.models import Campus, Church, OrganizationMembership
from apps.members.models import Member, MemberInvitation
from apps.members.services.member_service import MemberService
from apps.people.models import Person

User = get_user_model()


class PersonService:
    @staticmethod
    def find_duplicates(data, organization):
        email = (data.get('email') or '').strip().lower()
        phone = (data.get('phone') or '').strip()
        first_name = (data.get('first_name') or '').strip()
        last_name = (data.get('last_name') or '').strip()

        query = Q()
        if email:
            query |= Q(email__iexact=email)
        if phone:
            query |= Q(phone=phone)
        if first_name and last_name:
            query |= (Q(first_name__iexact=first_name) & Q(last_name__iexact=last_name))

        return Person.objects.filter(organization=organization).filter(query).order_by('last_name', 'first_name')[:10]

    @staticmethod
    def create_person(data, organization, user=None):
        person = Person.objects.create(
            organization=organization,
            user=user,
            first_name=(data.get('first_name') or '').strip(),
            last_name=(data.get('last_name') or '').strip(),
            middle_name=(data.get('middle_name') or '').strip() or None,
            gender=data.get('gender'),
            date_of_birth=data.get('date_of_birth'),
            phone=(data.get('phone') or '').strip() or None,
            email=(data.get('email') or '').strip().lower() or None,
            address=data.get('address'),
            photo=data.get('photo'),
            notes=data.get('notes'),
        )
        return person

    @staticmethod
    def convert_to_member(person, admin_user, data):
        from infrastructure.tenancy import TenantContext

        current_org = TenantContext.get()
        if current_org is None:
            raise ValidationError('Aucune organisation active n’a été sélectionnée.')

        if person.organization_id != current_org.tenant_id:
            raise ValidationError('Cette personne ne fait pas partie de l’organisation active.')

        if person.user is not None:
            raise ValidationError('Cette personne a déjà un compte utilisateur associé.')

        email = (person.email or data.get('email') or '').strip().lower()
        if not email:
            raise ValidationError('Une adresse email est obligatoire pour créer le compte membre.')

        church_id = data.get('church')
        campus_id = data.get('campus')
        if not church_id:
            raise ValidationError('L’église est obligatoire.')

        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('Un compte utilisateur existe déjà avec cet email.')

        if Member.objects.filter(organization=current_org, email__iexact=email).exists():
            raise ValidationError('Un membre existe déjà dans cette organisation avec cet email.')

        try:
            church = Church.objects.get(church_id=church_id)
        except Church.DoesNotExist as exc:
            raise ValidationError('L’église sélectionnée est introuvable.') from exc

        if church.organization_id != current_org.tenant_id:
            raise ValidationError('L’église ne fait pas partie de l’organisation active.')

        campus = None
        if campus_id:
            try:
                campus = Campus.objects.get(campus_id=campus_id)
            except Campus.DoesNotExist as exc:
                raise ValidationError('Le campus sélectionné est introuvable.') from exc
            if campus.church_id != church.church_id:
                raise ValidationError('Le campus ne correspond pas à l’église sélectionnée.')

        with transaction.atomic():
            username = MemberService._generate_username(person.first_name, person.last_name)
            user = User.objects.create_user(
                username=username,
                email=email,
                password=secrets.token_urlsafe(24),
                is_active=False,
            )
            person.user = user
            person.email = email
            if person.phone is None:
                person.phone = data.get('phone')
            person.save(update_fields=['user', 'email', 'phone'])

            member = Member.objects.create(
                user=user,
                organization=current_org,
                church=church,
                campus=campus,
                first_name=person.first_name,
                last_name=person.last_name,
                email=email,
                phone=person.phone or data.get('phone', ''),
                gender=data.get('gender', person.gender or ''),
                date_of_birth=person.date_of_birth or data.get('date_of_birth'),
                address=data.get('address') or person.address or '',
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
