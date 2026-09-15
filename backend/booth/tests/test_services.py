# -*- coding:utf-8 -*-
"""booth Foundation 테스트: 표기·파싱, 번호 발급, 라우터, 상태 전이, 측정 검증, 리포트 스키마.

반드시 --settings=backend.settings_booth_test 로 실행한다(모든 DB 가 로컬 SQLite).
"""
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.contrib.auth.models import User
from django.db import IntegrityError, OperationalError, connections
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from booth import conf, services
from booth.apps import enable_sqlite_wal
from booth.models import Participant
from booth.report_schema import EXAMPLE_REPORT, INDEX_KEYS, REPORT_SCHEMA, ReportInvalid, validate_report
from booth.routers import BoothRouter
from experiments.models import Experiments

Status = Participant.ReportStatus


# 테스트 클라이언트 요청은 기본이 http 다. booth 는 https 가 아니면 거부하므로, 기존 테스트는
# 로컬 개발용 예외(BOOTH_ALLOW_INSECURE=1)를 켠 채로 돈다. https 강제 자체는 SecureTransportTests 에서
# 이 값을 끄고(None) 확인한다.
TEST_BASE_ENV = {'BOOTH_ALLOW_INSECURE': '1'}


def _clean_env(values):
    env = {k: v for k, v in os.environ.items() if not k.startswith('BOOTH_')}
    env.update(TEST_BASE_ENV)
    for key, value in values.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


@contextmanager
def booth_env(**values):
    """BOOTH_* env 를 모두 지운 깨끗한 상태에서 values 만 설정한다(.env 영향 차단).

    BOOTH_ALLOW_INSECURE=1 은 기본으로 들어간다. 값을 None 으로 주면 그 변수를 지운다.
    """
    with mock.patch.dict(os.environ, _clean_env(values), clear=True):
        yield


class CleanEnvMixin:
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, _clean_env({}), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)


def survey(name='홍길동', consent='동의합니다', **extra):
    answers = {
        '참가자 번호': 'SF-001',
        '이름': name,
        '개인정보 수집·이용 동의': consent,
        '평소 잠들기까지 걸리는 시간': '30분 이상',
    }
    answers.update(extra)
    return answers


def side(**overrides):
    base = {
        'heart_rate': 72, 'rmssd_ms': 35.5, 'sdnn_ms': 48.0,
        'sleep_index': 55.0, 'autonomic_balance': 48.0, 'stress_recovery_index': 60.0, 'rr_count': 300,
    }
    base.update(overrides)
    return base


