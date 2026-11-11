from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('usuarios', '0003_alter_users_cargo'),
    ]

    operations = [
        migrations.CreateModel(
            name='LoginAttempt',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('identifier_key', models.CharField(max_length=64)),
                ('source_key', models.CharField(max_length=64)),
                ('failures', models.PositiveIntegerField(default=0)),
                ('locked_until', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(
                        fields=('identifier_key', 'source_key'),
                        name='usuarios_login_attempt_unique_origin',
                    ),
                ],
            },
        ),
    ]
