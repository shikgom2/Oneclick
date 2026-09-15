# -*- coding:utf-8 -*-
"""booth 운영 안정성 테스트.

동시·시간당 생성 상한과 평생 생성 한도, 스트림 열기 재시도 예산, 시간 상수의 불변식, 설문 중복 전송,
측정값 극단값, 대기 목록 상한, 개인 페이지 상태 JSON, 스태프 복구 명령, booth_purge 의 WAL 잔여물 정리,
배포 시스템 체크, 리포트 프롬프트·검증 규칙.

Claude 는 부르지 않는다. 반드시 --settings=backend.settings_booth_test 로 실행한다.
"""
import copy
import io
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import timedelta
from unittest import mock

import anthropic
import httpx
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError as DjangoOperationalError
from django.test import SimpleTestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from booth import checks, conf, report, services, views_pages
from booth.management.commands.booth_purge import Command as PurgeCommand
from booth.models import Participant
from booth.report_schema import EXAMPLE_REPORT, INDEX_KEYS, ReportInvalid, validate_report
from booth.tests.test_api import ApiTestCase
from booth.tests.test_pages import PageTestCase
from booth.tests.test_purge import DAY, PurgeHelpers
from booth.tests.test_report import (REAL_NAME, Clock, FakeStream, ReportDbTestCase, fake_client, fake_message,
                                     text_block)
from booth.tests.test_services import CleanEnvMixin, booth_env, measurement_payload, side, survey

Status = Participant.ReportStatus

UWSGI_HARAKIRI_SEC = 480      # 운영 oneclick.ini


# ---------------------------------------------------------------------------
# 생성 상한
# ---------------------------------------------------------------------------
class GenerationCapacityTests(ReportDbTestCase):

    def test_concurrent_generation_cap(self):
        busy = self.ready()
        self.mark(busy, report_status=Status.GENERATING, report_attempts=1, report_started_at=timezone.now())
        waiting = self.ready()
        with booth_env(BOOTH_MAX_CONCURRENT_REPORTS='1'):
            with self.assertLogs('booth.report', 'INFO') as logs:
                self.assertFalse(report.claim_for_generation(waiting.pk))
            self.assertIn('동시 생성 상한', '\n'.join(logs.output))
            fresh = self.reload(waiting)
            self.assertEqual(fresh.report_status, Status.PENDING)    # 미룰 뿐 실패로 만들지 않는다
            self.assertEqual(fresh.report_attempts, 0)

            # 멈춘(오래된) generating 은 세지 않는다.
            self.mark(busy, report_started_at=timezone.now() - timedelta(seconds=conf.STALE_GENERATING_SEC + 1))
            self.assertTrue(report.claim_for_generation(waiting.pk))

    def test_hourly_generation_cap(self):
        earlier = self.ready()
        self.mark(earlier, report_status=Status.DONE, report=EXAMPLE_REPORT, report_started_at=timezone.now())
        later = self.ready()
        with booth_env(BOOTH_REPORT_MAX_PER_HOUR='1'):
            with self.assertLogs('booth.report', 'INFO'):
                self.assertFalse(report.claim_for_generation(later.pk))
            self.mark(earlier, report_started_at=timezone.now() - timedelta(minutes=61))
            self.assertTrue(report.claim_for_generation(later.pk))

    def test_resubmission_does_not_reset_lifetime_generations(self):
        p = self.ready()
        self.assertTrue(report.claim_for_generation(p.pk))
        self.mark(p, report_status=Status.FAILED, report_attempts=conf.MAX_REPORT_ATTEMPTS)
        services.apply_survey(self.reload(p), survey(name=REAL_NAME, **{'질문': '새 답'}))
        fresh = self.reload(p)
        self.assertEqual(fresh.report_status, Status.PENDING)
        self.assertEqual(fresh.report_attempts, 0)
        self.assertEqual(fresh.report_generations, 1)

    def test_lifetime_cap_fails_claim_and_resubmission(self):
        p = self.ready()
        self.mark(p, report_generations=conf.MAX_REPORT_GENERATIONS)
        with self.assertLogs('booth.report', 'WARNING'):
            self.assertFalse(report.claim_for_generation(p.pk))
        fresh = self.reload(p)
        self.assertEqual(fresh.report_status, Status.FAILED)
        self.assertEqual(fresh.report_error, services.GENERATIONS_EXHAUSTED_ERROR)
        self.assertEqual(views_pages._page_state(fresh), 'failed_final')
        self.assertFalse(views_pages._can_generate(fresh))

        services.apply_survey(fresh, survey(name=REAL_NAME, **{'질문': '또 다른 답'}))
        again = self.reload(p)
        self.assertEqual(again.report_status, Status.FAILED)            # 재제출로 한도가 풀리지 않는다
        self.assertEqual(again.report_error, services.GENERATIONS_EXHAUSTED_ERROR)