def measurement_payload(**overrides):
    payload = {
        'measured_at': '2026-09-14T14:30:00+09:00',
        'device_id': 'tab-1',
        'app_version': '1.0.0',
        'before': side(),
        'after': side(heart_rate=68, sleep_index=62.0),
        'displayed': {'before': side(), 'after': side(sleep_index=70.0)},
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# DB 가 필요 없는 순수 함수
# ---------------------------------------------------------------------------
class ConfTests(SimpleTestCase):

    def test_defaults(self):
        with booth_env():
            self.assertEqual(conf.api_key(), '')
            self.assertEqual(conf.number_prefix(), 'SF')
            self.assertEqual(conf.survey_name_title(), '이름')
            self.assertEqual(conf.survey_consent_title(), '개인정보 수집·이용 동의')
            self.assertEqual(conf.survey_number_title(), '참가자 번호')
            self.assertEqual(conf.retention_days(), 14)
            self.assertEqual(conf.pending_window_hours(), 12)
            self.assertEqual(conf.public_base_url(), '')
            self.assertEqual(conf.report_model(), 'claude-opus-5')
            self.assertEqual(conf.report_effort(), 'medium')
            self.assertEqual(conf.report_max_tokens(), 16000)
            self.assertEqual(conf.report_deadline_sec(), 150)

    def test_env_read_at_call_time(self):
        with booth_env(BOOTH_NUMBER_PREFIX='AB', BOOTH_RETENTION_DAYS='7',
                       BOOTH_PUBLIC_BASE_URL='https://example.com/', BOOTH_API_KEY=' k '):
            self.assertEqual(conf.number_prefix(), 'AB')
            self.assertEqual(conf.retention_days(), 7)
            self.assertEqual(conf.public_base_url(), 'https://example.com')
            self.assertEqual(conf.api_key(), 'k')

    def test_invalid_int_falls_back(self):
        with booth_env(BOOTH_RETENTION_DAYS='abc', BOOTH_REPORT_MAX_TOKENS='0'):
            with self.assertLogs('booth.conf', 'WARNING') as logs:
                self.assertEqual(conf.retention_days(), 14)
                self.assertEqual(conf.report_max_tokens(), 16000)
        self.assertEqual(len(logs.output), 2)


class LabelAndParseTests(CleanEnvMixin, SimpleTestCase):

    def test_format_label(self):
        self.assertEqual(services.format_label(42), 'SF-042')
        self.assertEqual(services.format_label(7), 'SF-007')
        self.assertEqual(services.format_label(1234), 'SF-1234')

    def test_parse_number_accepts_variants(self):
        for value in (42, '42', 'SF-042', 'sf042', ' SF-42 ', 'SF 42', 'SF–042', 42.0):
            self.assertEqual(services.parse_number(value), 42, value)

    def test_parse_number_rejects(self):
        for value in (None, '', 'abc', 0, -1, '0', 'AB-042', True, 42.5, float('nan'),
                      '12345678901', 2147483648, [42], {'n': 42}):
            self.assertIsNone(services.parse_number(value), value)

    def test_parse_number_uses_configured_prefix(self):
        with booth_env(BOOTH_NUMBER_PREFIX='AB'):
            self.assertEqual(services.parse_number('AB-042'), 42)
            self.assertIsNone(services.parse_number('SF-042'))

    def test_mask_name(self):
        cases = {
            '홍길동': '홍○동',
            '이수': '이○',
            '남궁민수': '남○○수',
            ' 홍 길동 ': '홍○동',
            '김': '○',
            'John': 'J***',
            'J': '*',
            '': '',
            None: '',
            '   ': '',
        }
        for name, expected in cases.items():
            self.assertEqual(services.mask_name(name), expected, name)

    def test_parse_consent(self):
        agreed = ('동의합니다', ['동의함'], '위 내용을 확인하였으며 동의합니다.')
        refused = ('동의하지 않습니다', '동의 하지 않음', '미동의', '거부', ['비동의'], '동의 안 함',
                   '', None, '예', [])
        for value in agreed:
            self.assertTrue(services.parse_consent(value), value)
        for value in refused:
            self.assertFalse(services.parse_consent(value), value)

    def test_extract_survey_fields(self):
        self.assertEqual(services.extract_survey_fields(survey()), ('홍길동', True))
        # 제목의 공백·필수표시 변형, 목록 응답
        answers = {' 이름 *': ['  홍  길동 '], '개인정보 수집·이용 동의': ['동의하지 않습니다']}
        self.assertEqual(services.extract_survey_fields(answers), ('홍 길동', False))
        self.assertEqual(services.extract_survey_fields({}), ('', False))
        self.assertEqual(services.extract_survey_fields(None), ('', False))

    def test_survey_answers_for_ai_strips_identifying_questions(self):
        result = services.survey_answers_for_ai(survey())
        self.assertEqual(result, {'평소 잠들기까지 걸리는 시간': '30분 이상'})
        self.assertEqual(services.survey_answers_for_ai(None), {})

    def test_normalize_answers(self):
        cleaned = services.normalize_answers({'a': None, 'b': 3, 'c': ['x', None, 1], '  ': 'skip'})
        self.assertEqual(cleaned, {'a': '', 'b': '3', 'c': ['x', '1']})
        with self.assertRaises(ValueError):
            services.normalize_answers(['not', 'dict'])

    def test_kst_and_isoformat(self):
        utc = datetime(2026, 9, 14, 16, 0, tzinfo=dt_timezone.utc)
        local = services.kst(utc)
        self.assertEqual((local.year, local.month, local.day, local.hour), (2026, 9, 15, 1))
        self.assertEqual(local.utcoffset(), timedelta(hours=9))
        self.assertEqual(services.kst_isoformat(utc), '2026-09-15T01:00:00+09:00')
        self.assertIsNone(services.kst(None))
        self.assertIsNone(services.kst_isoformat(None))
        naive = datetime(2026, 9, 14, 16, 0)
        self.assertEqual(services.kst(naive).hour, 1)          # naive 는 UTC 로 본다

    def test_parse_iso_datetime(self):
        dt = services.parse_iso_datetime('2026-09-14T05:00:00Z')
        self.assertEqual(dt, datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc))
        naive = services.parse_iso_datetime('2026-09-14T14:00:00')     # 시간대 없음 -> KST
        self.assertEqual(naive.utcoffset(), timedelta(hours=9))
        for bad in ('', 'nope', None, 123, '2026-13-40T00:00:00'):
            self.assertIsNone(services.parse_iso_datetime(bad), bad)

    def test_display_sides(self):
        cleaned = services.validate_measurement(measurement_payload())
        sides = services.display_sides(cleaned)
        self.assertTrue(sides.is_displayed)
        self.assertEqual(sides.caption, '체험 화면 표시값 (시연용 보정 포함)')
        self.assertEqual(sides.after['sleep_index'], 70.0)

        raw = services.validate_measurement(measurement_payload(displayed=None))
        sides = services.display_sides(raw)
        self.assertFalse(sides.is_displayed)
        self.assertEqual(sides.caption, '측정값')
        self.assertEqual(sides.after['sleep_index'], 62.0)

        empty = services.display_sides(None)
        self.assertEqual(empty.caption, '측정값')
        self.assertIsNone(empty.before['heart_rate'])
        self.assertEqual([key for key, _ in services.COMPARISON_ROWS],
                         ['sleep_index', 'autonomic_balance', 'stress_recovery_index', 'heart_rate'])


