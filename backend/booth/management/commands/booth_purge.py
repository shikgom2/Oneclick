# -*- coding:utf-8 -*-
"""보유기간(BOOTH_RETENTION_DAYS, 기본 14일)이 지난 부스 체험자 데이터를 행째로 삭제한다.

동의서에 '참가번호 발급일로부터 14일 경과 후 지체 없이 파기'라고 약속했으므로 크론으로 1시간마다
실행한다(화면에 보이는 삭제일과 실제 삭제 사이를 한 시간 안으로 줄인다).
    python manage.py booth_purge             # 삭제
    python manage.py booth_purge --dry-run   # 삭제 대상 건수만 출력

출력·로그에는 건수만 남긴다. 크론 로그·메일은 여러 사람이 볼 수 있어서 이름은 물론
참가자 번호·토큰도 쓰지 않는다.

삭제 기준은 services.get_participant_by_token 과 같은 'created_at < 지금 - 보유일수' 다.
조회에서 이미 숨겨진 행과 삭제되는 행이 정확히 같아야, 크론이 늦게 돌아도 기간이 지난
페이지가 열리지 않고, 반대로 아직 보여야 할 페이지가 먼저 지워지지도 않는다.
크론이 실패하면 다음 실행에서 밀린 건이 함께 지워진다(그동안에도 조회는 막혀 있다).
"""
import logging
import os
import time
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connections
from django.utils import timezone

from booth import conf
from booth.checks import booth_database_check
from booth.models import Participant
from booth.services import kst

logger = logging.getLogger(__name__)

# PRAGMA secure_delete 조회값 -> 복원할 때 쓸 설정값
_SECURE_DELETE_VALUES = {0: 'OFF', 1: 'ON', 2: 'FAST'}

CHECKPOINT_ATTEMPTS = 3
_CHECKPOINT_BACKOFF_SEC = (1.0, 3.0)


