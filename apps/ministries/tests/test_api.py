from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Church, Organization, OrganizationMembership
from apps.members.models import Member
from apps.ministries.models import (
    LeadershipRole,
    Ministry,
    MinistryAssignment,
    MinistryLeadership,
    StructureStatus,
    Tribe,
    TribeAssignment,
    TribeLeadership,
)

User = get_user_model()


class MinistryTribeApiTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='church-admin', password='Strong-password-123')
        self.organization = Organization.objects.create(name='Org', slug='org', owner=self.admin)
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.church = Church.objects.create(organization=self.organization, name='Church', slug='church')
        self.other_church = Church.objects.create(organization=self.organization, name='Other Church', slug='other-church')
        self.member = self.create_member('member1', self.church)
        self.second_member = self.create_member('member2', self.church)
        self.third_member = self.create_member('member4', self.church)
        self.other_church_member = self.create_member('member3', self.other_church)
        refresh = RefreshToken.for_user(self.admin)
        refresh['tenant_id'] = str(self.organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

    def create_member(self, username, church):
        user = User.objects.create_user(username=username, password='Strong-password-123')
        return Member.objects.create(
            user=user,
            organization=self.organization,
            church=church,
            first_name=username,
            last_name='Member',
            email=f'{username}@example.com',
            status=Member.Status.ACTIVE,
        )

    def create_ministry(self, name='Music', church=None):
        return Ministry.objects.create(church=church or self.church, name=name)

    def create_tribe(self, name='Judah', church=None):
        return Tribe.objects.create(church=church or self.church, name=name)

    def test_create_structures_case_insensitive_names_and_independent_namespaces(self):
        ministry_response = self.client.post('/api/ministries/', {
            'church_id': str(self.church.church_id),
            'name': 'Music',
        }, format='json')
        duplicate_response = self.client.post('/api/ministries/', {
            'church_id': str(self.church.church_id),
            'name': 'mUsIc',
        }, format='json')
        other_church_response = self.client.post('/api/ministries/', {
            'church_id': str(self.other_church.church_id),
            'name': 'Music',
        }, format='json')
        same_name_tribe = self.client.post('/api/tribes/', {
            'church_id': str(self.church.church_id),
            'name': 'Music',
        }, format='json')

        self.assertEqual(ministry_response.status_code, 201, ministry_response.data)
        self.assertEqual(duplicate_response.status_code, 400)
        self.assertEqual(other_church_response.status_code, 201, other_church_response.data)
        self.assertEqual(same_name_tribe.status_code, 201, same_name_tribe.data)

    def test_ministry_list_includes_active_member_count_and_leader_name(self):
        ministry = self.create_ministry('Choir')
        self.client.post(
            f'/api/ministries/{ministry.id}/assignments/',
            {'member': str(self.member.id)},
            format='json',
        )
        self.client.post(
            f'/api/ministries/{ministry.id}/leadership/',
            {'member': str(self.second_member.id), 'role': LeadershipRole.LEADER},
            format='json',
        )

        response = self.client.get(f'/api/ministries/?church_id={self.church.church_id}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['active_members_count'], 1)
        self.assertEqual(response.data[0]['leader_name'], 'member2 Member')

    def test_ministry_sector_is_writable_and_returned_by_api(self):
        create_response = self.client.post('/api/ministries/', {
            'church_id': str(self.church.church_id),
            'name': 'Diaconie',
            'sector': 'Service',
            'description': 'Servir et soutenir',
        }, format='json')

        self.assertEqual(create_response.status_code, 201, create_response.data)
        self.assertEqual(create_response.data['sector'], 'Service')
        detail_response = self.client.get(f"/api/ministries/{create_response.data['id']}/")
        self.assertEqual(detail_response.data['sector'], 'Service')

    def test_ministry_assignments_allow_multiple_structures_and_preserve_history(self):
        first = self.create_ministry('Choir')
        second = self.create_ministry('Outreach')
        for ministry in (first, second):
            response = self.client.post(
                f'/api/ministries/{ministry.id}/assignments/',
                {'member': str(self.member.id)},
                format='json',
            )
            self.assertEqual(response.status_code, 201, response.data)

        close = self.client.post(
            f'/api/ministries/assignments/{first.assignments.get().id}/close/',
            {'ended_reason': 'Fin de service'},
            format='json',
        )
        self.assertEqual(close.status_code, 200, close.data)
        self.assertIsNotNone(first.assignments.get().left_at)
        history = self.client.get(f'/api/ministries/{first.id}/assignments/?history=1')
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.data), 1)

    def test_ministry_rejects_duplicate_active_and_inactive_assignment(self):
        ministry = self.create_ministry()
        first = self.client.post(
            f'/api/ministries/{ministry.id}/assignments/',
            {'member': str(self.member.id)},
            format='json',
        )
        duplicate = self.client.post(
            f'/api/ministries/{ministry.id}/assignments/',
            {'member': str(self.member.id)},
            format='json',
        )
        ministry.status = StructureStatus.INACTIVE
        ministry.save(update_fields=['status'])
        inactive = self.client.post(
            f'/api/ministries/{ministry.id}/assignments/',
            {'member': str(self.second_member.id)},
            format='json',
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(inactive.status_code, 400)
        self.assertEqual(ministry.assignments.count(), 1)

    def test_tribe_change_closes_previous_assignment_and_retains_history(self):
        first = self.create_tribe('Judah')
        second = self.create_tribe('Benjamin')
        initial = self.client.post(
            f'/api/tribes/{first.id}/assignments/',
            {'member': str(self.member.id)},
            format='json',
        )
        refused_change = self.client.post(
            f'/api/tribes/{second.id}/assignments/',
            {'member': str(self.member.id)},
            format='json',
        )
        changed = self.client.post(
            f'/api/tribes/{second.id}/assignments/',
            {'member': str(self.member.id), 'replace_active': True},
            format='json',
        )

        self.assertEqual(initial.status_code, 201, initial.data)
        self.assertEqual(refused_change.status_code, 400)
        self.assertEqual(changed.status_code, 201, changed.data)
        self.assertEqual(TribeAssignment.objects.filter(member=self.member).count(), 2)
        self.assertEqual(TribeAssignment.objects.filter(member=self.member, left_at__isnull=True).count(), 1)
        self.assertIsNotNone(first.assignments.get().left_at)
        history = self.client.get(f'/api/tribes/{first.id}/assignments/?history=1')
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.data), 1)

    def test_assignments_reject_members_from_another_church(self):
        ministry = self.create_ministry()
        tribe = self.create_tribe()
        ministry_result = self.client.post(
            f'/api/ministries/{ministry.id}/assignments/',
            {'member': str(self.other_church_member.id)},
            format='json',
        )
        tribe_result = self.client.post(
            f'/api/tribes/{tribe.id}/assignments/',
            {'member': str(self.other_church_member.id)},
            format='json',
        )
        self.assertEqual(ministry_result.status_code, 400)
        self.assertEqual(tribe_result.status_code, 400)

    def test_leadership_supports_no_leader_multiple_adjoints_and_history(self):
        ministry = self.create_ministry()
        self.assertEqual(ministry.leadership.count(), 0)
        leader = self.client.post(
            f'/api/ministries/{ministry.id}/leadership/',
            {'member': str(self.member.id), 'role': LeadershipRole.LEADER},
            format='json',
        )
        duplicate_leader = self.client.post(
            f'/api/ministries/{ministry.id}/leadership/',
            {'member': str(self.second_member.id), 'role': LeadershipRole.LEADER},
            format='json',
        )
        adjoint_one = self.client.post(
            f'/api/ministries/{ministry.id}/leadership/',
            {'member': str(self.second_member.id), 'role': LeadershipRole.ADJOINT},
            format='json',
        )
        adjoint_two = self.client.post(
            f'/api/ministries/{ministry.id}/leadership/',
            {'member': str(self.third_member.id), 'role': LeadershipRole.ADJOINT},
            format='json',
        )
        self.assertEqual(leader.status_code, 201, leader.data)
        self.assertEqual(duplicate_leader.status_code, 400)
        self.assertEqual(adjoint_one.status_code, 201, adjoint_one.data)
        self.assertEqual(adjoint_two.status_code, 201, adjoint_two.data)

        close = self.client.post(
            f'/api/ministries/leadership/{leader.data["id"]}/close/',
            {'ended_reason': 'Fin de mandat'},
            format='json',
        )
        replacement = self.client.post(
            f'/api/ministries/{ministry.id}/leadership/',
            {'member': str(self.second_member.id), 'role': LeadershipRole.LEADER},
            format='json',
        )
        self.assertEqual(close.status_code, 200, close.data)
        self.assertEqual(replacement.status_code, 201, replacement.data)
        self.assertEqual(MinistryLeadership.objects.filter(ministry=ministry).count(), 4)

    def test_inactive_structures_keep_history_but_reject_new_roles(self):
        tribe = self.create_tribe()
        assignment = self.client.post(
            f'/api/tribes/{tribe.id}/assignments/',
            {'member': str(self.member.id)},
            format='json',
        )
        leader = self.client.post(
            f'/api/tribes/{tribe.id}/leadership/',
            {'member': str(self.second_member.id), 'role': LeadershipRole.LEADER},
            format='json',
        )
        tribe.status = StructureStatus.INACTIVE
        tribe.save(update_fields=['status'])
        rejected = self.client.post(
            f'/api/tribes/{tribe.id}/assignments/',
            {'member': str(self.second_member.id)},
            format='json',
        )
        reactivated = self.client.patch(f'/api/tribes/{tribe.id}/', {'status': StructureStatus.ACTIVE}, format='json')

        self.assertEqual(assignment.status_code, 201)
        self.assertEqual(leader.status_code, 201)
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(reactivated.status_code, 200, reactivated.data)
        self.assertEqual(TribeAssignment.objects.filter(tribe=tribe).count(), 1)
        self.assertEqual(TribeLeadership.objects.filter(tribe=tribe).count(), 1)

    def test_archive_member_closes_all_church_assignments_and_leadership(self):
        ministry = self.create_ministry()
        tribe = self.create_tribe()
        for path, data in (
            (f'/api/ministries/{ministry.id}/assignments/', {'member': str(self.member.id)}),
            (f'/api/tribes/{tribe.id}/assignments/', {'member': str(self.member.id)}),
            (f'/api/ministries/{ministry.id}/leadership/', {'member': str(self.member.id), 'role': 'LEADER'}),
            (f'/api/tribes/{tribe.id}/leadership/', {'member': str(self.member.id), 'role': 'ADJOINT'}),
        ):
            response = self.client.post(path, data, format='json')
            self.assertEqual(response.status_code, 201, response.data)

        archived = self.client.post(f'/api/members/{self.member.id}/archive/', {}, format='json')
        self.assertEqual(archived.status_code, 200, archived.data)
        self.assertFalse(MinistryAssignment.objects.filter(member=self.member, left_at__isnull=True).exists())
        self.assertFalse(TribeAssignment.objects.filter(member=self.member, left_at__isnull=True).exists())
        self.assertFalse(MinistryLeadership.objects.filter(member=self.member, ended_at__isnull=True).exists())
        self.assertFalse(TribeLeadership.objects.filter(member=self.member, ended_at__isnull=True).exists())

    def test_regular_member_cannot_manage_structures(self):
        member_user = User.objects.create_user(username='ordinary', password='Strong-password-123')
        OrganizationMembership.objects.create(
            user=member_user,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        refresh = RefreshToken.for_user(member_user)
        refresh['tenant_id'] = str(self.organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

        response = self.client.post('/api/ministries/', {
            'church_id': str(self.church.church_id),
            'name': 'Blocked',
        }, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Ministry.objects.filter(name='Blocked').exists())

    def test_structures_and_ids_are_scoped_to_active_organization(self):
        outside_admin = User.objects.create_user(username='outside', password='Strong-password-123')
        outside_organization = Organization.objects.create(name='Outside', slug='outside', owner=outside_admin)
        outside_church = Church.objects.create(organization=outside_organization, name='Outside church', slug='outside-church')
        outside_ministry = self.create_ministry('Outside ministry', church=outside_church)

        response = self.client.get(f'/api/ministries/{outside_ministry.id}/')
        create = self.client.post('/api/ministries/', {
            'church_id': str(outside_church.church_id),
            'name': 'Spoofed',
        }, format='json')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(create.status_code, 400)
        self.assertFalse(Ministry.objects.filter(name='Spoofed').exists())

    def test_database_constraint_prevents_active_tribe_duplicates(self):
        tribe = self.create_tribe()
        TribeAssignment.objects.create(member=self.member, tribe=tribe)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TribeAssignment.objects.create(member=self.member, tribe=self.create_tribe('Another'))