class MeasurementValidationTests(SimpleTestCase):

    def test_valid_payload_is_normalized(self):
        payload = measurement_payload()
        payload['before']['unknown'] = 1
        payload['extra_top'] = 'drop'
        payload['after']['heart_rate'] = 67.6          # int 필드는 반올림
        payload['after']['rr_count'] = None
        del payload['after']['sdnn_ms']                # 빠진 키는 None
        cleaned = services.validate_measurement(payload)

        self.assertEqual(set(cleaned), {'measured_at', 'device_id', 'app_version', 'before', 'after', 'displayed'})
        self.assertEqual(cleaned['measured_at'], '2026-09-14T14:30:00+09:00')
        self.assertEqual(set(cleaned['before']), set(services.SIDE_FIELDS))
        self.assertEqual(cleaned['after']['heart_rate'], 68)
        self.assertIsInstance(cleaned['after']['heart_rate'], int)
        self.assertIsNone(cleaned['after']['rr_count'])
        self.assertIsNone(cleaned['after']['sdnn_ms'])
        self.assertIsInstance(cleaned['before']['sleep_index'], float)

    def test_optional_parts(self):
        payload = measurement_payload()
        del payload['displayed']
        del payload['device_id']
        del payload['app_version']
        cleaned = services.validate_measurement(payload)
        self.assertIsNone(cleaned['displayed'])
        self.assertIsNone(cleaned['device_id'])

    def test_invalid_payloads(self):
        bad_cases = [
            None,
            [],
            measurement_payload(measured_at=None),
            measurement_payload(measured_at='yesterday'),
            measurement_payload(before=None),
            measurement_payload(after='x'),
            measurement_payload(displayed='x'),
            measurement_payload(displayed={'before': side()}),
            measurement_payload(device_id=123),
            measurement_payload(before=side(heart_rate=True)),
            measurement_payload(before=side(heart_rate='72')),
            measurement_payload(before=side(heart_rate=25)),
            measurement_payload(before=side(heart_rate=221)),
            measurement_payload(before=side(sleep_index=100.1)),
            measurement_payload(before=side(rmssd_ms=-1)),
            measurement_payload(before=side(sdnn_ms=501)),
            measurement_payload(before=side(rr_count=1001)),
            measurement_payload(before=side(autonomic_balance=float('inf'))),
        ]
        for payload in bad_cases:
            with self.assertRaises(services.MeasurementInvalid, msg=repr(payload)):
                services.validate_measurement(payload)

    def test_boundaries_allowed(self):
        cleaned = services.validate_measurement(measurement_payload(
            before=side(heart_rate=30, rmssd_ms=0, sdnn_ms=500, sleep_index=0, rr_count=0),
            after=side(heart_rate=220, stress_recovery_index=100, rr_count=1000),
        ))
        self.assertEqual(cleaned['before']['heart_rate'], 30)
        self.assertEqual(cleaned['after']['rr_count'], 1000)


