from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('experiments', '0005_experiments_report'),
    ]

    operations = [
        migrations.AddField(
            model_name='experiments',
            name='ai_report',
            field=models.TextField(blank=True, db_column='AI_REPORT', null=True),
        ),
    ]