class GenerateRetryAfterTests(PageTestCase):

    def test_pending_after_generate_asks_page_to_retry_later(self):
        p = self.ready()
        with mock.patch('booth.report.generate_report', return_value='pending'):
            response = self.client.post(self.url('generate', p))
        data = response.json()
        self.assertEqual(data['report_status'], 'pending')
        self.assertEqual(data['retry_after_sec'], views_pages.GENERATE_RETRY_AFTER_SEC)


# ---------------------------------------------------------------------------
# 스트림 열기 재시도 예산과 시간 상수
# ---------------------------------------------------------------------------
class FailingOpen:
    """stream(...) 컨텍스트에 들어갈 때(__enter__) 오류를 내는 가짜. SDK 는 요청을 이때 보낸다."""

    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        raise self.exc

    def __exit__(self, *exc):
        return False


def status_error(code):
    request = httpx.Request('POST', 'http://booth-test.invalid/v1/messages')
    return anthropic.APIStatusError('status %d' % code, response=httpx.Response(code, request=request), body=None)


class OpenStreamRetryTests(CleanEnvMixin, SimpleTestCase):

    def run_call(self, side_effect, clock):
        client = fake_client(None)
        client.beta.messages.stream.side_effect = side_effect
        with mock.patch('booth.report.anthropic.Anthropic', return_value=client), \
                mock.patch('booth.report.monotonic', clock), \
                mock.patch('booth.report.time.sleep') as sleep:
            try:
                return report.call_claude('SYS', {}), client, sleep, None
            except Exception as exc:  # noqa: BLE001
                return None, client, sleep, exc

    def good_stream(self):
        return FakeStream(events=[mock.Mock(type='message_stop')], message=fake_message())

    def test_retryable_open_error_is_retried_within_budget(self):
        for error in (status_error(529), status_error(429), status_error(500),
                      anthropic.APIConnectionError(message='x', request=httpx.Request('POST', 'http://x'))):
            with self.assertLogs('booth.report', 'WARNING'):
                message, client, sleep, exc = self.run_call([FailingOpen(error), self.good_stream()], Clock(0))
            self.assertIsNone(exc, error)
            self.assertIsNotNone(message)
            self.assertEqual(client.beta.messages.stream.call_count, 2)
            sleep.assert_called_once_with(2.0)

    def test_no_retry_when_remaining_time_is_short(self):
        # 시작 0 -> 마감 150. 실패 시점 100 이면 남은 50초 < 대기+연결+응답(72초)이라 다시 열지 않는다.
        message, client, sleep, exc = self.run_call([FailingOpen(status_error(529)), self.good_stream()],
                                                    Clock(0, 100))
        self.assertIsInstance(exc, anthropic.APIStatusError)
        self.assertEqual(client.beta.messages.stream.call_count, 1)
        sleep.assert_not_called()

    def test_non_retryable_error_is_raised_immediately(self):
        message, client, sleep, exc = self.run_call([FailingOpen(status_error(400))], Clock(0))
        self.assertIsInstance(exc, anthropic.APIStatusError)
        sleep.assert_not_called()

    def test_retries_are_capped(self):
        errors = [FailingOpen(status_error(529)) for _ in range(report.MAX_OPEN_RETRIES + 2)]
        with self.assertLogs('booth.report', 'WARNING'):
            message, client, sleep, exc = self.run_call(errors, Clock(0))
        self.assertIsInstance(exc, anthropic.APIStatusError)
        self.assertEqual(client.beta.messages.stream.call_count, report.MAX_OPEN_RETRIES + 1)


