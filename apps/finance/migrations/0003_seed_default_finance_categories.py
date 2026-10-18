from django.db import migrations


DEFAULT_CATEGORIES = [
    ('Dîme', 'dime', 'INCOME', False),
    ('Offrande', 'offrande', 'INCOME', False),
    ('Don', 'don', 'INCOME', False),
    ('Collecte spéciale', 'collecte-speciale', 'INCOME', False),
    ('Fonctionnement', 'fonctionnement', 'EXPENSE', True),
    ('Achat matériel', 'achat-materiel', 'EXPENSE', True),
]


def seed_default_categories(apps, schema_editor):
    configuration_model = apps.get_model('finance', 'FinancialConfiguration')
    category_model = apps.get_model('finance', 'FinanceCategory')

    for configuration in configuration_model.objects.select_related('church').iterator():
        for name, code, kind, requires_approval in DEFAULT_CATEGORIES:
            category_model.objects.get_or_create(
                church_id=configuration.church_id,
                code=code,
                defaults={
                    'organization_id': configuration.organization_id,
                    'name': name,
                    'kind': kind,
                    'requires_approval': requires_approval,
                    'is_active': True,
                    'is_system': True,
                    'created_by_id': configuration.created_by_id,
                },
            )


class Migration(migrations.Migration):
    dependencies = [
        ('finance', '0002_financeaccount_financecategory_financefund_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_default_categories, migrations.RunPython.noop),
    ]
