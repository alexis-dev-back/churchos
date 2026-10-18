from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Church, Organization, OrganizationMembership
from apps.ecodim.models import (
    EcodimAuditLog,
    EcodimChild,
    EcodimClass,
    EcodimPickupAuthorization,
    EcodimStaffAssignment,
)
from apps.members.models import Member


User = get_user_model()


class EcodimApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='ecodim-admin', password='Strong-password-123')
        self.organization = Organization.objects.create(name='ECODIM Test', slug='ecodim-test-org')
        OrganizationMembership.objects.create(
            user=self.user,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.church = Church.objects.create(
            organization=self.organization,
            name='ECODIM Church',
            slug='ecodim-test-church',
        )
        refresh = RefreshToken.for_user(self.user)
        refresh['tenant_id'] = str(self.organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

    def test_dashboard_returns_real_tenant_scoped_metrics(self):
        EcodimChild.objects.create(
            organization=self.organization,
            church=self.church,
            reference='EC-TEST-001',
            first_name='Anne',
            last_name='Test',
        )

        response = self.client.get(
            '/api/ecodim/dashboard/',
            {'church_id': str(self.church.church_id)},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['summary']['active_children'], 1)
        self.assertEqual(response.data['summary']['active_classes'], 0)
        self.assertEqual(len(response.data['registrations_by_month']), 6)

    def test_child_creation_uses_the_authenticated_organization(self):
        member = Member.objects.create(
            user=self.user,
            organization=self.organization,
            church=self.church,
            first_name='Existing',
            last_name='Member',
            email='existing-member@example.com',
            status=Member.Status.ACTIVE,
        )
        response = self.client.post('/api/ecodim/children/', {
            'church_id': str(self.church.church_id),
            'first_name': 'Samuel',
            'last_name': 'Doe',
            'member': str(member.pk),
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        child = EcodimChild.objects.get(pk=response.data['id'])
        self.assertEqual(child.organization_id, self.organization.tenant_id)
        self.assertEqual(child.church_id, self.church.church_id)
        self.assertEqual(child.member_id, member.pk)
        self.assertTrue(child.reference.startswith('EC-'))

    def test_child_cannot_be_linked_to_a_member_from_another_church(self):
        other_church = Church.objects.create(
            organization=self.organization,
            name='Other Church',
            slug='ecodim-member-other-church',
        )
        other_user = User.objects.create_user(username='other-church-member', password='Strong-password-123')
        other_member = Member.objects.create(
            user=other_user,
            organization=self.organization,
            church=other_church,
            first_name='Other',
            last_name='Member',
            email='other-member@example.com',
            status=Member.Status.ACTIVE,
        )

        response = self.client.post('/api/ecodim/children/', {
            'church_id': str(self.church.church_id),
            'first_name': 'Samuel',
            'last_name': 'Doe',
            'member': str(other_member.pk),
        }, format='json')

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(EcodimChild.objects.exists())

    def test_class_creation_succeeds_and_duplicate_name_returns_validation_error(self):
        payload = {
            'church_id': str(self.church.church_id),
            'name': 'Groupe des 6-11 ans',
            'level': '6-11 ans',
            'minimum_age': 6,
            'maximum_age': 11,
            'capacity': 20,
        }

        created = self.client.post('/api/ecodim/classes/', payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(EcodimClass.objects.filter(church=self.church).count(), 1)

        duplicate = self.client.post('/api/ecodim/classes/', payload, format='json')
        self.assertEqual(duplicate.status_code, 400, duplicate.data)
        self.assertIn('name', duplicate.data)
        self.assertEqual(EcodimClass.objects.filter(church=self.church).count(), 1)

    def test_ecodim_staff_cannot_access_another_church(self):
        staff_user = User.objects.create_user(username='ecodim-staff', password='Strong-password-123')
        OrganizationMembership.objects.create(
            user=staff_user,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        staff_member = Member.objects.create(
            user=staff_user,
            organization=self.organization,
            church=self.church,
            first_name='Staff',
            last_name='Test',
            email='ecodim-staff@example.com',
            status=Member.Status.ACTIVE,
        )
        EcodimStaffAssignment.objects.create(
            member=staff_member,
            church=self.church,
            role=EcodimStaffAssignment.Role.CHECK_IN,
        )
        other_church = Church.objects.create(
            organization=self.organization,
            name='Other Church',
            slug='ecodim-other-church',
        )
        refresh = RefreshToken.for_user(staff_user)
        refresh['tenant_id'] = str(self.organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

        response = self.client.get(
            '/api/ecodim/dashboard/',
            {'church_id': str(other_church.church_id)},
        )

        self.assertEqual(response.status_code, 403)

    def test_check_in_staff_can_confirm_only_a_valid_authorized_handoff(self):
        child = EcodimChild.objects.create(
            organization=self.organization,
            church=self.church,
            reference='EC-HANDOFF-1',
            first_name='Lina',
            last_name='Test',
        )
        other_child = EcodimChild.objects.create(
            organization=self.organization,
            church=self.church,
            reference='EC-HANDOFF-2',
            first_name='Milo',
            last_name='Test',
        )
        authorization = EcodimPickupAuthorization.objects.create(
            child=child,
            name='Parent Approved',
            authorized_by=self.user,
        )
        other_authorization = EcodimPickupAuthorization.objects.create(
            child=other_child,
            name='Different Parent',
            authorized_by=self.user,
        )
        staff_user = User.objects.create_user(username='check-in-staff', password='Strong-password-123')
        OrganizationMembership.objects.create(
            user=staff_user,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        staff_member = Member.objects.create(
            user=staff_user,
            organization=self.organization,
            church=self.church,
            first_name='Check-in',
            last_name='Staff',
            email='check-in-staff@example.com',
            status=Member.Status.ACTIVE,
        )
        EcodimStaffAssignment.objects.create(
            member=staff_member,
            church=self.church,
            role=EcodimStaffAssignment.Role.CHECK_IN,
        )
        refresh = RefreshToken.for_user(staff_user)
        refresh['tenant_id'] = str(self.organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

        response = self.client.post(
            f'/api/ecodim/children/{child.pk}/handoffs/',
            {'authorization_id': str(authorization.pk), 'verification_note': 'Identité contrôlée'},
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['picked_up_by_name'], authorization.name)
        self.assertTrue(EcodimAuditLog.objects.filter(
            child=child,
            action='CHILD_HANDED_OFF',
        ).exists())
        rejected = self.client.post(
            f'/api/ecodim/children/{child.pk}/handoffs/',
            {'authorization_id': str(other_authorization.pk)},
            format='json',
        )
        self.assertEqual(rejected.status_code, 400, rejected.data)
        forbidden = self.client.post(
            f'/api/ecodim/children/{child.pk}/pickup-authorizations/',
            {'name': 'Unauthorised entry'},
            format='json',
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_revoked_pickup_authorization_cannot_be_reactivated(self):
        child = EcodimChild.objects.create(
            organization=self.organization,
            church=self.church,
            reference='EC-REVOKE-1',
            first_name='Maya',
            last_name='Test',
        )
        authorization = EcodimPickupAuthorization.objects.create(
            child=child,
            name='Former Contact',
            authorized_by=self.user,
        )

        revoked = self.client.patch(
            f'/api/ecodim/children/{child.pk}/pickup-authorizations/{authorization.pk}/',
            {'status': EcodimPickupAuthorization.Status.REVOKED},
            format='json',
        )
        reactivated = self.client.patch(
            f'/api/ecodim/children/{child.pk}/pickup-authorizations/{authorization.pk}/',
            {'status': EcodimPickupAuthorization.Status.ACTIVE},
            format='json',
        )

        self.assertEqual(revoked.status_code, 200, revoked.data)
        self.assertEqual(revoked.data['status'], EcodimPickupAuthorization.Status.REVOKED)
        self.assertEqual(reactivated.status_code, 400, reactivated.data)

    def test_class_capacity_is_enforced_when_enrolling_children(self):
        ecodim_class = EcodimClass.objects.create(
            organization=self.organization,
            church=self.church,
            name='3–5 ans',
            capacity=1,
        )
        children = [
            EcodimChild.objects.create(
                organization=self.organization,
                church=self.church,
                reference=f'EC-CAPACITY-{index}',
                first_name=f'Child {index}',
                last_name='Test',
            )
            for index in (1, 2)
        ]

        first = self.client.post(
            f'/api/ecodim/children/{children[0].pk}/enrollment/',
            {'class_id': str(ecodim_class.pk)},
            format='json',
        )
        second = self.client.post(
            f'/api/ecodim/children/{children[1].pk}/enrollment/',
            {'class_id': str(ecodim_class.pk)},
            format='json',
        )

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 400, second.data)
