from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('experiments', '0007_merge_20260527_0258'),
    ]

    operations = [
        migrations.AddField(
            model_name='experiments',
            name='stimulus_info',
            field=models.TextField(blank=True, db_column='STIMULUS_INFO', null=True),
        ),
    ]
