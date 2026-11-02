from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import (
    AppPermission,
    Church,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    OrganizationRolePermission,
    UserRoleAssignment,
)
from apps.pastoral.models import (
    PastoralAuditEvent,
    PastoralCase,
    PastoralCaseAccess,
    PastoralInteractionHistory,
    PastoralNote,
    PastoralNoteShare,
    PastoralTask,
    PastoralTaskHistory,
    PastoralNotification,
    PrayerRequestHistory,
)
from apps.people.models import Person

User = get_user_model()

PASTORAL_PERMISSIONS = [
    'pastoral.case.read',
    'pastoral.case.create',
    'pastoral.case.update',
    'pastoral.case.assign',
    'pastoral.case.share',
    'pastoral.case.transition',
    'pastoral.case.close',
    'pastoral.case.reopen',
    'pastoral.case.archive',
    'pastoral.case.restricted.read',
    'pastoral.case.very_restricted.read',
    'pastoral.note.create',
    'pastoral.note.update',
    'pastoral.note.share',
    'pastoral.note.restricted.read',
    'pastoral.prayer.read',
    'pastoral.prayer.create',
    'pastoral.prayer.update',
    'pastoral.prayer.share',
    'pastoral.prayer.restricted.read',
    'pastoral.prayer.very_restricted.read',
    'pastoral.interaction.create',
    'pastoral.interaction.update',
    'pastoral.interaction.share',
    'pastoral.interaction.restricted.read',
    'pastoral.task.create',
    'pastoral.task.manage',
    'pastoral.workflow.read',
    'pastoral.workflow.manage',
    'pastoral.report.read',
    'pastoral.audit.read',
    'pastoral.export',
]


class PastoralApiTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='org-admin',
            email='org-admin@example.test',
            password='Strong-password-123',
        )
        self.organization = Organization.objects.create(
            name='Organisation pastorale',
            slug='organisation-pastorale',
            owner=self.admin,
        )
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.church = Church.objects.create(
            organization=self.organization,
            name='Église principale',
            slug='eglise-principale',
        )
        self.case_worker = self._create_user('travailleur')
        self.case_worker_membership = OrganizationMembership.objects.create(
            user=self.case_worker,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        self._grant_permissions(self.case_worker, PASTORAL_PERMISSIONS)
        self.person = Person.objects.create(
            organization=self.organization,
            first_name='Ange',
            last_name='Kouame',
        )
        self._authenticate(self.case_worker)

    def _create_user(self, username):
        return User.objects.create_user(
            username=username,
            email=f'{username}@example.test',
            password='Strong-password-123',
        )

    def _grant_permissions(self, user, codes):
        membership = OrganizationMembership.objects.get(user=user, organization=self.organization)
        role = OrganizationRole.objects.create(
            organization=self.organization,
            name=f'Rôle {user.username}',
            code=f'role-{user.username}',
            created_by=self.admin,
        )
        for code in codes:
            permission = AppPermission.objects.get(code=code)
            OrganizationRolePermission.objects.create(role=role, permission=permission)
        UserRoleAssignment.objects.create(
            organization=self.organization,
            membership=membership,
            role=role,
            scope=UserRoleAssignment.Scope.ORGANIZATION,
            created_by=self.admin,
        )
        return role

    def _authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        refresh['tenant_id'] = str(self.organization.tenant_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

    def _create_case(self, **overrides):
        payload = {
            'church_id': str(self.church.church_id),
            'person_id': str(self.person.pk),
            'title': 'Accompagnement après une visite',
            'category': 'Visite',
            'source': 'VISIT',
        }
        payload.update(overrides)
        return self.client.post('/api/pastoral/cases/', payload, format='json')

    def test_case_creation_and_detail_reuse_authenticated_tenant(self):
        created = self._create_case()

        self.assertEqual(created.status_code, 201, created.data)
        case_id = created.data['id']
        case = PastoralCase.objects.get(pk=case_id)
        self.assertEqual(case.organization, self.organization)
        self.assertEqual(case.church, self.church)
        self.assertEqual(case.person, self.person)
        self.assertTrue(PastoralCaseAccess.objects.filter(case=case, user=self.case_worker).exists())
        detail = self.client.get(f'/api/pastoral/cases/{case_id}/')
        self.assertEqual(detail.status_code, 200, detail.data)

    def test_potential_duplicate_check_only_returns_cases_already_visible_to_user(self):
        created = self._create_case()
        query = (
            f'/api/pastoral/cases/potential-duplicates/?church_id={self.church.church_id}'
            f'&person_id={self.person.pk}'
        )
        visible = self.client.get(query)
        self.assertEqual(visible.status_code, 200, visible.data)
        self.assertEqual([str(item['id']) for item in visible.data], [created.data['id']])

        reader = self._create_user('createur-sans-acces')
        OrganizationMembership.objects.create(
            user=reader, organization=self.organization, role=OrganizationMembership.Role.MEMBER,
        )
        self._grant_permissions(reader, ['pastoral.case.create'])
        self._authenticate(reader)
        hidden = self.client.get(query)
        self.assertEqual(hidden.status_code, 200, hidden.data)
        self.assertEqual(hidden.data, [])

    def test_unassigned_cases_are_visible_to_scoped_assigners_and_can_be_claimed(self):
        created = self._create_case()
        assigner = self._create_user('attribueur')
        OrganizationMembership.objects.create(
            user=assigner, organization=self.organization, role=OrganizationMembership.Role.MEMBER,
        )
        self._grant_permissions(assigner, ['pastoral.case.read', 'pastoral.case.assign'])
        self._authenticate(assigner)

        listing = self.client.get('/api/pastoral/cases/')
        self.assertEqual(listing.status_code, 200, listing.data)
        self.assertEqual([item['id'] for item in listing.data['results']], [created.data['id']])
        suggestions = self.client.get(
            f"/api/pastoral/cases/{created.data['id']}/assignee-suggestions/",
        )
        self.assertEqual(suggestions.status_code, 200, suggestions.data)
        self.assertIn(assigner.username, [item['username'] for item in suggestions.data])

        assigned = self.client.post(
            f"/api/pastoral/cases/{created.data['id']}/assign/",
            {'assigned_to': assigner.pk, 'reason': 'Prise en charge'},
            format='json',
        )
        self.assertEqual(assigned.status_code, 200, assigned.data)
        self.assertEqual(assigned.data['assigned_to'], assigner.pk)

    def test_dashboard_counts_unassigned_alerts_using_configured_threshold(self):
        created = self._create_case()
        workflow = self.client.patch(
            '/api/pastoral/workflow/',
            {'unassigned_alert_after_days': 1},
            format='json',
        )
        self.assertEqual(workflow.status_code, 200, workflow.data)
        PastoralCase.objects.filter(pk=created.data['id']).update(
            created_at=timezone.now() - timedelta(days=2),
        )

        dashboard = self.client.get('/api/pastoral/dashboard/')

        self.assertEqual(dashboard.status_code, 200, dashboard.data)
        self.assertEqual(dashboard.data['unassigned_cases'], 1)
        self.assertEqual(dashboard.data['unassigned_alerts'], 1)

    def test_organization_admin_does_not_automatically_read_sensitive_cases(self):
        created = self._create_case()
        self._authenticate(self.admin)

        listing = self.client.get('/api/pastoral/cases/')
        detail = self.client.get(f"/api/pastoral/cases/{created.data['id']}/")

        self.assertEqual(listing.status_code, 403)
        self.assertEqual(detail.status_code, 404)
        self.assertTrue(PastoralAuditEvent.objects.filter(
            organization=self.organization,
            action='CASE_ACCESS_DENIED',
            result='DENIED',
        ).exists())

    def test_role_permission_alone_does_not_grant_case_access_until_shared(self):
        created = self._create_case()
        reader = self._create_user('lecteur')
        OrganizationMembership.objects.create(
            user=reader,
            organization=self.organization,
            role=OrganizationMembership.Role.MEMBER,
        )
        self._grant_permissions(reader, ['pastoral.case.read'])
        self._authenticate(reader)
        case_id = created.data['id']

        self.assertEqual(self.client.get(f'/api/pastoral/cases/{case_id}/').status_code, 404)
        self._authenticate(self.case_worker)
        shared = self.client.post(
            f'/api/pastoral/cases/{case_id}/share/',
            {'user_id': reader.pk, 'access_level': 'READ'},
            format='json',
        )
        self.assertIn(shared.status_code, (200, 201), shared.data)

        self._authenticate(reader)
        detail = self.client.get(f'/api/pastoral/cases/{case_id}/')
        self.assertEqual(detail.status_code, 200, detail.data)

    def test_revoking_explicit_case_share_removes_access(self):
        created = self._create_case()
        reader = self._create_user('lecteur-revoque')
        OrganizationMembership.objects.create(
            user=reader, organization=self.organization, role=OrganizationMembership.Role.MEMBER,
        )
        self._grant_permissions(reader, ['pastoral.case.read'])
        case_id = created.data['id']
        self.client.post(
            f'/api/pastoral/cases/{case_id}/share/',
            {'user_id': reader.pk, 'access_level': 'READ'},
            format='json',
        )

        self._authenticate(reader)
        self.assertEqual(self.client.get(f'/api/pastoral/cases/{case_id}/').status_code, 200)
        self._authenticate(self.case_worker)
        revoked = self.client.delete(
            f'/api/pastoral/cases/{case_id}/share/',
            {'user_id': reader.pk},
            format='json',
        )
        self.assertEqual(revoked.status_code, 200, revoked.data)
        self.assertTrue(revoked.data['revoked'])
        self._authenticate(reader)
        self.assertEqual(self.client.get(f'/api/pastoral/cases/{case_id}/').status_code, 404)

    def test_reassigned_task_does_not_leave_case_access_with_previous_assignee(self):
        created = self._create_case()
        case_id = created.data['id']
        former_assignee = self._create_user('ancien-responsable-tache')
        OrganizationMembership.objects.create(
            user=former_assignee, organization=self.organization, role=OrganizationMembership.Role.MEMBER,
        )
        self._grant_permissions(former_assignee, ['pastoral.case.read'])
        task = self.client.post(
            f'/api/pastoral/cases/{case_id}/tasks/',
            {'title': 'Suivi', 'assigned_to': former_assignee.pk},
            format='json',
        )
        self.assertEqual(task.status_code, 201, task.data)
        task_id = task.data['id']

        self._authenticate(former_assignee)
        self.assertEqual(self.client.get(f'/api/pastoral/cases/{case_id}/').status_code, 200)
        reassigned = self.client.patch(
            f'/api/pastoral/tasks/{task_id}/',
            {'assigned_to': self.case_worker.pk, 'reason': 'Nouvelle attribution'},
            format='json',
        )
        self.assertEqual(reassigned.status_code, 200, reassigned.data)
        self.assertEqual(self.client.get(f'/api/pastoral/cases/{case_id}/').status_code, 404)

    def test_foreign_organization_subject_is_rejected(self):
        other_organization = Organization.objects.create(name='Autre', slug='autre')
        foreign_person = Person.objects.create(
            organization=other_organization,
            first_name='Personne',
            last_name='Externe',
        )

        response = self._create_case(person_id=str(foreign_person.pk))

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(PastoralCase.objects.filter(person=foreign_person).exists())

    def test_case_subject_must_be_exactly_one_member_or_person(self):
        no_subject = self.client.post(
            '/api/pastoral/cases/',
            {'church_id': str(self.church.church_id), 'title': 'Sans personne'},
            format='json',
        )
        both_subjects = self._create_case(member_id='00000000-0000-0000-0000-000000000001')

        self.assertEqual(no_subject.status_code, 400)
        self.assertEqual(both_subjects.status_code, 400)

    def test_private_note_is_hidden_from_shared_case_reader_until_note_is_shared(self):
        case_response = self._create_case()
        case_id = case_response.data['id']
        reader = self._create_user('lecteur-note')
        OrganizationMembership.objects.create(
            user=reader, organization=self.organization, role=OrganizationMembership.Role.MEMBER,
        )
        self._grant_permissions(reader, ['pastoral.case.read'])
        self.client.post(
            f'/api/pastoral/cases/{case_id}/share/',
            {'user_id': reader.pk, 'access_level': 'READ'},
            format='json',
        )
        note_response = self.client.post(
            f'/api/pastoral/cases/{case_id}/notes/',
            {'content': 'Information pastorale privée', 'sensitivity': 'AUTHOR_ONLY'},
            format='json',
        )
        self.assertEqual(note_response.status_code, 201, note_response.data)
        self._authenticate(reader)

        hidden = self.client.get(f'/api/pastoral/cases/{case_id}/notes/')
        self.assertEqual(hidden.status_code, 200)
        self.assertEqual(hidden.data, [])

        self._authenticate(self.case_worker)
        shared = self.client.post(
            f'/api/pastoral/cases/{case_id}/notes/{note_response.data["id"]}/share/',
            {'user_id': reader.pk, 'can_edit': False},
            format='json',
        )
        self.assertIn(shared.status_code, (200, 201), shared.data)
        self._authenticate(reader)
        visible = self.client.get(f'/api/pastoral/cases/{case_id}/notes/')
        self.assertEqual(visible.status_code, 200)
        self.assertEqual(visible.data[0]['content'], 'Information pastorale privée')

    def test_anonymous_prayer_request_has_no_identity_and_cannot_be_reidentified(self):
        response = self.client.post(
            '/api/pastoral/prayer-requests/',
            {
                'church_id': str(self.church.church_id),
                'is_anonymous': True,
                'content': 'Prière pour une situation personnelle.',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data['is_anonymous'])
        self.assertIsNone(response.data['subject_name'])
        self.assertNotIn('member', response.data)
        self.assertNotIn('person', response.data)

        invalid = self.client.post(
            '/api/pastoral/prayer-requests/',
            {
                'church_id': str(self.church.church_id),
                'is_anonymous': True,
                'person_id': str(self.person.pk),
                'content': 'Une identité ne doit pas être associée.',
            },
            format='json',
        )
        self.assertEqual(invalid.status_code, 400)

    def test_interaction_prayer_and_task_changes_have_reasoned_history(self):
        case_response = self._create_case()
        case_id = case_response.data['id']

        interaction_response = self.client.post(
            f'/api/pastoral/cases/{case_id}/interactions/',
            {
                'kind': 'CALL',
                'scheduled_at': (timezone.now() + timedelta(days=1)).isoformat(),
                'objective': 'Point de suivi',
            },
            format='json',
        )
        self.assertEqual(interaction_response.status_code, 201, interaction_response.data)
        interaction_id = interaction_response.data['id']
        no_interaction_reason = self.client.patch(
            f'/api/pastoral/cases/{case_id}/interactions/{interaction_id}/',
            {'status': 'COMPLETED'},
            format='json',
        )
        self.assertEqual(no_interaction_reason.status_code, 400)
        interaction_update = self.client.patch(
            f'/api/pastoral/cases/{case_id}/interactions/{interaction_id}/',
            {'status': 'COMPLETED', 'reason': 'Échange réalisé'},
            format='json',
        )
        self.assertEqual(interaction_update.status_code, 200, interaction_update.data)
        self.assertEqual(PastoralInteractionHistory.objects.filter(interaction_id=interaction_id).count(), 2)

        prayer_response = self.client.post(
            '/api/pastoral/prayer-requests/',
            {
                'church_id': str(self.church.church_id),
                'is_anonymous': True,
                'content': 'Demande confidentielle',
            },
            format='json',
        )
        self.assertEqual(prayer_response.status_code, 201, prayer_response.data)
        prayer_id = prayer_response.data['id']
        prayer_update = self.client.patch(
            f'/api/pastoral/prayer-requests/{prayer_id}/',
            {'status': 'IN_PROGRESS', 'reason': 'Prise en charge'},
            format='json',
        )
        self.assertEqual(prayer_update.status_code, 200, prayer_update.data)
        self.assertEqual(PrayerRequestHistory.objects.filter(request_id=prayer_id).count(), 2)
        prayer_history = self.client.get(f'/api/pastoral/prayer-requests/{prayer_id}/history/')
        self.assertEqual(prayer_history.status_code, 200, prayer_history.data)
        self.assertEqual(len(prayer_history.data), 2)

        task_response = self.client.post(
            f'/api/pastoral/cases/{case_id}/tasks/',
            {'title': 'Rappeler', 'assigned_to': self.case_worker.pk},
            format='json',
        )
        self.assertEqual(task_response.status_code, 201, task_response.data)
        task_id = task_response.data['id']
        task_update = self.client.patch(
            f'/api/pastoral/tasks/{task_id}/',
            {'status': 'DONE', 'reason': 'Appel effectué'},
            format='json',
        )
        self.assertEqual(task_update.status_code, 200, task_update.data)
        self.assertEqual(PastoralTaskHistory.objects.filter(task_id=task_id).count(), 2)
        task_history = self.client.get(f'/api/pastoral/tasks/{task_id}/history/')
        self.assertEqual(task_history.status_code, 200, task_history.data)
        self.assertEqual(len(task_history.data), 2)

    def test_reports_apply_date_filters_and_export_only_aggregates(self):
        self._create_case()

        invalid_range = self.client.get('/api/pastoral/reports/?from=not-a-date')
        self.assertEqual(invalid_range.status_code, 400)
        future_range = self.client.get('/api/pastoral/reports/?from=2099-01-01')
        self.assertEqual(future_range.status_code, 200, future_range.data)
        self.assertEqual(future_range.data['cases_by_status'], [])

        exported = self.client.get('/api/pastoral/reports/?export=csv')
        self.assertEqual(exported.status_code, 200, exported.content)
        self.assertTrue(exported['Content-Type'].startswith('text/csv'))
        self.assertNotIn(b'Accompagnement apr', exported.content)
        self.assertIn(b'Dossiers par statut', exported.content)

    def test_interaction_types_are_configurable_and_private_outcomes_require_access(self):
        case_response = self._create_case()
        case_id = case_response.data['id']
        configured = self.client.patch(
            '/api/pastoral/workflow/',
            {'interaction_kinds': [{'code': 'COUNSELING', 'label': 'Entretien personnalisé'}]},
            format='json',
        )
        self.assertEqual(configured.status_code, 200, configured.data)
        interaction = self.client.post(
            f'/api/pastoral/cases/{case_id}/interactions/',
            {
                'kind': 'COUNSELING',
                'scheduled_at': (timezone.now() + timedelta(days=1)).isoformat(),
                'sensitivity': 'RESTRICTED',
                'outcome': 'Compte rendu confidentiel',
            },
            format='json',
        )
        self.assertEqual(interaction.status_code, 201, interaction.data)
        interaction_id = interaction.data['id']
        self.assertEqual(interaction.data['outcome'], 'Compte rendu confidentiel')

        invalid_kind = self.client.post(
            f'/api/pastoral/cases/{case_id}/interactions/',
            {
                'kind': 'VISIT',
                'scheduled_at': (timezone.now() + timedelta(days=1)).isoformat(),
            },
            format='json',
        )
        self.assertEqual(invalid_kind.status_code, 400)

        reader = self._create_user('lecteur-compte-rendu')
        OrganizationMembership.objects.create(
            user=reader, organization=self.organization, role=OrganizationMembership.Role.MEMBER,
        )
        self._grant_permissions(reader, ['pastoral.case.read', 'pastoral.interaction.update'])
        self.client.post(
            f'/api/pastoral/cases/{case_id}/share/',
            {'user_id': reader.pk, 'access_level': 'EDIT'},
            format='json',
        )
        self._authenticate(reader)
        denied = self.client.patch(
            f'/api/pastoral/cases/{case_id}/interactions/{interaction_id}/',
            {'outcome': 'Modification non autorisée', 'reason': 'Tentative'},
            format='json',
        )
        self.assertEqual(denied.status_code, 404)

        self._authenticate(self.case_worker)
        shared = self.client.post(
            f'/api/pastoral/cases/{case_id}/interactions/{interaction_id}/share/',
            {'user_id': reader.pk},
            format='json',
        )
        self.assertIn(shared.status_code, (200, 201), shared.data)
        self._authenticate(reader)
        allowed = self.client.patch(
            f'/api/pastoral/cases/{case_id}/interactions/{interaction_id}/',
            {'outcome': 'Compte rendu partagé', 'reason': 'Correction validée'},
            format='json',
        )
        self.assertEqual(allowed.status_code, 200, allowed.data)

    def test_due_task_reminders_are_idempotent_and_do_not_include_sensitive_text(self):
        case_response = self._create_case(title='Confidentiel : visite familiale')
        task = PastoralTask.objects.create(
            case_id=case_response.data['id'],
            title='Action confidentielle',
            description='Ne pas inclure dans les rappels.',
            assigned_to=self.case_worker,
            created_by=self.case_worker,
            due_at=timezone.now() - timedelta(hours=1),
        )

        call_command('dispatch_pastoral_reminders', stdout=StringIO())
        call_command('dispatch_pastoral_reminders', stdout=StringIO())

        self.assertEqual(PastoralNotification.objects.filter(task=task).count(), 1)
        response = self.client.get('/api/pastoral/notifications/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 1)
        self.assertNotIn('Action confidentielle', response.data[0]['message'])
        self.assertNotIn('visite familiale', response.data[0]['message'])

    def test_closure_requires_reason_and_all_open_items_to_be_resolved(self):
        case_response = self._create_case(status='IN_PROGRESS')
        case_id = case_response.data['id']
        task = PastoralTask.objects.create(
            case_id=case_id,
            title='Rappeler la famille',
            assigned_to=self.case_worker,
            created_by=self.case_worker,
            due_at=timezone.now() + timedelta(days=1),
        )
        blocked = self.client.post(
            f'/api/pastoral/cases/{case_id}/close/',
            {'reason': 'Accompagnement terminé'},
            format='json',
        )
        self.assertEqual(blocked.status_code, 400, blocked.data)

        task.status = PastoralTask.Status.DONE
        task.save(update_fields=['status'])
        no_reason = self.client.post(f'/api/pastoral/cases/{case_id}/close/', {}, format='json')
        self.assertEqual(no_reason.status_code, 400, no_reason.data)
        closed = self.client.post(
            f'/api/pastoral/cases/{case_id}/close/',
            {'reason': 'Accompagnement terminé'},
            format='json',
        )
        self.assertEqual(closed.status_code, 200, closed.data)
        self.assertEqual(closed.data['status'], 'CLOSED')

    def test_foreign_tenant_case_id_is_hidden(self):
        other_organization = Organization.objects.create(name='Autre org', slug='autre-org')
        other_church = Church.objects.create(
            organization=other_organization, name='Autre église', slug='autre-eglise',
        )
        foreign_person = Person.objects.create(
            organization=other_organization, first_name='Jean', last_name='Externe',
        )
        foreign_case = PastoralCase.objects.create(
            organization=other_organization,
            church=other_church,
            person=foreign_person,
            title='Dossier externe',
            created_by=self.case_worker,
        )

        response = self.client.get(f'/api/pastoral/cases/{foreign_case.pk}/')

        self.assertEqual(response.status_code, 404)

    def test_audit_events_cannot_be_changed_or_deleted(self):
        response = self._create_case()
        event = PastoralAuditEvent.objects.get(object_id=response.data['id'])
        with self.assertRaises(ValidationError):
            PastoralAuditEvent.objects.filter(pk=event.pk).update(action='TAMPERED')
        with self.assertRaises(ValidationError):
            event.delete()
