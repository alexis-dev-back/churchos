from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Church, Campus, Organization, OrganizationMembership
from apps.members.models import Member, MemberInvitation

User = get_user_model()


class MemberFlowTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner',
            email='owner@example.com',
            password='Strong-password-123',
        )
        self.organization = Organization.objects.create(
            name='Organization A',
            slug='organization-a',
            owner=self.owner,
        )
        OrganizationMembership.objects.create(
            user=self.owner,
            organization=self.organization,
            role=OrganizationMembership.Role.OWNER,
        )
        self.church = Church.objects.create(
            organization=self.organization,
            name='Church A',
            slug='church-a',
            pastor_name='Pastor A',
        )
        self.campus = Campus.objects.create(
            church=self.church,
            name='Campus A',
            slug='campus-a',
        )

    def _auth(self, user=None):
        user = user or self.owner
        token = RefreshToken.for_user(user)
        token['tenant_id'] = str(self.organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')

    def test_admin_can_create_member_and_invitation(self):
        self._auth()

        response = self.client.post('/api/members/', {
            'first_name': 'Jean',
            'last_name': 'Dupont',
            'email': 'jean@example.com',
            'phone': '+22501020304',
            'gender': 'M',
            'date_of_birth': '1995-01-15',
            'church': str(self.church.church_id),
            'campus': str(self.campus.campus_id),
        }, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertIn('invitation_token', response.data)
        self.assertRegex(response.data['invitation_token'], r'^\d{5}$')
        member = Member.objects.get(email='jean@example.com')
        self.assertEqual(member.status, Member.Status.PENDING)
        self.assertFalse(member.user.is_active)
        self.assertTrue(MemberInvitation.objects.filter(member=member).exists())

    def test_admin_can_create_member_without_campus(self):
        self._auth()

        response = self.client.post('/api/members/', {
            'first_name': 'Sans',
            'last_name': 'Campus',
            'email': 'sans-campus@example.com',
            'church': str(self.church.church_id),
            'campus': None,
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(Member.objects.get(email='sans-campus@example.com').campus)

    def test_cannot_create_member_for_other_organization_church(self):
        other_user = User.objects.create_user(
            username='other-admin',
            email='other-admin@example.com',
            password='Strong-password-123',
        )
        other_org = Organization.objects.create(
            name='Organization B',
            slug='organization-b',
            owner=other_user,
        )
        OrganizationMembership.objects.create(
            user=other_user,
            organization=other_org,
            role=OrganizationMembership.Role.OWNER,
        )
        other_church = Church.objects.create(
            organization=other_org,
            name='Church B',
            slug='church-b',
            pastor_name='Pastor B',
        )

        self._auth()
        response = self.client.post('/api/members/', {
            'first_name': 'Other',
            'last_name': 'Member',
            'email': 'other@example.com',
            'church': str(other_church.church_id),
            'campus': str(self.campus.campus_id),
        }, format='json')

        self.assertEqual(response.status_code, 400)

    def test_accept_invitation_activates_member_account(self):
        self._auth()
        create_response = self.client.post('/api/members/', {
            'first_name': 'Marie',
            'last_name': 'Lemoine',
            'email': 'marie@example.com',
            'phone': '+22507080910',
            'gender': 'F',
            'date_of_birth': '2000-10-20',
            'church': str(self.church.church_id),
            'campus': str(self.campus.campus_id),
        }, format='json')

        self.assertEqual(create_response.status_code, 201)
        token = create_response.data['invitation_token']
        self.assertRegex(token, r'^\d{5}$')

        self.client.credentials()
        mismatched_email = self.client.post('/api/auth/accept-invitation/', {
            'token': token,
            'email': 'someone-else@example.com',
            'username': 'marie.lemoine',
            'password': 'Strong-password-123',
            'password_confirmation': 'Strong-password-123',
        }, format='json')
        self.assertEqual(mismatched_email.status_code, 400)

        response = self.client.post('/api/auth/accept-invitation/', {
            'token': token,
            'email': 'marie@example.com',
            'username': 'marie.lemoine',
            'password': 'Strong-password-123',
            'password_confirmation': 'Strong-password-123',
        }, format='json')

        self.assertEqual(response.status_code, 200)
        invitation = MemberInvitation.objects.get(member__email='marie@example.com')
        invitation.refresh_from_db()
        member = Member.objects.get(email='marie@example.com')
        self.assertEqual(member.status, Member.Status.ACTIVE)
        self.assertTrue(member.user.is_active)
        self.assertEqual(member.user.username, 'marie.lemoine')
        self.assertIsNotNone(invitation.used_at)

    def test_member_can_view_own_profile(self):
        self._auth()
        create_response = self.client.post('/api/members/', {
            'first_name': 'Luc',
            'last_name': 'Martin',
            'email': 'luc@example.com',
            'phone': '+22506050403',
            'gender': 'M',
            'date_of_birth': '1990-03-10',
            'church': str(self.church.church_id),
            'campus': str(self.campus.campus_id),
        }, format='json')
        self.assertEqual(create_response.status_code, 201)

        member = Member.objects.get(email='luc@example.com')
        member.user.is_active = True
        member.user.save(update_fields=['is_active'])
        token = RefreshToken.for_user(member.user)
        token['tenant_id'] = str(self.organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')

        response = self.client.get('/api/members/me/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['email'], 'luc@example.com')

    def test_suspending_member_only_deactivates_membership_in_active_organization(self):
        user = User.objects.create_user(
            username='multi-org-member',
            email='multi-org-member@example.com',
            password='Strong-password-123',
            is_active=True,
        )
        member = Member.objects.create(
            user=user,
            organization=self.organization,
            church=self.church,
            campus=self.campus,
            first_name='Multi',
            last_name='Organization',
            email=user.email,
            status=Member.Status.ACTIVE,
        )
        membership = OrganizationMembership.objects.create(
            user=user,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        other_organization = Organization.objects.create(name='Organization B', slug='organization-b')
        other_membership = OrganizationMembership.objects.create(
            user=user,
            organization=other_organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        self._auth()

        response = self.client.post(f'/api/members/{member.pk}/suspend/')

        self.assertEqual(response.status_code, 200, response.data)
        user.refresh_from_db()
        membership.refresh_from_db()
        other_membership.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertFalse(membership.is_active)
        self.assertTrue(other_membership.is_active)