class ReportSchemaTests(SimpleTestCase):

    UNSUPPORTED = {'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf',
                   'minLength', 'maxLength', 'pattern', 'maxItems', 'uniqueItems'}

    def _walk(self, schema, path='$'):
        self.assertFalse(self.UNSUPPORTED & set(schema), path)
        if 'minItems' in schema:
            self.assertIn(schema['minItems'], (0, 1), path)
        if schema.get('type') == 'object':
            self.assertIs(schema.get('additionalProperties'), False, path)
            self.assertEqual(sorted(schema['required']), sorted(schema['properties']), path)
            for key, sub in schema['properties'].items():
                self._walk(sub, '%s.%s' % (path, key))
        if schema.get('type') == 'array':
            self._walk(schema['items'], path + '[]')

    def test_schema_is_structured_output_compatible(self):
        self._walk(REPORT_SCHEMA)
        self.assertEqual(set(REPORT_SCHEMA['properties']),
                         {'headline', 'survey_insights', 'indices', 'tips', 'closing'})
        key_schema = REPORT_SCHEMA['properties']['indices']['items']['properties']['key']
        self.assertEqual(key_schema['enum'], list(INDEX_KEYS))

    def test_example_report_validates(self):
        self.assertEqual(validate_report(EXAMPLE_REPORT), EXAMPLE_REPORT)
        self.assertEqual({item['key'] for item in EXAMPLE_REPORT['indices']}, set(INDEX_KEYS))

    def test_validate_report_rejects_and_strips(self):
        import copy
        with self.assertRaises(ReportInvalid):
            validate_report(None)
        for key in ('headline', 'survey_insights', 'indices', 'tips', 'closing'):
            broken = copy.deepcopy(EXAMPLE_REPORT)
            del broken[key]
            with self.assertRaises(ReportInvalid, msg=key):
                validate_report(broken)
        broken = copy.deepcopy(EXAMPLE_REPORT)
        broken['indices'][0]['key'] = 'made_up'
        with self.assertRaises(ReportInvalid):
            validate_report(broken)
        broken = copy.deepcopy(EXAMPLE_REPORT)
        broken['tips'].append(3)
        with self.assertRaises(ReportInvalid):
            validate_report(broken)

        extra = copy.deepcopy(EXAMPLE_REPORT)
        extra['injected'] = '<script>'
        extra['headline']['extra'] = 'x'
        self.assertEqual(validate_report(extra), EXAMPLE_REPORT)


