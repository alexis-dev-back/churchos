from django.db import migrations


PERMISSIONS = [
    ('communication.read', 'communication', 'read', 'Consulter les communications'),
    ('communication.manage', 'communication', 'manage', 'Gérer les communications'),
]


def seed_permissions(apps, schema_editor):
    permission_model = apps.get_model('core', 'AppPermission')
    for code, module, action, label in PERMISSIONS:
        permission_model.objects.get_or_create(
            code=code,
            defaults={'module': module, 'action': action, 'label': label},
        )


def remove_permissions(apps, schema_editor):
    permission_model = apps.get_model('core', 'AppPermission')
    permission_model.objects.filter(code__in=[permission[0] for permission in PERMISSIONS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_churchsettings_logo_primary_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_permissions, remove_permissions),
    ]
