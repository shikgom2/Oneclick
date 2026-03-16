# Generated manually on 2026-03-14

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('eeg', '0006_eegfaa_eegbaseline_faa_eegdiff1_faa_eegdiff2_faa_and_more'),
    ]

    operations = [
        migrations.RenameField(
            model_name='eegfaa',
            old_name='delta',
            new_name='baseline',
        ),
        migrations.RenameField(
            model_name='eegfaa',
            old_name='theta',
            new_name='stimulation1',
        ),
        migrations.RenameField(
            model_name='eegfaa',
            old_name='alpha',
            new_name='recovery1',
        ),
        migrations.RenameField(
            model_name='eegfaa',
            old_name='beta',
            new_name='stimulation2',
        ),
        migrations.RenameField(
            model_name='eegfaa',
            old_name='gamma',
            new_name='recovery2',
        ),
    ]