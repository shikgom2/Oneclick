# -*- coding: utf-8 -*-
"""서버에서 만들어졌지만 저장소로 들어오지 못한 merge 마이그레이션의 복원 스텁.

0008_experiments_stimulus_info 이 이 이름을 부모로 참조하는데 파일이 없어서
로컬에서 makemigrations/migrate 가 NodeNotFoundError 로 죽었다. merge
마이그레이션은 operations 가 없으므로 이름만 맞으면 그래프가 복원된다.
서버에는 이미 적용 기록(django_migrations)이 있어 다시 실행되지 않는다 —
서버의 원본 파일을 이 스텁으로 덮어쓰지는 말 것.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('experiments', '0006_experiments_ai_report'),
    ]

    operations = []