# ---------------------------------------------------------------------------
# 라우터·WAL
# ---------------------------------------------------------------------------
class RouterTests(SimpleTestCase):

    def setUp(self):
        self.router = BoothRouter()

    def test_db_for_read_write(self):
        self.assertEqual(self.router.db_for_read(Participant), 'booth')
        self.assertEqual(self.router.db_for_write(Participant), 'booth')
        self.assertIsNone(self.router.db_for_read(User))
        self.assertIsNone(self.router.db_for_write(Experiments))

    def test_allow_relation(self):
        p1, p2 = Participant(number=1), Participant(number=2)
        self.assertTrue(self.router.allow_relation(p1, p2))
        self.assertFalse(self.router.allow_relation(p1, User()))
        self.assertFalse(self.router.allow_relation(User(), p1))
        self.assertIsNone(self.router.allow_relation(User(), Experiments()))

    def test_allow_migrate(self):
        self.assertTrue(self.router.allow_migrate('booth', 'booth', model_name='participant'))
        self.assertFalse(self.router.allow_migrate('default', 'booth', model_name='participant'))
        self.assertFalse(self.router.allow_migrate('booth', 'auth', model_name='user'))
        self.assertFalse(self.router.allow_migrate('booth', 'experiments'))
        self.assertIsNone(self.router.allow_migrate('default', 'auth', model_name='user'))
        self.assertIsNone(self.router.allow_migrate('default', 'experiments'))


class WalHandlerTests(SimpleTestCase):

    def test_only_booth_sqlite_gets_wal(self):
        booth_conn = mock.MagicMock(alias='booth', vendor='sqlite')
        enable_sqlite_wal(sender=None, connection=booth_conn)
        executed = [c.args[0] for c in booth_conn.cursor.return_value.__enter__.return_value.execute.call_args_list]
        self.assertEqual(executed, ['PRAGMA journal_mode=WAL', 'PRAGMA journal_size_limit=1048576'])

        for alias, vendor in (('default', 'sqlite'), ('default', 'mysql'), ('booth', 'postgresql')):
            other = mock.MagicMock(alias=alias, vendor=vendor)
            enable_sqlite_wal(sender=None, connection=other)
            other.cursor.assert_not_called()


# ---------------------------------------------------------------------------
# DB 사용 테스트
# ---------------------------------------------------------------------------
class BoothDbTestCase(CleanEnvMixin, TestCase):
    databases = {'default', 'booth'}

    def setUp(self):
        super().setUp()
        sleep_patcher = mock.patch('booth.services.time.sleep')   # 재시도 대기 제거
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def reload(self, participant):
        return Participant.objects.using('booth').get(pk=participant.pk)

    def ready_participant(self, consent='동의합니다'):
        p = services.issue_participant('phone')
        services.apply_survey(p, survey(consent=consent))
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        return p


class DatabaseIsolationTests(BoothDbTestCase):

    def test_tables_live_only_in_booth_db(self):
        booth_tables = connections['booth'].introspection.table_names()
        default_tables = connections['default'].introspection.table_names()
        self.assertIn('booth_participant', booth_tables)
        self.assertNotIn('booth_participant', default_tables)
        self.assertNotIn('auth_user', booth_tables)
        self.assertIn('auth_user', default_tables)

    def test_participant_is_written_to_booth(self):
        p = services.issue_participant('kiosk')
        self.assertEqual(p._state.db, 'booth')
        self.assertTrue(Participant.objects.filter(pk=p.pk).exists())   # 라우터 경유 조회