class TimingInvariantTests(SimpleTestCase):

    def test_stale_limit_exceeds_worker_lifetime_and_stays_below_harakiri(self):
        worker_lifetime = conf.REPORT_DEADLINE_MAX_SEC + report.READ_TIMEOUT_SEC + report.CONNECT_TIMEOUT_SEC
        self.assertGreater(conf.STALE_GENERATING_SEC, worker_lifetime)
        self.assertLess(conf.STALE_GENERATING_SEC, UWSGI_HARAKIRI_SEC)

    def test_deadline_above_max_falls_back_to_default(self):
        with booth_env(BOOTH_REPORT_DEADLINE_SEC=str(conf.REPORT_DEADLINE_MAX_SEC + 1)):
            with self.assertLogs('booth.conf', 'WARNING'):
                self.assertEqual(conf.report_deadline_sec(), 150)
        with booth_env(BOOTH_REPORT_DEADLINE_SEC=str(conf.REPORT_DEADLINE_MAX_SEC)):
            self.assertEqual(conf.report_deadline_sec(), conf.REPORT_DEADLINE_MAX_SEC)


class PersonalPageTimingTests(PageTestCase):

    def test_page_regenerates_only_after_stale_limit(self):
        p = self.ready()
        content = self.client.get(self.url('personal', p)).content.decode('utf-8')
        value = int(content.split('data-regenerate-after-sec="')[1].split('"')[0])
        self.assertGreater(value, conf.STALE_GENERATING_SEC)

    def test_status_state_changes_when_consent_flips_before_measurement(self):
        p = self.with_survey(self.issue(), consent='동의하지 않습니다')
        first = self.client.get(self.url('status', p)).json()
        self.assertEqual(first['state'], 'no_consent')
        services.apply_survey(p, survey(consent='동의합니다'))
        second = self.client.get(self.url('status', p)).json()
        self.assertEqual(second['state'], 'after_survey')
        for key in ('survey_received', 'measurement_received', 'report_status'):
            self.assertEqual(first[key], second[key], key)       # state 가 없으면 페이지가 바뀐 줄 모른다


# ---------------------------------------------------------------------------
# 설문 중복·측정 극단값·대기 목록
# ---------------------------------------------------------------------------
class SurveyRedeliveryTests(ReportDbTestCase):

    def test_identical_redelivery_keeps_report_and_attempts(self):
        p = services.issue_participant('phone')
        submitted = '2026-09-14T05:00:00Z'
        services.apply_survey(p, survey(), submitted_at=submitted)
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        self.mark(p, report_status=Status.DONE, report=EXAMPLE_REPORT, report_attempts=2)

        services.apply_survey(self.reload(p), survey(), submitted_at=submitted)
        fresh = self.reload(p)
        self.assertEqual((fresh.report_status, fresh.report_attempts, fresh.survey_revision), (Status.DONE, 2, 1))
        self.assertEqual(fresh.report, EXAMPLE_REPORT)

    def test_older_delivery_is_ignored_and_newer_replaces(self):
        p = services.issue_participant('phone')
        services.apply_survey(p, survey(name='홍길동'), submitted_at='2026-09-14T05:00:00Z')
        services.apply_survey(p, survey(name='옛응답'), submitted_at='2026-09-14T04:00:00Z')
        self.assertEqual(self.reload(p).name, '홍길동')
        services.apply_survey(p, survey(name='새응답'), submitted_at='2026-09-14T06:00:00Z')
        fresh = self.reload(p)
        self.assertEqual((fresh.name, fresh.survey_revision), ('새응답', 2))

    def test_extreme_submitted_at_falls_back_to_server_time(self):
        p = services.issue_participant('phone')
        before = timezone.now()
        services.apply_survey(p, survey(), submitted_at='0001-01-01T00:00:00+09:00')
        self.assertGreaterEqual(self.reload(p).survey_received_at, before)


