# -*- coding:utf-8 -*-
from django.apps import AppConfig

from .conf import BOOTH_DB


def enable_sqlite_wal(sender, connection, **kwargs):
    """booth SQLite 연결에만 WAL 저널 모드를 켠다.

    uWSGI 워커 여러 개가 같은 SQLite 파일을 읽고 쓴다. 기본(rollback journal) 모드에서는
    쓰기 중에 읽기까지 막혀 상태 폴링 요청이 줄줄이 대기한다. WAL 은 읽기와 쓰기가 서로를
    막지 않는다. journal_mode=WAL 은 파일에 영구 기록되지만, 새 파일·복원 파일에도 확실히
    적용되도록 연결마다 실행한다(이미 WAL 이면 사실상 비용이 없다).

    journal_size_limit: 체크포인트가 끝난 뒤 -wal 파일을 이 크기(1MB) 이하로 줄인다. 기본(-1)은
    파일을 줄이지 않아, 지운 행의 옛 페이지 사본(이름·응답)이 파일 끝에 오래 남을 수 있다.
    (삭제 직후의 사본은 booth_purge 의 TRUNCATE 체크포인트가 비운다.)

    default(운영 MySQL) 연결에는 절대 손대지 않도록 별칭과 벤더를 모두 확인한다.
    """
    if connection.alias != BOOTH_DB or connection.vendor != 'sqlite':
        return
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA journal_size_limit=1048576')


class BoothConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'booth'
    verbose_name = '부스 체험 AI 리포트'

    def ready(self):
        from django.db.backends.signals import connection_created
        from . import checks  # noqa: F401  시스템 체크 등록(import 부수효과)

        connection_created.connect(enable_sqlite_wal, dispatch_uid='booth_enable_sqlite_wal')