class IssueParticipantTests(BoothDbTestCase):

    def test_sequential_numbers_and_tokens(self):
        first = services.issue_participant('phone')
        second = services.issue_participant('kiosk')
        self.assertEqual((first.number, second.number), (1, 2))
        self.assertEqual(first.label, 'SF-001')
        self.assertEqual(second.source, 'kiosk')
        self.assertNotEqual(first.token, second.token)
        self.assertGreaterEqual(len(first.token), 32)
        self.assertEqual(first.report_status, Status.WAITING)
        self.assertEqual(str(first), 'SF-001')

    def test_invalid_source(self):
        with self.assertRaises(ValueError):
            services.issue_participant('tablet')

    def test_retries_on_number_collision(self):
        services.issue_participant('phone')                 # number 1 선점
        with mock.patch('booth.services._current_max_number', side_effect=[0, 1]) as max_mock:
            with self.assertLogs('booth.services', 'WARNING') as logs:
                p = services.issue_participant('phone')     # 첫 시도는 1 과 충돌 -> 재시도
        self.assertIn('IntegrityError', logs.output[0])
        self.assertEqual(p.number, 2)
        self.assertEqual(max_mock.call_count, 2)
        self.assertEqual(Participant.objects.using('booth').count(), 2)

    def test_gives_up_after_five_collisions(self):
        services.issue_participant('phone')
        with mock.patch('booth.services._current_max_number', return_value=0) as max_mock:
            with self.assertLogs('booth.services', 'WARNING') as logs:
                with self.assertRaises(IntegrityError):
                    services.issue_participant('phone')
        self.assertEqual(max_mock.call_count, services.WRITE_RETRIES)
        self.assertEqual(len(logs.output), services.WRITE_RETRIES)

    def test_retries_on_sqlite_lock(self):
        real = services._acquire_write_lock
        calls = {'n': 0}

        def flaky(pk=0):
            calls['n'] += 1
            if calls['n'] == 1:
                raise OperationalError('database is locked')
            return real(pk)

        with mock.patch('booth.services._acquire_write_lock', side_effect=flaky):
            with self.assertLogs('booth.services', 'WARNING') as logs:
                p = services.issue_participant('phone')
        self.assertIn('OperationalError', logs.output[0])
        self.assertEqual(p.number, 1)
        self.assertEqual(calls['n'], 2)

    def test_other_operational_errors_are_not_retried(self):
        with mock.patch('booth.services._acquire_write_lock', side_effect=OperationalError('disk I/O error')):
            with self.assertRaises(OperationalError):
                services.issue_participant('phone')


class LookupTests(BoothDbTestCase):

    def test_get_by_token(self):
        p = services.issue_participant('phone')
        self.assertEqual(services.get_participant_by_token(p.token).pk, p.pk)
        for bad in (None, '', 'nope', 'x' * 65, 123):
            self.assertIsNone(services.get_participant_by_token(bad))

    def test_expired_token_and_number_are_hidden(self):
        p = services.issue_participant('phone')
        Participant.objects.using('booth').filter(pk=p.pk).update(
            created_at=timezone.now() - timedelta(days=14, minutes=1))
        self.assertIsNone(services.get_participant_by_token(p.token))
        self.assertIsNone(services.get_participant_by_number(p.number))
        with booth_env(BOOTH_RETENTION_DAYS='30'):
            self.assertIsNotNone(services.get_participant_by_token(p.token))

    def test_get_by_number(self):
        p = services.issue_participant('phone')
        self.assertEqual(services.get_participant_by_number('SF-001').pk, p.pk)
        self.assertEqual(services.get_participant_by_number(1).pk, p.pk)
        self.assertIsNone(services.get_participant_by_number(2))
        self.assertIsNone(services.get_participant_by_number('bad'))

    def test_build_report_url(self):
        p = services.issue_participant('phone')
        path = '/booth/p/%s/' % p.token
        self.assertEqual(services.build_report_url(p), path)
        request = RequestFactory().get('/booth/api/measurement/')
        self.assertEqual(services.build_report_url(p, request), 'http://testserver' + path)
        with booth_env(BOOTH_PUBLIC_BASE_URL='https://180.83.245.145/'):
            self.assertEqual(services.build_report_url(p, request), 'https://180.83.245.145' + path)

    def test_deletion_date_is_kst(self):
        p = services.issue_participant('phone')
        created = datetime(2026, 9, 14, 16, 0, tzinfo=dt_timezone.utc)      # KST 9/15 01:00
        Participant.objects.using('booth').filter(pk=p.pk).update(created_at=created)
        p = self.reload(p)
        self.assertEqual(services.deletion_date(p), date(2026, 9, 29))
        with booth_env(BOOTH_RETENTION_DAYS='1'):
            self.assertEqual(services.deletion_date(p), date(2026, 9, 16))

    def test_masked_name_property(self):
        p = services.issue_participant('phone')
        services.apply_survey(p, survey(name='홍길동'))
        self.assertEqual(p.masked_name, '홍○동')