class MeasurementExtremeValueTests(ApiTestCase):

    def test_huge_numbers_and_extreme_dates_are_400_not_500(self):
        p = self.make(1)
        services.apply_survey(p, survey())
        for measurement in (measurement_payload(before=side(rmssd_ms=10 ** 400)),
                            measurement_payload(after=side(heart_rate=10 ** 400)),
                            measurement_payload(measured_at='0001-01-01T00:00:00+09:00'),
                            measurement_payload(measured_at='9999-12-31T23:59:59-09:00')):
            response = self.post_json('booth:api_measurement', {'number': 1, 'measurement': measurement})
            self.assertError(response, 400)
        self.assertFalse(self.reload(p).has_measurement)


class PendingListCapTests(ApiTestCase):

    def test_newest_surveys_first_and_unsurveyed_capped(self):
        now = timezone.now()
        older = self.make(1)
        newer = self.make(2)
        services.apply_survey(older, survey(name='홍길동'), submitted_at=(now - timedelta(minutes=9)).isoformat())
        services.apply_survey(newer, survey(name='박지민'), submitted_at=(now - timedelta(minutes=1)).isoformat())
        for number in range(3, 3 + 25):
            self.make(number)

        body = self.get_api(reverse('booth:api_pending')).json()
        numbers = [item['number'] for item in body['participants']]
        self.assertEqual(numbers[:2], [2, 1])
        self.assertEqual(numbers[2:], list(range(27, 7, -1)))        # 발급 최신순 20명
        self.assertEqual(body['unsurveyed_omitted'], 5)


# ---------------------------------------------------------------------------
# 스태프 복구 명령
# ---------------------------------------------------------------------------
class ResetReportCommandTests(ReportDbTestCase):

    def run_reset(self, *args):
        out = io.StringIO()
        with self.assertLogs('booth.services', 'WARNING'):
            call_command('booth_reset_report', *args, stdout=out)
        return out.getvalue()

    def test_resets_final_failure_so_it_can_be_generated_again(self):
        p = self.ready()
        self.mark(p, report_status=Status.FAILED, report_attempts=conf.MAX_REPORT_ATTEMPTS,
                  report_generations=conf.MAX_REPORT_GENERATIONS, report_error='AI 거절(x)',
                  report_started_at=timezone.now())
        output = self.run_reset('SF-001')
        fresh = self.reload(p)
        self.assertEqual(fresh.report_status, Status.PENDING)
        self.assertEqual((fresh.report_attempts, fresh.report_generations, fresh.report_error), (0, 0, ''))
        self.assertIsNone(fresh.report)
        self.assertTrue(fresh.has_measurement)
        self.assertIn('SF-001: failed -> pending', output)
        self.assertNotIn(REAL_NAME, output)
        self.assertTrue(report.claim_for_generation(p.pk))

    def test_clear_measurement(self):
        p = self.ready()
        self.mark(p, report_status=Status.DONE, report=EXAMPLE_REPORT)
        output = self.run_reset('1', '--clear-measurement')
        fresh = self.reload(p)
        self.assertEqual(fresh.report_status, Status.WAITING)
        self.assertFalse(fresh.has_measurement)
        self.assertIn('done -> waiting (측정값 삭제)', output)

    def test_broken_stored_report_is_cleared(self):
        p = self.ready()
        self.mark(p, report_status=Status.DONE, report={'unexpected': True})
        self.run_reset('SF-001')
        fresh = self.reload(p)
        self.assertEqual(fresh.report_status, Status.PENDING)
        self.assertIsNone(fresh.report)

    def test_running_generation_result_is_discarded(self):
        p = self.ready()
        claimed_at = report._claim(p.pk)
        self.assertIsNotNone(claimed_at)
        self.run_reset('SF-001')
        self.assertEqual(report._finish(p.pk, claimed_at, Status.DONE, EXAMPLE_REPORT, ''), 0)
        self.assertIsNone(self.reload(p).report)

    def test_unknown_participant_is_error_without_echoing_input(self):
        self.ready()
        for value in ('홍길동', 'SF-099'):
            with self.assertRaises(CommandError) as ctx:
                call_command('booth_reset_report', value, stdout=io.StringIO())
            self.assertNotIn('홍길동', str(ctx.exception))


