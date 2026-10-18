from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Campus, Church, Organization, OrganizationMembership
from apps.events.models import (
    ChurchEventAdministrator,
    Event,
    EventAttendance,
    EventOccurrence,
    EventRegistration,
    EventStatus,
    RegistrationStatus,
)
from apps.members.models import Member
from apps.people.models import Person

User = get_user_model()


class EventApiTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='event-admin', password='Strong-password-123')
        self.organization = Organization.objects.create(name='Event Org', slug='event-org', owner=self.admin)
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.church = Church.objects.create(organization=self.organization, name='Church', slug='event-church')
        self.other_church = Church.objects.create(organization=self.organization, name='Other', slug='event-other')
        self.campus = Campus.objects.create(church=self.church, name='Campus', slug='event-campus')
        self.admin_token = self.access_token(self.admin, self.organization)
        self.set_token(self.admin_token)

    @staticmethod
    def access_token(user, organization):
        refresh = RefreshToken.for_user(user)
        refresh['tenant_id'] = str(organization.tenant_id)
        return str(refresh.access_token)

    def set_token(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def event_payload(self, **overrides):
        starts_at = timezone.now() + timedelta(days=2)
        payload = {
            'scope': 'CHURCH',
            'church_id': str(self.church.church_id),
            'title': 'Conférence',
            'description': 'Événement de test',
            'starts_at': starts_at.isoformat(),
            'ends_at': (starts_at + timedelta(hours=2)).isoformat(),
            'registration_mode': 'REQUIRED',
            'capacity': 1,
            'waitlist_enabled': True,
            'checkin_mode': 'REGISTRATION',
            'checkin_opens_at': (timezone.now() - timedelta(hours=1)).isoformat(),
            'checkin_closes_at': (timezone.now() + timedelta(hours=2)).isoformat(),
        }
        payload.update(overrides)
        return payload

    def create_published_event(self, **overrides):
        response = self.client.post('/api/events/', self.event_payload(**overrides), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        event_id = response.data['id']
        published = self.client.post(f'/api/events/{event_id}/status/', {'status': 'PUBLISHED'}, format='json')
        self.assertEqual(published.status_code, 200, published.data)
        event = Event.objects.get(pk=event_id)
        return event, event.occurrences.get()

    def register_visitor(self, event, occurrence, *, first='Visitor'):
        return self.client.post(
            f'/api/events/public/{event.id}/occurrences/{occurrence.id}/registrations/',
            {
                'first_name': first,
                'last_name': 'Guest',
                'email': f'{first.lower()}@example.com',
            },
            format='json',
        )

    def test_event_creation_validates_scope_tenant_and_publishes_occurrence(self):
        outside_org = Organization.objects.create(name='Outside', slug='outside-event-scope')
        outside_church = Church.objects.create(organization=outside_org, name='Outside Church', slug='outside-event-church')
        invalid = self.client.post('/api/events/', self.event_payload(
            church_id=str(outside_church.church_id),
        ), format='json')
        created = self.client.post('/api/events/', self.event_payload(), format='json')

        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(created.status_code, 201, created.data)
        event = Event.objects.get(pk=created.data['id'])
        self.assertEqual(event.organization, self.organization)
        self.assertEqual(event.occurrences.get().status, EventStatus.DRAFT)

        publish = self.client.post(f'/api/events/{event.id}/status/', {'status': 'PUBLISHED'}, format='json')
        self.assertEqual(publish.status_code, 200, publish.data)
        self.assertEqual(event.occurrences.get().status, EventStatus.PUBLISHED)

    def test_external_registration_is_a_distinct_person_and_waitlist_offer_is_reserved(self):
        event, occurrence = self.create_published_event()
        first = self.register_visitor(event, occurrence)
        second = self.register_visitor(event, occurrence, first='Second')

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data['status'], RegistrationStatus.CONFIRMED)
        self.assertTrue(first.data['qr_token'])
        self.assertRegex(first.data['verification_code'], r'^\d{5}$')
        self.assertTrue(first.data['manage_token'])
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(second.data['status'], RegistrationStatus.WAITLISTED)
        self.assertFalse(Person.objects.filter(user__isnull=False).exists())
        self.assertEqual(Person.objects.count(), 2)

        cancelled = self.client.post(
            f"/api/events/registrations/{first.data['registration']['id']}/cancel/",
            {'manage_token': first.data['manage_token']},
            format='json',
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        second_registration = EventRegistration.objects.get(pk=second.data['registration']['id'])
        second_registration.refresh_from_db()
        self.assertEqual(second_registration.status, RegistrationStatus.OFFERED)
        self.assertIsNotNone(second_registration.offer_expires_at)

        accepted = self.client.post(
            f'/api/events/registrations/{second_registration.id}/offer-response/',
            {'accept': True, 'manage_token': second.data['manage_token']},
            format='json',
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertEqual(accepted.data['registration']['status'], RegistrationStatus.CONFIRMED)
        self.assertTrue(accepted.data['qr_token'])
        self.assertRegex(accepted.data['verification_code'], r'^\d{5}$')

    def test_scan_never_checks_in_and_confirm_is_idempotent(self):
        event, occurrence = self.create_published_event()
        registered = self.register_visitor(event, occurrence)
        registration_id = registered.data['registration']['id']

        code_scan = self.client.post('/api/events/registrations/scan/', {
            'token': registered.data['verification_code'],
            'expected_occurrence_id': str(occurrence.id),
        }, format='json')
        self.assertEqual(code_scan.status_code, 200, code_scan.data)
        self.assertEqual(code_scan.data['result'], 'VALID')

        scan = self.client.post('/api/events/registrations/scan/', {
            'token': registered.data['qr_token'],
            'expected_occurrence_id': str(occurrence.id),
        }, format='json')
        self.assertEqual(scan.status_code, 200, scan.data)
        self.assertEqual(scan.data['result'], 'VALID')
        self.assertFalse(EventAttendance.objects.filter(registration_id=registration_id).exists())

        checkin = self.client.post(f'/api/events/registrations/{registration_id}/check-in/', {
            'token': registered.data['verification_code'],
        }, format='json')
        duplicate = self.client.post(f'/api/events/registrations/{registration_id}/check-in/', {
            'token': registered.data['verification_code'],
        }, format='json')
        self.assertEqual(checkin.status_code, 201, checkin.data)
        self.assertEqual(duplicate.status_code, 200, duplicate.data)
        self.assertEqual(duplicate.data['result'], 'ALREADY_CHECKED_IN')
        self.assertEqual(EventAttendance.objects.filter(registration_id=registration_id).count(), 1)

    def test_five_digit_code_scan_is_limited_to_selected_occurrence(self):
        event, occurrence = self.create_published_event()
        registration = self.register_visitor(event, occurrence)
        other_event, other_occurrence = self.create_published_event(
            church_id=str(self.other_church.church_id),
            capacity=None,
            waitlist_enabled=False,
        )
        wrong_occurrence = self.client.post('/api/events/registrations/scan/', {
            'token': registration.data['verification_code'],
            'expected_occurrence_id': str(other_occurrence.id),
        }, format='json')
        missing_occurrence = self.client.post('/api/events/registrations/scan/', {
            'token': registration.data['verification_code'],
        }, format='json')

        self.assertEqual(wrong_occurrence.status_code, 404, wrong_occurrence.data)
        self.assertEqual(missing_occurrence.status_code, 400, missing_occurrence.data)

    def test_tenant_isolation_and_member_scope_for_self_checkin(self):
        event, occurrence = self.create_published_event(
            checkin_mode='SELF',
            registration_mode='DISABLED',
            capacity=None,
            waitlist_enabled=False,
        )
        outside_admin = User.objects.create_user(username='outside-events', password='Strong-password-123')
        outside_organization = Organization.objects.create(name='Outside Events', slug='outside-events', owner=outside_admin)
        OrganizationMembership.objects.create(
            user=outside_admin,
            organization=outside_organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        outside_token = self.access_token(outside_admin, outside_organization)
        self.set_token(outside_token)
        detail = self.client.get(f'/api/events/{event.id}/')
        self.assertEqual(detail.status_code, 404)

        self.set_token(self.admin_token)
        token_response = self.client.post(
            f'/api/events/{event.id}/occurrences/{occurrence.id}/self-checkin-token/',
            {},
            format='json',
        )
        self.assertEqual(token_response.status_code, 200, token_response.data)

        member_user = User.objects.create_user(username='attendee', password='Strong-password-123')
        OrganizationMembership.objects.create(
            user=member_user,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        member = Member.objects.create(
            user=member_user,
            organization=self.organization,
            church=self.church,
            campus=self.campus,
            first_name='Member',
            last_name='Attendee',
            email='member@example.com',
            status=Member.Status.ACTIVE,
        )
        self.set_token(self.access_token(member_user, self.organization))
        self_checkin = self.client.post(
            f'/api/events/{event.id}/occurrences/{occurrence.id}/self-checkin/',
            {'token': token_response.data['token']},
            format='json',
        )
        duplicate = self.client.post(
            f'/api/events/{event.id}/occurrences/{occurrence.id}/self-checkin/',
            {'token': token_response.data['token']},
            format='json',
        )
        self.assertEqual(self_checkin.status_code, 201, self_checkin.data)
        self.assertEqual(duplicate.status_code, 200, duplicate.data)
        self.assertEqual(EventAttendance.objects.filter(occurrence=occurrence, member=member).count(), 1)

    def test_recurrence_generation_is_bounded_and_distinct(self):
        starts_at = timezone.now() + timedelta(days=1)
        payload = self.event_payload(
            starts_at=starts_at.isoformat(),
            ends_at=(starts_at + timedelta(hours=1)).isoformat(),
            recurrence_frequency='WEEKLY',
            recurrence_interval=1,
            recurrence_until=(starts_at + timedelta(days=35)).date().isoformat(),
        )
        created = self.client.post('/api/events/', payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        generated = self.client.post(f"/api/events/{created.data['id']}/occurrences/generate/", {'limit': 3}, format='json')
        self.assertEqual(generated.status_code, 201, generated.data)
        self.assertEqual(len(generated.data), 3)
        self.assertEqual(EventOccurrence.objects.filter(event_id=created.data['id']).count(), 4)
        too_many = self.client.post(f"/api/events/{created.data['id']}/occurrences/generate/", {'limit': 51}, format='json')
        self.assertEqual(too_many.status_code, 400)

    def test_series_update_requires_confirmation_and_moves_only_future_occurrences(self):
        starts_at = timezone.now() + timedelta(days=3)
        created = self.client.post('/api/events/', self.event_payload(
            starts_at=starts_at.isoformat(),
            ends_at=(starts_at + timedelta(hours=2)).isoformat(),
            recurrence_frequency='WEEKLY',
            recurrence_interval=1,
            recurrence_until=(starts_at + timedelta(days=25)).date().isoformat(),
        ), format='json')
        self.assertEqual(created.status_code, 201, created.data)
        generated = self.client.post(f"/api/events/{created.data['id']}/occurrences/generate/", {'limit': 3}, format='json')
        self.assertEqual(generated.status_code, 201)
        event = Event.objects.get(pk=created.data['id'])
        original_starts = list(event.occurrences.order_by('starts_at').values_list('starts_at', flat=True))
        first_new_start = original_starts[0] + timedelta(days=1)

        refused = self.client.post(f'/api/events/{event.id}/series-update/', {
            'confirm_future': False,
            'occurrence': {
                'starts_at': first_new_start.isoformat(),
                'ends_at': (first_new_start + timedelta(hours=2)).isoformat(),
            },
        }, format='json')
        updated = self.client.post(f'/api/events/{event.id}/series-update/', {
            'confirm_future': True,
            'event': {'title': 'Nouvelle conférence'},
            'occurrence': {
                'starts_at': first_new_start.isoformat(),
                'ends_at': (first_new_start + timedelta(hours=2)).isoformat(),
                'location': 'Nouvelle salle',
            },
        }, format='json')
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(updated.status_code, 200, updated.data)
        event.refresh_from_db()
        self.assertEqual(event.title, 'Nouvelle conférence')
        updated_starts = list(event.occurrences.order_by('starts_at').values_list('starts_at', flat=True))
        self.assertEqual(updated_starts, [start + timedelta(days=1) for start in original_starts])

    def test_attendance_correction_is_reasoned_and_qr_cancellation_is_rejected(self):
        event, occurrence = self.create_published_event()
        registered = self.register_visitor(event, occurrence)
        registration_id = registered.data['registration']['id']
        self.client.post(f'/api/events/registrations/{registration_id}/check-in/', {
            'token': registered.data['qr_token'],
        }, format='json')
        attendance = EventAttendance.objects.get(registration_id=registration_id)

        correction = self.client.post(f'/api/events/attendances/{attendance.id}/corrections/', {
            'state': 'REVOKED',
            'reason': 'Pointage attribué par erreur',
        }, format='json')
        self.assertEqual(correction.status_code, 200, correction.data)
        attendance.refresh_from_db()
        self.assertEqual(attendance.state, 'REVOKED')
        self.assertEqual(attendance.corrections.count(), 1)

        cancelled = self.client.post(
            f"/api/events/registrations/{registered.data['registration']['id']}/cancel/",
            {'manage_token': registered.data['manage_token']},
            format='json',
        )
        self.assertEqual(cancelled.status_code, 200)
        scan = self.client.post('/api/events/registrations/scan/', {
            'token': registered.data['qr_token'],
            'expected_occurrence_id': str(occurrence.id),
        }, format='json')
        self.assertEqual(scan.status_code, 200, scan.data)
        self.assertEqual(scan.data['result'], 'CANCELLED')

    def test_cancelled_event_can_be_archived_but_its_series_cannot_be_edited(self):
        event, occurrence = self.create_published_event()
        cancelled = self.client.post(
            f'/api/events/{event.id}/status/',
            {'status': 'CANCELLED', 'confirm': True},
            format='json',
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.data)

        archived = self.client.post(
            f'/api/events/{event.id}/status/',
            {'status': 'ARCHIVED'},
            format='json',
        )
        self.assertEqual(archived.status_code, 200, archived.data)

        update = self.client.post(
            f'/api/events/{event.id}/series-update/',
            {'confirm_future': True, 'event': {'title': 'Modification interdite'}},
            format='json',
        )
        self.assertEqual(update.status_code, 400, update.data)
        event.refresh_from_db()
        self.assertEqual(event.status, EventStatus.ARCHIVED)
        self.assertEqual(event.title, 'Conférence')

    def test_scan_rejects_qr_for_another_occurrence(self):
        event, occurrence = self.create_published_event()
        registered = self.register_visitor(event, occurrence)

        response = self.client.post(
            '/api/events/registrations/scan/',
            {'token': registered.data['qr_token'], 'expected_occurrence_id': str(uuid4())},
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data['result'], 'WRONG_OCCURRENCE')

    def test_expired_waitlist_offer_is_released_to_the_next_person(self):
        event, occurrence = self.create_published_event()
        first = self.register_visitor(event, occurrence, first='First')
        second = self.register_visitor(event, occurrence, first='Second')
        third = self.register_visitor(event, occurrence, first='Third')

        self.client.post(
            f"/api/events/registrations/{first.data['registration']['id']}/cancel/",
            {'manage_token': first.data['manage_token']},
            format='json',
        )
        second_registration = EventRegistration.objects.get(pk=second.data['registration']['id'])
        second_registration.offer_expires_at = timezone.now() - timedelta(minutes=1)
        second_registration.save(update_fields=['offer_expires_at'])

        self.register_visitor(event, occurrence, first='Fourth')

        second_registration.refresh_from_db()
        third_registration = EventRegistration.objects.get(pk=third.data['registration']['id'])
        self.assertEqual(second_registration.status, RegistrationStatus.EXPIRED)
        self.assertEqual(third_registration.status, RegistrationStatus.OFFERED)

    def test_authenticated_member_registration_requires_event_scope(self):
        event, occurrence = self.create_published_event(capacity=None, waitlist_enabled=False)
        member_user = User.objects.create_user(username='event-member', password='Strong-password-123')
        OrganizationMembership.objects.create(
            user=member_user,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        member = Member.objects.create(
            user=member_user,
            organization=self.organization,
            church=self.other_church,
            first_name='Out',
            last_name='OfScope',
            email='out@example.com',
            status=Member.Status.ACTIVE,
        )

        self.set_token(self.access_token(member_user, self.organization))
        response = self.client.post(
            f'/api/events/public/{event.id}/occurrences/{occurrence.id}/registrations/',
            {},
            format='json',
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(EventRegistration.objects.filter(member=member).exists())

    def test_church_event_administrator_manages_only_assigned_church_events(self):
        church_user = User.objects.create_user(username='church-event-manager', password='Strong-password-123')
        OrganizationMembership.objects.create(
            user=church_user,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        member = Member.objects.create(
            user=church_user,
            organization=self.organization,
            church=self.church,
            campus=self.campus,
            first_name='Church',
            last_name='Manager',
            email='church-manager@example.com',
            status=Member.Status.ACTIVE,
        )
        ChurchEventAdministrator.objects.create(church=self.church, member=member, assigned_by=self.admin)
        other_event, other_occurrence = self.create_published_event(church_id=str(self.other_church.church_id))
        other_registration = self.register_visitor(other_event, other_occurrence)

        self.set_token(self.access_token(church_user, self.organization))
        access = self.client.get('/api/events/manager-access/')
        created = self.client.post('/api/events/', self.event_payload(), format='json')
        organization_event = self.client.post(
            '/api/events/',
            self.event_payload(scope='ORGANIZATION', church_id=None),
            format='json',
        )
        own_events = self.client.get('/api/events/')
        other_detail = self.client.get(f'/api/events/{other_event.id}/')
        other_scan = self.client.post(
            '/api/events/registrations/scan/',
            {
                'token': other_registration.data['qr_token'],
                'expected_occurrence_id': str(other_occurrence.id),
            },
            format='json',
        )

        self.assertEqual(access.status_code, 200, access.data)
        self.assertTrue(access.data['can_manage'])
        self.assertFalse(access.data['is_organization_admin'])
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(organization_event.status_code, 403, organization_event.data)
        self.assertEqual(own_events.status_code, 200, own_events.data)
        self.assertEqual({item['id'] for item in own_events.data}, {created.data['id']})
        self.assertEqual(other_detail.status_code, 403)
        self.assertEqual(other_scan.status_code, 404)

    def test_organization_admin_can_assign_only_members_of_target_church(self):
        user = User.objects.create_user(username='appointed-event-manager', password='Strong-password-123')
        OrganizationMembership.objects.create(
            user=user,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        member = Member.objects.create(
            user=user,
            organization=self.organization,
            church=self.church,
            first_name='Appointed',
            last_name='Manager',
            email='appointed@example.com',
            status=Member.Status.ACTIVE,
        )

        assigned = self.client.post(
            f'/api/events/churches/{self.church.church_id}/administrators/',
            {'member_id': str(member.id)},
            format='json',
        )
        listed = self.client.get(f'/api/events/churches/{self.church.church_id}/administrators/')

        self.assertEqual(assigned.status_code, 201, assigned.data)
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(len(listed.data), 1)
        self.assertEqual(listed.data[0]['member_id'], str(member.id))

        other_member_user = User.objects.create_user(username='wrong-church-manager', password='Strong-password-123')
        OrganizationMembership.objects.create(
            user=other_member_user,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        other_member = Member.objects.create(
            user=other_member_user,
            organization=self.organization,
            church=self.other_church,
            first_name='Wrong',
            last_name='Church',
            email='wrong-church@example.com',
            status=Member.Status.ACTIVE,
        )
        rejected = self.client.post(
            f'/api/events/churches/{self.church.church_id}/administrators/',
            {'member_id': str(other_member.id)},
            format='json',
        )
        self.assertEqual(rejected.status_code, 404)
