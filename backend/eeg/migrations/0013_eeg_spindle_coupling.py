from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('eeg', '0012_eegpsdspectrogram_eegbaseline_connectivity2_sigma_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='eeg',
            name='spindle_coupling',
            field=models.JSONField(blank=True, db_column='EEG_SPINDLE_COUPLING', null=True),
        ),
    ]