# ---------------------------------------------------------------------------
# booth_purge: WAL 잔여물
# ---------------------------------------------------------------------------
class _RawCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, sql):
        try:
            return self.cursor.execute(sql)
        except sqlite3.DatabaseError as exc:          # Django 연결처럼 django.db 예외로 바꾼다
            raise DjangoOperationalError(str(exc)) from exc

    def fetchone(self):
        return self.cursor.fetchone()


class _RawSqliteConnection:
    """PurgeCommand._compact 가 쓰는 만큼만 흉내 낸 파일 기반 SQLite 연결(테스트 DB 는 메모리라 WAL 이 없다)."""
    vendor = 'sqlite'
    in_atomic_block = False

    def __init__(self, path):
        self.conn = sqlite3.connect(path, timeout=0.1, isolation_level=None)
        self.settings_dict = {'NAME': path}

    @contextmanager
    def cursor(self):
        cursor = self.conn.cursor()
        try:
            yield _RawCursor(cursor)
        finally:
            cursor.close()


class PurgeWalResidueTests(SimpleTestCase):

    def test_busy_checkpoint_fails_loudly_and_next_run_removes_residue(self):
        marker = b'ZZOLDNAMEZZ'
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = os.path.join(tmp, 'booth.sqlite3')
            writer = _RawSqliteConnection(path)
            conn = writer.conn
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA wal_autocheckpoint=0')        # 테스트 중 자동 체크포인트가 WAL 을 비우지 않게
            conn.execute('CREATE TABLE t (v TEXT)')
            conn.execute('BEGIN')
            for i in range(50):
                conn.execute('INSERT INTO t VALUES (?)', ('%s-%d' % (marker.decode(), i),))
            conn.execute('COMMIT')
            conn.execute('PRAGMA secure_delete=ON')
            conn.execute('DELETE FROM t')

            command = PurgeCommand()
            reader = sqlite3.connect(path, timeout=0.1, isolation_level=None)
            try:
                reader.execute('BEGIN')
                reader.execute('SELECT count(*) FROM t').fetchone()      # 오래 열린 읽기 트랜잭션
                with mock.patch('booth.management.commands.booth_purge.time.sleep'), \
                        self.assertLogs('booth', 'WARNING'), self.assertRaises(CommandError):
                    command._compact(writer)
                self.assertTrue(command._wal_has_frames(writer))
            finally:
                reader.close()

            command._compact(writer)
            self.assertFalse(command._wal_has_frames(writer))
            conn.close()
            data = b''
            for suffix in ('', '-wal'):
                if os.path.exists(path + suffix):
                    with open(path + suffix, 'rb') as handle:
                        data += handle.read()
            self.assertNotIn(marker, data)


class PurgeCompactionDecisionTests(PurgeHelpers, TransactionTestCase):

    def test_leftover_wal_is_compacted_even_when_nothing_expired(self):
        self.make(DAY)
        with mock.patch.object(PurgeCommand, '_wal_has_frames', return_value=True), \
                mock.patch.object(PurgeCommand, '_compact') as compact:
            output = self.purge()
        compact.assert_called_once()
        self.assert_count(output, r'삭제 (\d+)건', 0)

    def test_nothing_expired_and_empty_wal_skips_compaction(self):
        self.make(DAY)
        with mock.patch.object(PurgeCommand, '_wal_has_frames', return_value=False), \
                mock.patch.object(PurgeCommand, '_compact') as compact:
            self.purge()
        compact.assert_not_called()