class SurveyTests(BoothDbTestCase):

    def test_apply_survey_sets_fields(self):
        p = services.issue_participant('phone')
        services.apply_survey(p, survey(), submitted_at='2026-09-14T05:00:00Z')
        stored = self.reload(p)
        for obj in (p, stored):             # 넘긴 인스턴스도 최신 값으로 갱신된다
            self.assertEqual(obj.name, '홍길동')
            self.assertTrue(obj.consent)
            self.assertEqual(obj.survey_revision, 1)
            self.assertEqual(obj.survey_received_at, datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc))
            self.assertEqual(obj.survey_answers['평소 잠들기까지 걸리는 시간'], '30분 이상')
            self.assertEqual(obj.report_status, Status.WAITING)     # 측정이 아직 없다

    def test_invalid_submitted_at_uses_now(self):
        p = services.issue_participant('phone')
        before = timezone.now()
        services.apply_survey(p, survey(), submitted_at='garbage')
        self.assertGreaterEqual(p.survey_received_at, before)

    def test_resubmission_overwrites(self):
        p = services.issue_participant('phone')
        services.apply_survey(p, survey(name='홍길동'))
        services.apply_survey(p, survey(name='박지민'))
        self.assertEqual(self.reload(p).name, '박지민')
        services.apply_survey(p, survey(name='박지민', consent='동의하지 않습니다'))
        stored = self.reload(p)
        self.assertEqual(stored.name, '')                  # 동의 철회: 이름·응답을 남기지 않는다
        self.assertEqual(stored.survey_answers, {})
        self.assertFalse(stored.consent)
        self.assertEqual(stored.survey_revision, 3)

    def test_non_dict_answers_rejected(self):
        p = services.issue_participant('phone')
        with self.assertRaises(ValueError):
            services.apply_survey(p, 'not a dict')

    def test_stale_instance_does_not_clobber_other_writes(self):
        p = services.issue_participant('phone')
        stale = Participant.objects.using('booth').get(pk=p.pk)       # 측정 도착 전의 사본
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        services.apply_survey(stale, survey())                          # 오래된 사본으로 설문 저장
        stored = self.reload(p)
        self.assertTrue(stored.has_measurement)
        self.assertEqual(stored.report_status, Status.PENDING)


class MeasurementTests(BoothDbTestCase):

    def test_apply_and_conflict(self):
        p = services.issue_participant('phone')
        m = services.validate_measurement(measurement_payload())
        services.apply_measurement(p, m)
        self.assertTrue(self.reload(p).has_measurement)
        with self.assertRaises(services.AlreadyMeasured):
            services.apply_measurement(p, m)
        stale = Participant.objects.using('booth').get(pk=p.pk)
        stale.measurement_received_at = None           # 오래된 사본이어도 DB 기준으로 판정
        stale.measurement = None
        with self.assertRaises(services.AlreadyMeasured):
            services.apply_measurement(stale, m)

    def test_overwrite(self):
        p = services.issue_participant('phone')
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        new = services.validate_measurement(measurement_payload(device_id='tab-2'))
        services.apply_measurement(p, new, overwrite=True)
        self.assertEqual(self.reload(p).measurement['device_id'], 'tab-2')

    def test_clear_measurement(self):
        p = self.ready_participant()
        services.clear_measurement(p)
        stored = self.reload(p)
        self.assertIsNone(stored.measurement)
        self.assertIsNone(stored.measurement_received_at)
        self.assertEqual(stored.report_status, Status.WAITING)


