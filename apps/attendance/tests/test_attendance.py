from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Church, Organization, OrganizationMembership
from apps.events.models import ChurchEventAdministrator, Event, EventAttendance, EventOccurrence, EventScope, EventStatus
from apps.members.models import Member

User = get_user_model()


class AttendanceApiTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='presence-admin', password='Strong-password-123')
        self.organization = Organization.objects.create(name='Presence Org', slug='presence-org', owner=self.admin)
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.church = Church.objects.create(organization=self.organization, name='Presence Church', slug='presence-church')
        self.member = self.create_member('present', self.organization, self.church)
        self.other_member = self.create_member('absent', self.organization, self.church)
        self.authenticate(self.admin, self.organization)

    @staticmethod
    def create_member(username, organization, church, status=Member.Status.ACTIVE):
        user = User.objects.create_user(username=username, password='Strong-password-123')
        OrganizationMembership.objects.create(
            user=user,
            organization=organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        return Member.objects.create(
            user=user,
            organization=organization,
            church=church,
            first_name=username.title(),
            last_name='Attendance',
            email=f'{username}@example.com',
            status=status,
        )

    def authenticate(self, user, organization):
        token = RefreshToken.for_user(user)
        token['tenant_id'] = str(organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token.access_token}')

    def create_session(self, **payload):
        starts_at = timezone.now() - timedelta(minutes=5)
        data = {
            'title': 'Culte du dimanche',
            'church_id': str(self.church.church_id),
            'starts_at': starts_at.isoformat(),
            'ends_at': (starts_at + timedelta(hours=2)).isoformat(),
            'allowed_methods': ['MANUAL', 'LIST', 'SELF', 'QR'],
            'tolerance_minutes': 15,
        }
        data.update(payload)
        response = self.client.post('/api/attendance/sessions/', data, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['session']

    def test_session_freezes_expected_members_and_closes_absences_idempotently(self):
        session = self.create_session()
        expected = self.client.get(f"/api/attendance/sessions/{session['id']}/expected/")
        self.assertEqual(expected.status_code, 200)
        self.assertEqual(len(expected.data), 2)

        self.create_member('new-member', self.organization, self.church)
        expected_after_join = self.client.get(f"/api/attendance/sessions/{session['id']}/expected/")
        self.assertEqual(len(expected_after_join.data), 2)

        opened = self.client.post(f"/api/attendance/sessions/{session['id']}/actions/open/", {}, format='json')
        self.assertEqual(opened.status_code, 200, opened.data)
        participant = next(item for item in expected_after_join.data if item['member_id'] == str(self.member.id))
        first_checkin = self.client.post(
            f"/api/attendance/sessions/{session['id']}/check-ins/",
            {'expected_participant_id': participant['id'], 'method': 'MANUAL'},
            format='json',
        )
        self.assertEqual(first_checkin.status_code, 201, first_checkin.data)
        first_timestamp = first_checkin.data['attendance']['checked_in_at']

        duplicate = self.client.post(
            f"/api/attendance/sessions/{session['id']}/check-ins/",
            {'expected_participant_id': participant['id'], 'method': 'LIST'},
            format='json',
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.data['attendance']['checked_in_at'], first_timestamp)
        self.assertEqual(duplicate.data['attendance']['method'], 'MANUAL')

        closed = self.client.post(f"/api/attendance/sessions/{session['id']}/actions/close/", {}, format='json')
        self.assertEqual(closed.status_code, 200, closed.data)
        closed_again = self.client.post(f"/api/attendance/sessions/{session['id']}/actions/close/", {}, format='json')
        self.assertEqual(closed_again.status_code, 200, closed_again.data)
        final_list = self.client.get(f"/api/attendance/sessions/{session['id']}/expected/")
        records = {item['member_id']: item['attendance']['status'] for item in final_list.data}
        self.assertEqual(records[str(self.member.id)], 'PRESENT')
        self.assertEqual(records[str(self.other_member.id)], 'ABSENT')

    def test_late_boundary_is_inclusive_and_later_checkin_is_late(self):
        session = self.create_session()
        self.client.post(f"/api/attendance/sessions/{session['id']}/actions/open/", {}, format='json')
        expected = self.client.get(f"/api/attendance/sessions/{session['id']}/expected/").data
        participant = next(item for item in expected if item['member_id'] == str(self.member.id))

        from apps.attendance.models import AttendanceSession
        from apps.attendance.services import record_attendance
        from apps.attendance.models import ExpectedParticipant, SessionMethod

        model_session = AttendanceSession.objects.get(pk=session['id'])
        expected_member = ExpectedParticipant.objects.get(pk=participant['id'])
        exactly_at_deadline = model_session.starts_at + timedelta(minutes=model_session.tolerance_minutes)
        record, created = record_attendance(model_session, expected_member, self.admin, SessionMethod.MANUAL, now=exactly_at_deadline)
        self.assertTrue(created)
        self.assertEqual(record.status, 'PRESENT')

    def test_member_cannot_create_session_or_point_for_another_member(self):
        session = self.create_session()
        self.authenticate(self.member.user, self.organization)
        forbidden_create = self.client.post('/api/attendance/sessions/', {}, format='json')
        self.assertEqual(forbidden_create.status_code, 403)

        self.authenticate(self.admin, self.organization)
        self.client.post(f"/api/attendance/sessions/{session['id']}/actions/open/", {}, format='json')
        self.authenticate(self.member.user, self.organization)
        self_checkin = self.client.post(
            f"/api/attendance/sessions/{session['id']}/self-check-in/",
            {},
            format='json',
        )
        self.assertEqual(self_checkin.status_code, 201, self_checkin.data)
        from apps.attendance.models import AttendanceRecord

        self.assertEqual(
            AttendanceRecord.objects.get(pk=self_checkin.data['attendance']['id']).expected_participant.member_id,
            self.member.id,
        )

    def test_cannot_create_session_for_another_organization(self):
        other_user = User.objects.create_user(username='other-owner', password='Strong-password-123')
        other_org = Organization.objects.create(name='Other Presence Org', slug='other-presence-org', owner=other_user)
        other_church = Church.objects.create(organization=other_org, name='Other Church', slug='other-presence-church')
        other_member = self.create_member('foreign', other_org, other_church)
        starts_at = timezone.now() + timedelta(days=1)
        response = self.client.post('/api/attendance/sessions/', {
            'title': 'Hors périmètre',
            'church_id': str(self.church.church_id),
            'population_rule': 'MANUAL',
            'member_ids': [str(other_member.id)],
            'starts_at': starts_at.isoformat(),
            'ends_at': (starts_at + timedelta(hours=2)).isoformat(),
            'allowed_methods': ['MANUAL'],
        }, format='json')
        self.assertEqual(response.status_code, 400, response.data)

    def test_event_occurrence_shares_one_canonical_attendance_record(self):
        from apps.attendance.models import AttendanceRecord
        from apps.attendance.services import get_or_create_event_attendance_session, sync_event_attendance

        starts_at = timezone.now() + timedelta(hours=1)
        event = Event.objects.create(
            organization=self.organization,
            scope=EventScope.CHURCH,
            church=self.church,
            title='Événement intégré',
            status=EventStatus.PUBLISHED,
            created_by=self.admin,
        )
        occurrence = EventOccurrence.objects.create(
            event=event,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
            status=EventStatus.PUBLISHED,
        )
        session = get_or_create_event_attendance_session(occurrence)
        self.assertEqual(session.expected_participants.count(), 2)
        source = EventAttendance.objects.create(
            occurrence=occurrence,
            member=self.member,
            checked_in_by=self.admin,
            source='MANUAL',
        )
        sync_event_attendance(source, actor=self.admin)
        sync_event_attendance(source, actor=self.admin)
        self.assertEqual(AttendanceRecord.objects.filter(event_attendance=source).count(), 1)
        self.assertEqual(AttendanceRecord.objects.filter(expected_participant__session=session).count(), 1)

    def test_church_event_administrator_is_limited_to_managed_church(self):
        managed_church = Church.objects.create(
            organization=self.organization,
            name='Managed Church',
            slug='managed-attendance-church',
        )
        manager_member = self.create_member('church-manager', self.organization, managed_church)
        ChurchEventAdministrator.objects.create(
            church=managed_church,
            member=manager_member,
            assigned_by=self.admin,
        )
        managed_session = self.create_session(church_id=str(managed_church.church_id))
        other_session = self.create_session()
        self.authenticate(manager_member.user, self.organization)

        context = self.client.get('/api/attendance/context/')
        self.assertEqual(context.status_code, 200, context.data)
        self.assertFalse(context.data['is_organization_admin'])
        self.assertEqual(context.data['managed_church_ids'], [str(managed_church.church_id)])

        sessions = self.client.get('/api/attendance/sessions/')
        self.assertEqual(sessions.status_code, 200, sessions.data)
        self.assertEqual([item['id'] for item in sessions.data['results']], [managed_session['id']])
        self.assertEqual(
            self.client.get(f"/api/attendance/sessions/{other_session['id']}/").status_code,
            403,
        )

        members = self.client.get('/api/attendance/members/')
        self.assertEqual(members.status_code, 200, members.data)
        self.assertEqual([item['id'] for item in members.data], [str(manager_member.id)])
        self.assertEqual(self.client.get('/api/attendance/reports/weekly/').status_code, 403)
        report = self.client.get(
            f"/api/attendance/reports/weekly/?church_id={managed_church.church_id}",
        )
        self.assertEqual(report.status_code, 200, report.data)