class WalSizeLimitTests(SimpleTestCase):

    def test_booth_connections_limit_wal_size(self):
        from booth.apps import enable_sqlite_wal
        booth_conn = mock.MagicMock(alias='booth', vendor='sqlite')
        enable_sqlite_wal(sender=None, connection=booth_conn)
        executed = [c.args[0] for c in booth_conn.cursor.return_value.__enter__.return_value.execute.call_args_list]
        self.assertIn('PRAGMA journal_size_limit=1048576', executed)


# ---------------------------------------------------------------------------
# 배포 시스템 체크
# ---------------------------------------------------------------------------
class DeployCheckTests(CleanEnvMixin, SimpleTestCase):

    def ids(self, db_name='/var/lib/oneclick-booth/booth.sqlite3'):
        with mock.patch('booth.checks._booth_db_name', return_value=db_name):
            return {warning.id for warning in checks.booth_deploy_check(None)}

    def test_clean_production_like_configuration_has_no_warnings(self):
        with booth_env(BOOTH_ALLOW_INSECURE=None, BOOTH_API_KEY='k', BOOTH_FORM_API_KEY='f',
                       BOOTH_PUBLIC_BASE_URL='https://180.83.245.145',
                       BOOTH_FORM_URL='https://docs.google.com/forms/d/e/X/viewform',
                       BOOTH_FORM_NUMBER_ENTRY='entry.1'):
            self.assertEqual(self.ids(), set())

    def test_form_url_without_number_entry_warns(self):
        # 인쇄 QR 이 폼을 바로 열므로 번호 미리 채우기가 없으면 휴대폰 체험자는 번호를 알 수 없다.
        form_url = 'https://docs.google.com/forms/d/e/X/viewform'
        with booth_env(BOOTH_FORM_URL=form_url):
            self.assertIn('booth.W005', self.ids())
        with booth_env(BOOTH_FORM_URL=form_url, BOOTH_FORM_NUMBER_ENTRY='  '):
            self.assertIn('booth.W005', self.ids())
        with booth_env(BOOTH_FORM_URL=form_url, BOOTH_FORM_NUMBER_ENTRY='1234567'):
            self.assertNotIn('booth.W005', self.ids())
        with booth_env():                                   # 폼 자체가 없으면 W005 가 아니다
            self.assertNotIn('booth.W005', self.ids())

    def test_each_warning(self):
        inside = str(settings.BASE_DIR / 'booth.sqlite3')
        with booth_env(BOOTH_ALLOW_INSECURE=None, BOOTH_API_KEY='k', BOOTH_FORM_API_KEY='f'):
            self.assertIn('booth.W001', self.ids(inside))
        with booth_env(BOOTH_ALLOW_INSECURE=None, BOOTH_API_KEY='k', BOOTH_FORM_API_KEY='f',
                       BOOTH_PUBLIC_BASE_URL='http://180.83.245.145:8000'):
            self.assertIn('booth.W002', self.ids())
        with booth_env(BOOTH_ALLOW_INSECURE=None, BOOTH_API_KEY='k'):
            self.assertIn('booth.W003', self.ids())
        with booth_env(BOOTH_ALLOW_INSECURE='1', BOOTH_API_KEY='k', BOOTH_FORM_API_KEY='f'):
            self.assertIn('booth.W004', self.ids())

    def test_path_inside(self):
        base = settings.BASE_DIR
        self.assertTrue(checks.path_inside(str(base / 'booth.sqlite3'), base))
        self.assertFalse(checks.path_inside('file:memorydb_booth?mode=memory&cache=shared', base))
        self.assertFalse(checks.path_inside(os.path.join(tempfile.gettempdir(), 'booth.sqlite3'), base))
        self.assertFalse(checks.path_inside('', base))


