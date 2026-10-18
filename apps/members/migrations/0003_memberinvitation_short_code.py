from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ('members', '0002_alter_member_campus'),
    ]

    operations = [
        migrations.AlterField(
            model_name='memberinvitation',
            name='token_hash',
            field=models.CharField(db_index=True, max_length=128),
        ),
        migrations.AddConstraint(
            model_name='memberinvitation',
            constraint=models.UniqueConstraint(
                fields=('token_hash',),
                condition=Q(status='PENDING'),
                name='unique_pending_member_invitation_token_hash',
            ),
        ),
    ]
