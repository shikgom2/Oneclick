# -*- coding:utf-8 -*-
"""booth_purge 관리 명령 테스트: 보유기간 경과 삭제, 최근 데이터 유지, --dry-run, 출력에 개인정보 없음.

반드시 --settings=backend.settings_booth_test 로 실행한다(모든 DB 가 로컬 SQLite).
"""
import re
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.checks import Error
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from booth import services
from booth.models import Participant
from booth.tests.test_services import CleanEnvMixin, booth_env, measurement_payload, survey

DAY = timedelta(days=1)
MINUTE = timedelta(minutes=1)


class PurgeHelpers(CleanEnvMixin):
    databases = {'default', 'booth'}

    def setUp(self):
        super().setUp()
        sleep_patcher = mock.patch('booth.services.time.sleep')   # 쓰기 재시도 대기 제거
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def make(self, age, name='홍길동'):
        """age 만큼 전에 만들어진, 설문·측정이 모두 있는 참가자."""
        p = services.issue_participant('phone')
        services.apply_survey(p, survey(name=name))
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        # created_at 은 auto_now_add 라 save 로는 못 바꾼다.
        Participant.objects.using('booth').filter(pk=p.pk).update(created_at=timezone.now() - age)
        return Participant.objects.using('booth').get(pk=p.pk)

    def purge(self, *args):
        out, err = StringIO(), StringIO()
        call_command('booth_purge', *args, stdout=out, stderr=err)
        return out.getvalue() + err.getvalue()

    def remaining_numbers(self):
        return set(Participant.objects.using('booth').values_list('number', flat=True))

    def assert_count(self, output, pattern, expected):
        match = re.search(pattern, output)
        self.assertIsNotNone(match, output)
        self.assertEqual(int(match.group(1)), expected, output)


class PurgeTests(PurgeHelpers, TestCase):

    def test_deletes_only_expired(self):
        very_old = self.make(30 * DAY)
        just_expired = self.make(14 * DAY + MINUTE)
        almost_expired = self.make(14 * DAY - MINUTE)
        fresh = self.make(timedelta(0))

        output = self.purge()

        self.assertEqual(self.remaining_numbers(), {almost_expired.number, fresh.number})
        self.assert_count(output, r'삭제 (\d+)건', 2)
        self.assert_count(output, r'남은 체험자 (\d+)건', 2)
        self.assertNotIn('dry-run', output)
        for gone in (very_old, just_expired):
            self.assertFalse(Participant.objects.using('booth').filter(pk=gone.pk).exists())

    def test_deletes_exactly_what_lookup_already_hides(self):
        people = [self.make(age) for age in (20 * DAY, 14 * DAY + MINUTE, 14 * DAY - MINUTE, 2 * DAY)]
        visible = {p.number for p in people if services.get_participant_by_token(p.token) is not None}
        self.assertEqual(len(visible), 2)

        self.purge()

        self.assertEqual(self.remaining_numbers(), visible)

    def test_respects_retention_env(self):
        old = self.make(4 * DAY)
        recent = self.make(2 * DAY)
        with booth_env(BOOTH_RETENTION_DAYS='3'):
            output = self.purge()
        self.assertEqual(self.remaining_numbers(), {recent.number})
        self.assertNotIn(old.number, self.remaining_numbers())
        self.assertIn('보유기간 3일', output)

    def test_dry_run_keeps_everything(self):
        self.make(30 * DAY)
        self.make(15 * DAY)
        self.make(DAY)

        output = self.purge('--dry-run')

        self.assertEqual(Participant.objects.using('booth').count(), 3)
        self.assertIn('[dry-run]', output)
        self.assert_count(output, r'삭제 대상 (\d+)건', 2)

    def test_nothing_to_delete(self):
        self.make(DAY)
        output = self.purge()
        self.assert_count(output, r'삭제 (\d+)건', 0)
        self.assertEqual(Participant.objects.using('booth').count(), 1)

    def test_output_and_logs_have_no_personal_data(self):
        people = [self.make(30 * DAY, name='홍길동'), self.make(30 * DAY, name='John Smith'),
                  self.make(DAY, name='박지민')]
        forbidden = ['홍길동', '박지민', 'John', '홍○동', '박○민', '30분 이상']
        for p in people:
            forbidden += [p.token, p.label]

        dry_output = self.purge('--dry-run')
        with self.assertLogs('booth', 'INFO') as logs:
            real_output = self.purge()

        for text in (dry_output, real_output, '\n'.join(logs.output)):
            for value in forbidden:
                self.assertNotIn(value, text)

    def test_never_touches_default_database(self):
        self.make(30 * DAY)
        with self.assertNumQueries(0, using='default'):
            self.purge('--dry-run')
            self.purge()

    def test_refuses_when_booth_database_is_misconfigured(self):
        self.make(30 * DAY)
        broken = [Error("settings.DATABASES 에 'booth' 항목이 없습니다.", id='booth.E001')]
        with mock.patch('booth.management.commands.booth_purge.booth_database_check', return_value=broken):
            with self.assertRaises(CommandError) as ctx:
                self.purge()
        self.assertIn('booth', str(ctx.exception))
        self.assertEqual(Participant.objects.using('booth').count(), 1)

    def test_secure_delete_setting_is_restored(self):
        self.make(30 * DAY)
        with connections['booth'].cursor() as cursor:
            cursor.execute('PRAGMA secure_delete')
            before = cursor.fetchone()[0]
        self.purge()
        with connections['booth'].cursor() as cursor:
            cursor.execute('PRAGMA secure_delete')
            self.assertEqual(cursor.fetchone()[0], before)


class PurgeOutsideTransactionTests(PurgeHelpers, TransactionTestCase):
    """운영 크론처럼 바깥 트랜잭션 없이 실행되는 경로(삭제 후 WAL 체크포인트 포함)."""

    def test_purge_commits_and_checkpoints(self):
        self.make(30 * DAY)
        fresh = self.make(DAY)
        output = self.purge()
        self.assertEqual(self.remaining_numbers(), {fresh.number})
        self.assert_count(output, r'삭제 (\d+)건', 1)