# ---------------------------------------------------------------------------
# 리포트 프롬프트·검증
# ---------------------------------------------------------------------------
class PromptRuleTests(SimpleTestCase):

    def test_autonomic_balance_direction_is_explicit(self):
        guide = report._INDEX_GUIDE['autonomic_balance']
        self.assertIn('점수가 높을수록 LF/HF 비가 1.0 에 가깝다', guide)
        self.assertIn('50 은 균형이나 가운데를 뜻하지 않으며', guide)
        self.assertIn('Autonomic Balance 는 점수가 높을수록 LF/HF 비가 1.0 에 가까워', report.SYSTEM_PROMPT)

    def test_other_guides_and_rules(self):
        self.assertIn('수면의 질', report._INDEX_GUIDE['sleep_index'])
        self.assertIn('높을수록 이완·회복', report._INDEX_GUIDE['stress_recovery_index'])
        self.assertIn('Sleep Index 22', report._INDEX_GUIDE['default_values'])
        for rule in ('합쇼체', '지시가 아니다', '측정값·화면 표시값의 숫자', '높아졌다'):
            self.assertIn(rule, report.SYSTEM_PROMPT)

    def test_example_report_follows_the_rules(self):
        by_key = {item['key']: item for item in EXAMPLE_REPORT['indices']}
        self.assertIn('균형에 가까울수록 높게', by_key['autonomic_balance']['meaning'])
        self.assertNotIn('가운데', by_key['autonomic_balance']['meaning'])
        self.assertIn('5~95', by_key['sleep_index']['meaning'])
        dumped = json.dumps(EXAMPLE_REPORT, ensure_ascii=False)
        self.assertNotIn('0~100', dumped)
        self.assertNotIn('답했습니다', dumped)


class StrictReportValidationTests(SimpleTestCase):

    def variant(self, mutate):
        data = copy.deepcopy(EXAMPLE_REPORT)
        mutate(data)
        return data

    def test_strict_rejects_duplicates_missing_and_blank_text(self):
        cases = [
            lambda d: d['indices'].append(copy.deepcopy(d['indices'][0])),
            lambda d: d['indices'].pop(1),
            lambda d: d['headline'].update(one_liner='   '),
            lambda d: d['tips'].append(''),
            lambda d: d.update(closing=''),
            lambda d: d['survey_insights'][0].update(body=' '),
            lambda d: d.update(tips=[]),
        ]
        for mutate in cases:
            with self.assertRaises(ReportInvalid):
                validate_report(self.variant(mutate), strict=True)

    def test_lenient_render_dedupes_reorders_and_drops_blank_tips(self):
        data = self.variant(lambda d: (d['indices'].reverse(), d['indices'].append(copy.deepcopy(d['indices'][0])),
                                       d['tips'].append('  ')))
        result = validate_report(data)
        self.assertEqual([item['key'] for item in result['indices']], list(INDEX_KEYS))
        self.assertEqual(result['tips'], EXAMPLE_REPORT['tips'])

    def test_interpret_message_uses_strict_validation(self):
        data = self.variant(lambda d: d['indices'].append(copy.deepcopy(d['indices'][0])))
        report_data, error = report.interpret_message(fake_message(content=[text_block(json.dumps(data))]))
        self.assertIsNone(report_data)
        self.assertEqual(error, 'AI 출력 형식 오류: indices 에 sleep_index 가 두 번 있음')


class ReportTemplateNoticeTests(PageTestCase):

    def test_raw_values_have_caution_and_every_report_has_share_notice_and_glosses(self):
        p = self.done(displayed=False)
        content = self.client.get(self.url('personal', p)).content.decode('utf-8')
        self.assertIn('짧은 체험 중 한 번 측정한 값이라 전·후 차이를 개인의 변화나 자극의 효과로 볼 수 없습니다.', content)
        self.assertIn('이 주소를 아는 사람은 누구나 이 리포트를 볼 수 있습니다.', content)
        self.assertInHTML('<h3>Autonomic Balance <span class="gloss-inline">(자율신경 균형)</span></h3>', content)