class ReportStatusTransitionTests(BoothDbTestCase):

    def mark(self, participant, **fields):
        Participant.objects.using('booth').filter(pk=participant.pk).update(**fields)
        return self.reload(participant)

    def test_waiting_until_both_present(self):
        p = services.issue_participant('phone')
        self.assertEqual(p.report_status, Status.WAITING)
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        self.assertEqual(p.report_status, Status.WAITING)
        services.apply_survey(p, survey())
        self.assertEqual(p.report_status, Status.PENDING)

    def test_survey_then_measurement_is_pending(self):
        p = self.ready_participant()
        self.assertEqual(self.reload(p).report_status, Status.PENDING)

    def test_no_consent(self):
        p = self.ready_participant(consent='동의하지 않습니다')
        self.assertEqual(self.reload(p).report_status, Status.NO_CONSENT)
        # 동의로 재제출해도, 거부 상태에서 받은 측정은 값을 저장하지 않았으므로 다시 측정해야 한다
        services.apply_survey(p, survey(consent='동의합니다'))
        self.assertEqual(self.reload(p).report_status, Status.WAITING)
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        self.assertEqual(self.reload(p).report_status, Status.PENDING)

    def test_consent_withdrawn_clears_report(self):
        p = self.ready_participant()
        p = self.mark(p, report_status=Status.DONE, report=EXAMPLE_REPORT, report_attempts=1,
                      report_done_at=timezone.now())
        services.apply_survey(p, survey(consent='거부'))
        stored = self.reload(p)
        self.assertEqual(stored.report_status, Status.NO_CONSENT)
        self.assertIsNone(stored.report)
        self.assertIsNone(stored.report_done_at)

    def test_refresh_does_not_downgrade_generating_or_done(self):
        p = self.ready_participant()
        for current in (Status.GENERATING, Status.DONE, Status.PENDING):
            p = self.mark(p, report_status=current, report=EXAMPLE_REPORT)
            self.assertEqual(services.refresh_report_status(p), current)
            self.assertEqual(p.report, EXAMPLE_REPORT)

    def test_replacement_resets_done_and_generating(self):
        for current in (Status.DONE, Status.GENERATING, Status.FAILED):
            p = self.ready_participant()
            p = self.mark(p, report_status=current, report=EXAMPLE_REPORT, report_attempts=5,
                          report_error='x', report_started_at=timezone.now())
            services.apply_measurement(
                p, services.validate_measurement(measurement_payload(device_id='tab-9')), overwrite=True)
            stored = self.reload(p)
            self.assertEqual(stored.report_status, Status.PENDING, current)
            self.assertIsNone(stored.report)
            self.assertEqual(stored.report_error, '')
            self.assertIsNone(stored.report_started_at)
            self.assertEqual(stored.report_attempts, 0)

    def test_survey_resubmission_resets_done(self):
        p = self.ready_participant()
        p = self.mark(p, report_status=Status.DONE, report=EXAMPLE_REPORT)
        services.apply_survey(p, survey(**{'평소 잠들기까지 걸리는 시간': '10분 이내'}))
        stored = self.reload(p)
        self.assertEqual(stored.report_status, Status.PENDING)
        self.assertIsNone(stored.report)

    def test_failed_goes_pending_unless_attempts_exhausted(self):
        p = self.ready_participant()
        p = self.mark(p, report_status=Status.FAILED, report_attempts=2)
        self.assertEqual(services.refresh_report_status(p, save=True), Status.PENDING)
        self.assertEqual(self.reload(p).report_status, Status.PENDING)

        p = self.mark(p, report_status=Status.FAILED, report_attempts=conf.MAX_REPORT_ATTEMPTS)
        self.assertEqual(services.refresh_report_status(p), Status.FAILED)

    def test_clearing_measurement_resets_report(self):
        p = self.ready_participant()
        p = self.mark(p, report_status=Status.DONE, report=EXAMPLE_REPORT)
        services.clear_measurement(p)
        stored = self.reload(p)
        self.assertEqual(stored.report_status, Status.WAITING)
        self.assertIsNone(stored.report)

    def test_refresh_save_only_touches_report_fields(self):
        p = self.ready_participant()
        stale = self.mark(p, report_status=Status.WAITING)
        Participant.objects.using('booth').filter(pk=p.pk).update(name='다른이름')
        services.refresh_report_status(stale, save=True)
        stored = self.reload(p)
        self.assertEqual(stored.report_status, Status.PENDING)
        self.assertEqual(stored.name, '다른이름')        # 오래된 name 으로 덮어쓰지 않는다
