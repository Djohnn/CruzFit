from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('usuarios', '0004_loginattempt'),
    ]

    operations = [
        migrations.AlterField(
            model_name='loginattempt',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, db_index=True),
        ),
    ]
