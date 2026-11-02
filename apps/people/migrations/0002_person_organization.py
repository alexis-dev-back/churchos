from collections import defaultdict

import django.db.models.deletion
from django.db import migrations, models


def assign_unambiguous_organizations(apps, schema_editor):
    Person = apps.get_model('people', 'Person')
    Member = apps.get_model('members', 'Member')
    FamilyMember = apps.get_model('families', 'FamilyMember')
    EventRegistration = apps.get_model('events', 'EventRegistration')
    AttendanceVisitor = apps.get_model('attendance', 'AttendanceVisitor')
    database = schema_editor.connection.alias
    organizations_by_person = defaultdict(set)

    sources = (
        Member.objects.using(database).filter(user__person_profile__organization__isnull=True)
        .values_list('user__person_profile__id', 'organization_id'),
        FamilyMember.objects.using(database).filter(person__organization__isnull=True, person__isnull=False)
        .values_list('person_id', 'family__organization_id'),
        EventRegistration.objects.using(database).filter(person__organization__isnull=True, person__isnull=False)
        .values_list('person_id', 'occurrence__event__organization_id'),
        AttendanceVisitor.objects.using(database).filter(person__organization__isnull=True)
        .values_list('person_id', 'organization_id'),
    )
    for source in sources:
        for person_id, organization_id in source.iterator():
            if person_id and organization_id:
                organizations_by_person[person_id].add(organization_id)

    person_ids_by_organization = defaultdict(list)
    for person_id, organization_ids in organizations_by_person.items():
        if len(organization_ids) == 1:
            person_ids_by_organization[organization_ids.pop()].append(person_id)

    for organization_id, person_ids in person_ids_by_organization.items():
        Person.objects.using(database).filter(
            pk__in=person_ids,
            organization__isnull=True,
        ).update(organization_id=organization_id)


class Migration(migrations.Migration):
    dependencies = [
        ('attendance', '0003_expectedparticipant_expected_for_absence'),
        ('core', '0004_churchsettings_logo_primary_and_more'),
        ('events', '0004_eventregistration_verification_code_hash_and_more'),
        ('families', '0003_family_ministry_family_neighborhood_and_more'),
        ('members', '0003_memberinvitation_short_code'),
        ('people', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='person',
            name='organization',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='people',
                to='core.organization',
            ),
        ),
        migrations.RunPython(assign_unambiguous_organizations, migrations.RunPython.noop),
    ]