class Command(BaseCommand):
    help = '보유기간이 지난 부스 체험자 데이터를 삭제합니다. --dry-run 은 건수만 출력합니다.'

    # 다른 앱의 시스템 체크 오류 때문에 개인정보 파기가 멈추면 안 된다. 전체 체크는 끄고,
    # 이 명령에 필요한 booth DB·라우터 설정만 handle 에서 직접 확인한다.
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='삭제하지 않고 삭제 대상 건수만 출력합니다.',
        )

    def handle(self, *args, **options):
        errors = booth_database_check(None)
        if errors:
            raise CommandError('booth 설정 오류: ' + ' / '.join(error.msg for error in errors))

        days = conf.retention_days()
        cutoff = timezone.now() - timedelta(days=days)
        expired = Participant.objects.using(conf.BOOTH_DB).filter(created_at__lt=cutoff)
        basis = '보유기간 %d일, %s KST 이전 생성' % (days, kst(cutoff).strftime('%Y-%m-%d %H:%M'))

        if options['dry_run']:
            count = expired.count()
            self.stdout.write('[dry-run] 삭제 대상 %d건 (%s). 삭제하지 않았습니다.' % (count, basis))
            return

        connection = connections[conf.BOOTH_DB]
        deleted = self._delete(connection, expired)
        remaining = Participant.objects.using(conf.BOOTH_DB).count()
        logger.info('[booth] 보유기간 경과 체험자 %d건 삭제, 남은 %d건', deleted, remaining)
        self.stdout.write('삭제 %d건 (%s), 남은 체험자 %d건.' % (deleted, basis, remaining))

        # 테스트처럼 바깥 트랜잭션 안에서 불렸다면 VACUUM·체크포인트를 할 수 없으니 건너뛴다.
        # 이번에 지운 행이 없어도 -wal 에 페이지가 남아 있으면 정리한다. 지난 실행의 체크포인트가
        # 다른 연결 때문에 끝나지 못했으면, 마지막 행사일처럼 더 지울 행이 없는 날에도 옛 사본이
        # 계속 남기 때문이다.
        if connection.vendor == 'sqlite' and not connection.in_atomic_block \
                and (deleted or self._wal_has_frames(connection)):
            self._compact(connection)

    def _delete(self, connection, queryset):
        """삭제하고 삭제 건수를 돌려준다.

        SQLite 는 DELETE 해도 행 내용이 빈 페이지에 그대로 남아 파일을 열면 복구될 수 있다.
        '파기'가 되도록 이 연결에서만 secure_delete 를 켜서 지운 영역을 0 으로 덮어쓴다.
        (uWSGI 워커 연결에는 영향이 없다. PRAGMA 는 연결 단위 설정이다.)
        """
        is_sqlite = connection.vendor == 'sqlite'
        previous = None
        if is_sqlite:
            with connection.cursor() as cursor:
                cursor.execute('PRAGMA secure_delete')
                row = cursor.fetchone()
                previous = row[0] if row else None
                cursor.execute('PRAGMA secure_delete = ON')
        try:
            # Participant 는 다른 모델과 관계·시그널이 없어 Django 가 DELETE 한 문장으로 처리한다.
            # 트랜잭션의 첫 문장이 쓰기라 SQLite 잠금을 timeout(20초)까지 기다려 준다.
            deleted, _ = queryset.delete()
        finally:
            if previous in _SECURE_DELETE_VALUES:
                with connection.cursor() as cursor:
                    cursor.execute('PRAGMA secure_delete = %s' % _SECURE_DELETE_VALUES[previous])
        return deleted

    @staticmethod
    def _wal_path(connection):
        name = str(connection.settings_dict.get('NAME') or '')
        if not name or name.startswith('file:') or ':memory:' in name:
            return None
        return name + '-wal'

    def _wal_has_frames(self, connection):
        path = self._wal_path(connection)
        if not path:
            return False
        try:
            return os.path.getsize(path) > 0
        except OSError:
            return False

    def _compact(self, connection):
        """지운 행의 '예전 사본'까지 파일에서 없앤다. 끝내지 못하면 CommandError(0 이 아닌 종료 코드).

        secure_delete 는 이번 DELETE 로 비는 영역만 0 으로 덮는다. 그 전에 uWSGI 워커가 설문·측정·
        리포트 상태를 UPDATE 하면서 빈 공간에 남긴 옛 행 사본(이름·응답 포함)은 그대로 남는다.
        실제 WAL 파일로 확인한 결과 DELETE 만 하면 지운 이름 72곳, secure_delete 를 켜도 2곳이
        파일에 남았다. VACUUM 으로 DB 를 통째로 다시 쓰고, TRUNCATE 체크포인트로 WAL 에 남은
        페이지 사본까지 비운다.

        다른 연결이 읽기 트랜잭션을 오래 쥐고 있으면(sqlite3 CLI, DB 브라우저, 백업 복사) 체크포인트가
        busy 로 끝난다. 짧게 기다리며 몇 번 더 시도하고, 그래도 안 되면 삭제는 이미 커밋된 상태로
        CommandError 를 낸다. 크론 로그에 실패가 남아야 사람이 원인을 치운다. 다음 실행은 지울 행이
        없어도 -wal 이 비어 있지 않으면 다시 정리한다.
        """
        try:
            with connection.cursor() as cursor:
                cursor.execute('VACUUM')
        except DatabaseError as exc:
            logger.warning('[booth] 삭제 후 VACUUM 실패 (%s)', type(exc).__name__)
            raise CommandError('삭제는 완료했지만 VACUUM 에 실패했습니다(%s). DB 를 연 다른 프로그램을 닫고 '
                               '다시 실행하세요.' % type(exc).__name__)

        problem = ''
        for attempt in range(CHECKPOINT_ATTEMPTS):
            try:
                with connection.cursor() as cursor:
                    cursor.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                    row = cursor.fetchone()
            except DatabaseError as exc:
                problem = type(exc).__name__
            else:
                if not (row and row[0]):
                    return
                problem = '다른 연결이 사용 중'
            if attempt < CHECKPOINT_ATTEMPTS - 1:
                time.sleep(_CHECKPOINT_BACKOFF_SEC[min(attempt, len(_CHECKPOINT_BACKOFF_SEC) - 1)])

        logger.warning('[booth] 삭제 후 WAL 체크포인트를 끝내지 못했습니다 (%s).', problem)
        raise CommandError('삭제는 완료했지만 WAL 체크포인트를 끝내지 못했습니다(%s). booth DB 를 연 다른 '
                           '프로그램(sqlite3, DB 브라우저, 백업)을 닫고 다시 실행하세요.' % problem)
