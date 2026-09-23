# -*- coding:utf-8 -*-
"""AI 리포트 생성 테스트: claim, 프롬프트 자료의 개인정보 제거, 응답 해석, 마감, 관리 명령.

Claude 는 절대 실제로 부르지 않는다. call_claude 를 mock 하거나, 클라이언트를
httpx.MockTransport(프로세스 안에서 응답을 만들어 주는 가짜 전송)로 바꿔 SDK 경로만 검증한다.
반드시 --settings=backend.settings_booth_test 로 실행한다.
"""
import io
import json
import os
import threading
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import anthropic
import httpx
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from booth import conf, report, services
from booth.models import Participant
from booth.report_schema import EXAMPLE_REPORT, REPORT_SCHEMA, validate_report
from booth.tests.test_services import CleanEnvMixin, measurement_payload, side, survey

Status = Participant.ReportStatus

REAL_NAME = '홍길동'
# 테스트 env(CleanEnvMixin)에서는 BOOTH_* 가 비어 마감이 기본값(상한 200초)이다. 마감 시각 계산이 있는 테스트의 기준.
DEADLINE = conf.REPORT_DEADLINE_MAX_SEC

# 구글 폼의 수면 문항 9개(제목에 번호·공백 변형 포함). 설문 지표(survey_metrics) 가 계산되는 응답.
SLEEP_SURVEY = {
    '1. 어젯밤 잠자리에 든 시각': '밤 11시 ~ 12시',
    '2.  오늘 아침 잠자리에서 나온 시각 ': '아침 7시 ~ 8시',
    '3) 잠자리에 든 뒤 잠들기까지 걸린 시간': '16 ~ 30분',
    '4. 실제로 잠든 시간 (총 수면 시간)': '6 ~ 7시간',
    '5. 밤중에 깬 횟수 (기억나는 만큼)': '2회',
    '6. 오늘 아침 일어났을 때 개운한 정도': '조금 개운함',
    '7. 어젯밤 잠에 대한 만족도': '보통',
    '8. 지금 이 순간, 얼마나 개운한가요?': '약간 졸림',
    '9. 평소 취침 시각이 얼마나 일정한가요? (최근 한 달 기준)': '대체로 같음',
}


def text_block(text):
    return SimpleNamespace(type='text', text=text)


def fake_message(content=None, stop_reason='end_turn', stop_details=None, model='claude-opus-5'):
    if content is None:
        content = [SimpleNamespace(type='thinking', thinking=''),
                   text_block(json.dumps(EXAMPLE_REPORT, ensure_ascii=False))]
    return SimpleNamespace(
        model=model, stop_reason=stop_reason, stop_details=stop_details, content=content,
        usage=SimpleNamespace(input_tokens=1200, output_tokens=900, iterations=None),
    )


class Clock:
    """monotonic 대용. 정해진 값을 차례로 돌려주고, 다 쓰면 마지막 값을 반복한다."""

    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]


class FakeStream:
    """client.beta.messages.stream(...) 이 돌려주는 컨텍스트 매니저 겸 스트림."""

    def __init__(self, events=(), message=None, block_until_closed=False, end_quietly=False):
        self.events = list(events)
        self.message = message
        self.block_until_closed = block_until_closed
        self.end_quietly = end_quietly
        self.closed = threading.Event()
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.exited = True
        self.close()

    def close(self):
        self.closed.set()

    def __iter__(self):
        if self.block_until_closed:
            # 내용 없는 thinking 구간 흉내: 닫힐 때까지 기다린다(테스트가 멈추지 않게 5초 상한).
            self.closed.wait(5)
            if self.end_quietly:
                return
            raise httpx.ReadError('stream closed')
        for event in self.events:
            yield event

    def get_final_message(self):
        return self.message


def fake_client(stream):
    client = mock.MagicMock()
    client.beta.messages.stream.return_value = stream
    return client


class ReportDbTestCase(CleanEnvMixin, TestCase):
    databases = {'default', 'booth'}

    def setUp(self):
        super().setUp()
        sleep_patcher = mock.patch('booth.services.time.sleep')
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def reload(self, participant):
        return Participant.objects.using('booth').get(pk=participant.pk)

    def ready(self, consent='동의합니다', answers=None, measurement=None):
        p = services.issue_participant('phone')
        services.apply_survey(p, answers if answers is not None else survey(name=REAL_NAME, consent=consent))
        services.apply_measurement(p, services.validate_measurement(measurement or measurement_payload()))
        return p

    def mark(self, participant, **fields):
        Participant.objects.using('booth').filter(pk=participant.pk).update(**fields)


# ---------------------------------------------------------------------------
# claim
# ---------------------------------------------------------------------------
class ClaimTests(ReportDbTestCase):

    def test_claim_is_exclusive(self):
        p = self.ready()
        self.assertEqual(p.report_status, Status.PENDING)
        self.assertTrue(report.claim_for_generation(p.pk))
        self.assertFalse(report.claim_for_generation(p.pk))
        fresh = self.reload(p)
        self.assertEqual(fresh.report_status, Status.GENERATING)
        self.assertEqual(fresh.report_attempts, 1)
        self.assertIsNotNone(fresh.report_started_at)

    def test_stale_generating_is_reclaimed(self):
        p = self.ready()
        self.assertTrue(report.claim_for_generation(p.pk))

        self.mark(p, report_started_at=timezone.now() - timedelta(seconds=100))
        self.assertFalse(report.claim_for_generation(p.pk))          # 아직 생성 중으로 본다

        self.mark(p, report_started_at=timezone.now() - timedelta(seconds=conf.STALE_GENERATING_SEC + 1))
        self.assertTrue(report.claim_for_generation(p.pk))           # 멈춘 작업 회수
        self.assertEqual(self.reload(p).report_attempts, 2)

    def test_failed_is_claimable_until_attempt_cap(self):
        p = self.ready()
        self.mark(p, report_status=Status.FAILED, report_attempts=conf.MAX_REPORT_ATTEMPTS - 1)
        self.assertTrue(report.claim_for_generation(p.pk))
        self.assertEqual(self.reload(p).report_attempts, conf.MAX_REPORT_ATTEMPTS)

        self.mark(p, report_status=Status.FAILED)
        self.assertFalse(report.claim_for_generation(p.pk))
        fresh = self.reload(p)
        self.assertEqual(fresh.report_status, Status.FAILED)
        self.assertEqual(fresh.report_attempts, conf.MAX_REPORT_ATTEMPTS)

    def test_exhausted_stale_generating_becomes_failed(self):
        p = self.ready()
        self.mark(p, report_status=Status.GENERATING, report_attempts=conf.MAX_REPORT_ATTEMPTS,
                  report_started_at=timezone.now() - timedelta(seconds=conf.STALE_GENERATING_SEC + 5))
        self.assertFalse(report.claim_for_generation(p.pk))
        fresh = self.reload(p)
        self.assertEqual(fresh.report_status, Status.FAILED)
        self.assertIn('시도 횟수', fresh.report_error)

    def test_exhausted_but_fresh_generating_is_left_alone(self):
        p = self.ready()
        self.mark(p, report_status=Status.GENERATING, report_attempts=conf.MAX_REPORT_ATTEMPTS,
                  report_started_at=timezone.now())
        self.assertFalse(report.claim_for_generation(p.pk))
        self.assertEqual(self.reload(p).report_status, Status.GENERATING)

    def test_not_ready_rows_are_not_claimed(self):
        waiting = services.issue_participant('phone')
        services.apply_survey(waiting, survey())
        self.assertFalse(report.claim_for_generation(waiting.pk))
        self.assertEqual(self.reload(waiting).report_status, Status.WAITING)

        no_consent = self.ready(consent='동의하지 않습니다')
        self.assertEqual(no_consent.report_status, Status.NO_CONSENT)
        self.assertFalse(report.claim_for_generation(no_consent.pk))
        # 상태를 강제로 pending 으로 바꿔도 consent=False 조건에서 걸린다.
        self.mark(no_consent, report_status=Status.PENDING)
        self.assertFalse(report.claim_for_generation(no_consent.pk))

    def test_done_is_not_reclaimed(self):
        p = self.ready()
        self.mark(p, report_status=Status.DONE, report=EXAMPLE_REPORT)
        self.assertFalse(report.claim_for_generation(p.pk))
        self.assertEqual(self.reload(p).report_status, Status.DONE)


# ---------------------------------------------------------------------------
# 프롬프트 자료
# ---------------------------------------------------------------------------
class PayloadTests(ReportDbTestCase):

    def assert_no_identifiers(self, text, participant):
        self.assertNotIn(REAL_NAME, text)
        self.assertNotIn(participant.token, text)
        self.assertNotIn(participant.label, text)
        self.assertNotIn('참가자 번호', text)
        self.assertNotIn('개인정보 수집·이용 동의', text)
        self.assertNotIn('"이름"', text)

    def test_payload_excludes_identity(self):
        p = self.ready()
        payload = report.build_user_payload(self.reload(p))
        self.assertEqual(payload['survey_answers'], {'평소 잠들기까지 걸리는 시간': '30분 이상'})
        dumped = json.dumps(payload, ensure_ascii=False)
        self.assert_no_identifiers(dumped, p)
        self.assert_no_identifiers(report.build_user_message(payload), p)
        self.assertNotIn(REAL_NAME, report.SYSTEM_PROMPT)

    def test_name_inside_free_text_is_scrubbed(self):
        p = services.issue_participant('phone')
        answers = survey(name=REAL_NAME, **{
            '하고 싶은 말': '저는 홍 길동이고 번호는 sf-001, 홍길동 입니다',
            '관심사': ['수면', REAL_NAME],
        })
        services.apply_survey(p, answers)
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        payload = report.build_user_payload(self.reload(p))
        dumped = json.dumps(payload, ensure_ascii=False)
        self.assert_no_identifiers(dumped, p)
        self.assertNotIn('sf-001', dumped)
        self.assertNotIn('홍 길동', dumped)
        self.assertIn('수면', payload['survey_answers']['관심사'])

    def test_screen_table_note_without_numbers(self):
        p = self.ready()
        m = report.build_user_payload(self.reload(p))['screen_table']
        self.assertEqual(m['source'], 'displayed')
        self.assertIn('시연용 보정', m['note'])
        self.assertEqual(set(m), {'source', 'note', 'table_rows'})
        self.assertEqual(m['table_rows'], [label for _, label in services.COMPARISON_ROWS])
        # 전·후 표의 숫자는 보내지 않는다(보정된 전·후 방향을 문장에 쓰면 곧 효과 주장이 된다).
        dumped = json.dumps(m, ensure_ascii=False)
        for value in ('70', '72', '55', '48', '60', '35.5'):
            self.assertNotIn(value, dumped)

    def test_raw_values_and_missing_notes(self):
        payload_in = measurement_payload(displayed=None,
                                         before=side(sleep_index=None),
                                         after=side(sleep_index=None, heart_rate=None))
        p = self.ready(answers=survey(**{'평소 잠들기까지 걸리는 시간': ''}), measurement=payload_in)
        payload = report.build_user_payload(self.reload(p))
        self.assertEqual(payload['screen_table']['source'], 'raw')
        self.assertNotIn('시연용', payload['screen_table']['note'])
        missing = ' / '.join(payload['missing'])
        self.assertIn('설문 응답', missing)
        self.assertIn('Sleep Index 전·후 값', missing)
        self.assertIn('심박수 (bpm) 체험 후 값', missing)
        self.assertIsNone(payload['pre_measurement']['values']['sleep_index'])
        self.assertIn('Sleep Index', payload['pre_measurement']['missing'])

    def test_payload_has_pre_values_and_survey_metrics_but_never_after_or_displayed(self):
        # 체험 전 값은 특징적인 숫자로, 체험 후·화면 표시값은 참고 범위 문구(60~100, 70 이상 등)와 겹치지 않는
        # 소수로 둔다. 후·표시 숫자는 payload 어디에도 없어야 한다.
        payload_in = measurement_payload(
            before=side(heart_rate=73, rmssd_ms=36.3, sdnn_ms=47.7, sleep_index=54.4, autonomic_balance=47.3,
                        stress_recovery_index=61.1, rr_count=301),
            after=side(heart_rate=59, rmssd_ms=41.7, sdnn_ms=52.9, sleep_index=63.3, autonomic_balance=57.7,
                       stress_recovery_index=66.6, rr_count=299),
            displayed={'before': side(sleep_index=54.4, rmssd_ms=36.3, heart_rate=73),
                       'after': side(heart_rate=58, rmssd_ms=44.4, sdnn_ms=53.3, sleep_index=71.1,
                                     autonomic_balance=58.8, stress_recovery_index=77.7, rr_count=298)},
        )
        answers = survey(name=REAL_NAME, **SLEEP_SURVEY)
        del answers['평소 잠들기까지 걸리는 시간']         # 기본 문항이 3번 문항보다 먼저 잠들기까지 문항으로 잡히지 않게
        p = self.ready(answers=answers, measurement=payload_in)
        payload = report.build_user_payload(self.reload(p))
        dumped = json.dumps(payload, ensure_ascii=False)

        pre = payload['pre_measurement']
        self.assertEqual(pre['values'], {
            'heart_rate_bpm': 73, 'rmssd_ms': 36.3, 'sdnn_ms': 47.7, 'rr_count': 301,
            'sleep_index': 54.4, 'autonomic_balance': 47.3, 'stress_recovery_index': 61.1,
        })
        self.assertEqual(pre['categories']['heart_rate'], '참고 범위 안')
        self.assertEqual(pre['categories']['stress_recovery_index'], '중간 (앱 등급 Warning)')
        self.assertIn('60초', pre['method'])
        self.assertIn('체험 후 값은 이 자료에 없다', pre['method'])
        self.assertEqual(pre['missing'], [])
        for value in ('59', '41.7', '52.9', '63.3', '57.7', '66.6', '299',        # 체험 후 원 측정값
                      '58', '44.4', '53.3', '71.1', '58.8', '77.7', '298'):      # 화면 표시값(보정 포함)
            self.assertNotIn(value, dumped, value)
        # measurement 의 after/displayed 객체 자체가 없다(screen_table.source 의 낱말 'displayed' 는 출처 표시일 뿐).
        self.assertNotIn('"displayed":', dumped)
        self.assertNotIn('"after":', dumped)
        self.assertEqual(payload['screen_table']['source'], 'displayed')

        metrics = payload['survey_metrics']
        self.assertEqual((metrics['bedtime'], metrics['wake_time'], metrics['time_in_bed_hours']), ('23:30', '07:30', 8.0))
        self.assertEqual((metrics['total_sleep_hours'], metrics['sleep_efficiency_percent']), (6.5, 81.2))
        self.assertEqual(metrics['sleep_latency_minutes_max'], 30)
        self.assertEqual(metrics['awakenings'], 2)
        self.assertEqual(metrics['components'], {'latency': 1, 'duration': 1, 'efficiency': 1, 'awakenings': 2})
        self.assertEqual(metrics['simple_score']['score'], 5)
        self.assertEqual(metrics['simple_score']['band'], '보통')
        self.assertEqual(metrics['self_ratings']['alertness'], '약간 졸림')
        self.assertIn('추정', metrics['note'])
        self.assertEqual(set(payload), {'survey_answers', 'survey_metrics', 'pre_measurement', 'screen_table',
                                        'index_guide', 'missing'})
        self.assert_no_identifiers(dumped, p)

    def test_survey_metrics_answers_are_scrubbed_too(self):
        # 지표 payload 의 answers 에는 응답 문구가 다시 들어간다. 신원 정보를 지운 뒤 계산해야 한다.
        answers = survey(name=REAL_NAME, **SLEEP_SURVEY)
        answers['1. 어젯밤 잠자리에 든 시각'] = '밤 11시 ~ 12시 (%s 기준)' % REAL_NAME
        p = self.ready(answers=answers)
        payload = report.build_user_payload(self.reload(p))
        self.assertEqual(payload['survey_metrics']['answers']['bedtime'], '밤 11시 ~ 12시 (○○○ 기준)')
        self.assertEqual(payload['survey_metrics']['bedtime'], '23:30')
        self.assert_no_identifiers(json.dumps(payload, ensure_ascii=False), p)

    def test_missing_pre_measurement_is_listed(self):
        empty = {key: None for key in services.SIDE_FIELDS}
        p = self.ready(measurement=measurement_payload(before=empty, displayed=None))
        payload = report.build_user_payload(self.reload(p))
        self.assertIn('체험 전 측정값(심박수·RMSSD)', payload['missing'])
        self.assertEqual(len(payload['pre_measurement']['missing']), 7)
        self.assertEqual(payload['pre_measurement']['categories']['heart_rate'], '링 접촉 부족으로 계산 불가')

    def test_request_shape(self):
        with mock.patch.dict(os.environ, {'BOOTH_REPORT_MODEL': 'claude-test-model',
                                          'BOOTH_REPORT_EFFORT': 'low',
                                          'BOOTH_REPORT_MAX_TOKENS': '8000'}):
            request = report.build_request('SYS', {'a': 1})
        self.assertEqual(request['model'], 'claude-test-model')
        self.assertEqual(request['max_tokens'], 8000)
        self.assertEqual(request['betas'], ['server-side-fallback-2026-07-01'])
        self.assertEqual(request['thinking'], {'type': 'adaptive'})
        self.assertEqual(request['output_config'], {
            'effort': 'low', 'format': {'type': 'json_schema', 'schema': REPORT_SCHEMA},
        })
        self.assertEqual(request['extra_body'], {'fallbacks': 'default'})
        self.assertEqual(request['system'], 'SYS')
        self.assertEqual(request['messages'][0]['role'], 'user')
        self.assertIn('"a": 1', request['messages'][0]['content'])


# ---------------------------------------------------------------------------
# generate_report (call_claude mock)
# ---------------------------------------------------------------------------
class GenerateReportTests(ReportDbTestCase):

    def generate(self, participant, **mock_kwargs):
        with mock.patch('booth.report.call_claude', **mock_kwargs) as call:
            status = report.generate_report(participant)
        return status, call

    def test_success_saves_done(self):
        p = self.ready()
        with self.assertLogs('booth.report', 'INFO') as logs:
            status, call = self.generate(p, return_value=fake_message())
        self.assertEqual(status, Status.DONE)
        self.assertEqual(p.report_status, Status.DONE)           # 넘겨받은 인스턴스도 최신화
        fresh = self.reload(p)
        self.assertEqual(fresh.report_status, Status.DONE)
        self.assertEqual(fresh.report, validate_report(EXAMPLE_REPORT))
        self.assertEqual(fresh.report_error, '')
        self.assertEqual(fresh.report_attempts, 1)
        self.assertIsNotNone(fresh.report_done_at)

        system, payload = call.call_args[0]
        self.assertEqual(system, report.SYSTEM_PROMPT)
        self.assertNotIn(REAL_NAME, json.dumps(payload, ensure_ascii=False))
        log_text = '\n'.join(logs.output)
        self.assertIn(p.label, log_text)
        self.assertIn('in=1200', log_text)
        self.assertNotIn(REAL_NAME, log_text)

    def test_no_consent_is_not_generated(self):
        p = self.ready(consent='동의하지 않습니다')
        status, call = self.generate(p, return_value=fake_message())
        self.assertEqual(status, Status.NO_CONSENT)
        call.assert_not_called()
        self.assertIsNone(self.reload(p).report)

    def test_waiting_and_done_are_not_generated(self):
        waiting = services.issue_participant('phone')
        status, call = self.generate(waiting, return_value=fake_message())
        self.assertEqual(status, Status.WAITING)
        call.assert_not_called()

        done = self.ready()
        self.mark(done, report_status=Status.DONE, report=EXAMPLE_REPORT)
        status, call = self.generate(done, return_value=fake_message())
        self.assertEqual(status, Status.DONE)
        call.assert_not_called()

    def test_refusal_is_failed_with_category(self):
        p = self.ready()
        message = fake_message(content=[], stop_reason='refusal',
                               stop_details=SimpleNamespace(type='refusal', category='cyber', explanation='x'))
        status, _ = self.generate(p, return_value=message)
        self.assertEqual(status, Status.FAILED)
        fresh = self.reload(p)
        self.assertEqual(fresh.report_error, 'AI 거절(cyber)')
        self.assertIsNone(fresh.report)
        self.assertIsNone(fresh.report_done_at)

    def test_refusal_without_details(self):
        p = self.ready()
        status, _ = self.generate(p, return_value=fake_message(stop_reason='refusal', stop_details=None))
        self.assertEqual(status, Status.FAILED)
        self.assertEqual(self.reload(p).report_error, 'AI 거절(unknown)')

    def test_max_tokens_is_failed(self):
        p = self.ready()
        truncated = fake_message(content=[text_block('{"headline": {"one_liner": "잘')], stop_reason='max_tokens')
        status, _ = self.generate(p, return_value=truncated)
        self.assertEqual(status, Status.FAILED)
        self.assertIn('최대 토큰', self.reload(p).report_error)

    def test_invalid_json_is_failed(self):
        p = self.ready()
        status, _ = self.generate(p, return_value=fake_message(content=[text_block('리포트입니다 {')]))
        self.assertEqual(status, Status.FAILED)
        self.assertIn('JSON', self.reload(p).report_error)

    def test_schema_mismatch_is_failed(self):
        p = self.ready()
        broken = dict(EXAMPLE_REPORT)
        del broken['tips']
        status, _ = self.generate(p, return_value=fake_message(content=[text_block(json.dumps(broken))]))
        self.assertEqual(status, Status.FAILED)
        self.assertEqual(self.reload(p).report_error, 'AI 출력 형식 오류: tips 누락')

    def test_no_text_block_is_failed(self):
        p = self.ready()
        status, _ = self.generate(p, return_value=fake_message(content=[SimpleNamespace(type='thinking')]))
        self.assertEqual(status, Status.FAILED)
        self.assertIn('텍스트', self.reload(p).report_error)

    def test_exception_message_is_not_stored(self):
        p = self.ready()
        secret = '%s 님의 설문 응답: 30분 이상 %s' % (REAL_NAME, p.token)
        with self.assertLogs('booth.report', 'WARNING') as logs:
            status, _ = self.generate(p, side_effect=RuntimeError(secret))
        self.assertEqual(status, Status.FAILED)
        error = self.reload(p).report_error
        self.assertEqual(error, '리포트 생성 오류(RuntimeError)')
        self.assertLessEqual(len(error), conf.REPORT_ERROR_MAX_LEN)
        # 스택은 로그에 남지만 예외 메시지(개인정보 포함 가능)는 report_error 에 들어가지 않는다.
        self.assertNotIn(REAL_NAME, error)

    def test_api_errors_are_described_without_body(self):
        request = httpx.Request('POST', 'http://booth-test.invalid/v1/messages')
        cases = [
            (anthropic.InternalServerError('%s overloaded' % REAL_NAME,
                                           response=httpx.Response(529, request=request), body=None),
             'AI API 오류(HTTP 529)'),
            (anthropic.APITimeoutError(request=request), 'AI 응답 시간 초과'),
            (anthropic.APIConnectionError(message=REAL_NAME, request=request), 'AI 서버 연결 실패'),
            (httpx.ReadTimeout(REAL_NAME), 'AI 응답 수신 시간 초과'),
            (httpx.RemoteProtocolError(REAL_NAME), 'AI 응답 수신 오류(RemoteProtocolError)'),
            (report.ReportDeadlineExceeded(150), '리포트 생성 시간(150초)을 넘겨 중단했습니다.'),
        ]
        for exc, expected in cases:
            self.assertEqual(report.describe_exception(exc), expected)

    def test_failed_then_retry_succeeds(self):
        p = self.ready()
        status, _ = self.generate(p, side_effect=anthropic.APITimeoutError(
            request=httpx.Request('POST', 'http://booth-test.invalid')))
        self.assertEqual(status, Status.FAILED)
        status, _ = self.generate(p, return_value=fake_message())
        self.assertEqual(status, Status.DONE)
        fresh = self.reload(p)
        self.assertEqual(fresh.report_attempts, 2)
        self.assertEqual(fresh.report_error, '')

    def test_result_discarded_when_data_replaced_during_generation(self):
        p = self.ready()

        def replace_measurement(system, payload):
            other = Participant.objects.using('booth').get(pk=p.pk)
            services.apply_measurement(
                other, services.validate_measurement(measurement_payload(before=side(heart_rate=90))),
                overwrite=True)
            return fake_message()

        status, _ = self.generate(p, side_effect=replace_measurement)
        self.assertEqual(status, Status.PENDING)
        fresh = self.reload(p)
        self.assertIsNone(fresh.report)
        self.assertEqual(fresh.report_attempts, 0)

    def test_result_discarded_when_other_worker_reclaimed(self):
        p = self.ready()

        def reclaimed(system, payload):
            # 이 워커가 멈춘 것으로 판정되어 다른 워커가 다시 claim 한 상황
            self.mark(p, report_started_at=timezone.now() - timedelta(seconds=conf.STALE_GENERATING_SEC + 1))
            self.assertTrue(report.claim_for_generation(p.pk))
            return fake_message()

        status, _ = self.generate(p, side_effect=reclaimed)
        self.assertEqual(status, Status.GENERATING)          # 다른 워커의 generating 을 덮어쓰지 않는다
        self.assertIsNone(self.reload(p).report)

    def test_fallback_blocks_are_handled(self):
        full = json.dumps(EXAMPLE_REPORT, ensure_ascii=False)
        fallback = SimpleNamespace(type='fallback')
        # 부분 출력이 버려지고 대체 모델이 처음부터 쓴 경우
        report_data, error = report.interpret_message(fake_message(
            content=[text_block(full[:40]), fallback, text_block(full)]))
        self.assertEqual(error, '')
        self.assertEqual(report_data, validate_report(EXAMPLE_REPORT))
        # 대체 모델이 부분 출력에 이어서 쓴 경우
        report_data, error = report.interpret_message(fake_message(
            content=[text_block(full[:40]), fallback, text_block(full[40:])]))
        self.assertEqual(error, '')
        self.assertEqual(report_data, validate_report(EXAMPLE_REPORT))


# ---------------------------------------------------------------------------
# call_claude (클라이언트 mock)
# ---------------------------------------------------------------------------
class CallClaudeTests(CleanEnvMixin, SimpleTestCase):

    def test_client_settings_and_stream_call(self):
        stream = FakeStream(events=[SimpleNamespace(type='message_start'), SimpleNamespace(type='message_stop')],
                            message=fake_message())
        with mock.patch('booth.report.anthropic.Anthropic', return_value=fake_client(stream)) as ctor:
            message = report.call_claude('SYS', {'survey_answers': {}})
        self.assertIs(message, stream.message)
        self.assertTrue(stream.exited)
        kwargs = ctor.call_args.kwargs
        self.assertEqual(kwargs['api_key'], os.environ['ANTHROPIC_API_KEY'])
        self.assertEqual(kwargs['max_retries'], 0)        # 재시도는 마감 안에서 report._open_stream 이 한다
        self.assertIsInstance(kwargs['timeout'], httpx.Timeout)
        stream_kwargs = ctor.return_value.beta.messages.stream.call_args.kwargs
        self.assertEqual(stream_kwargs, report.build_request('SYS', {'survey_answers': {}}))

    def test_missing_api_key(self):
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': ''}), \
                mock.patch('booth.report.anthropic.Anthropic') as ctor:
            with self.assertRaises(report.ReportGenerationError) as ctx:
                report.call_claude('SYS', {})
        ctor.assert_not_called()
        self.assertIn('ANTHROPIC_API_KEY', str(ctx.exception))

    def test_deadline_checked_between_events(self):
        events = [SimpleNamespace(type='message_start'), SimpleNamespace(type='content_block_delta'),
                  SimpleNamespace(type='message_stop')]
        stream = FakeStream(events=events, message=fake_message())
        # 시작 0 -> 마감 DEADLINE. 타이머 계산 0, 첫 이벤트 10, 둘째 이벤트 DEADLINE+1(마감 초과)
        with mock.patch('booth.report.anthropic.Anthropic', return_value=fake_client(stream)), \
                mock.patch('booth.report.monotonic', Clock(0, 0, 10, DEADLINE + 1)):
            with self.assertRaises(report.ReportDeadlineExceeded):
                report.call_claude('SYS', {})
        self.assertTrue(stream.exited)

    def test_late_message_stop_is_kept(self):
        stream = FakeStream(events=[SimpleNamespace(type='message_start'), SimpleNamespace(type='message_stop')],
                            message=fake_message())
        with mock.patch('booth.report.anthropic.Anthropic', return_value=fake_client(stream)), \
                mock.patch('booth.report.monotonic', Clock(0, 0, 10, DEADLINE + 1)):
            self.assertIs(report.call_claude('SYS', {}), stream.message)

    def test_watchdog_closes_silent_stream(self):
        stream = FakeStream(block_until_closed=True)
        # 타이머 계산 시점에 이미 마감 -> 즉시 스트림을 닫는다
        with mock.patch('booth.report.anthropic.Anthropic', return_value=fake_client(stream)), \
                mock.patch('booth.report.monotonic', Clock(0, DEADLINE)):
            with self.assertRaises(report.ReportDeadlineExceeded):
                report.call_claude('SYS', {})
        self.assertTrue(stream.closed.is_set())

    def test_watchdog_quiet_end_is_deadline(self):
        # 닫힌 스트림이 예외 없이 끝나 미완성 메시지(stop_reason 없음)가 남는 경우
        stream = FakeStream(block_until_closed=True, end_quietly=True, message=fake_message(stop_reason=None))
        with mock.patch('booth.report.anthropic.Anthropic', return_value=fake_client(stream)), \
                mock.patch('booth.report.monotonic', Clock(0, DEADLINE)):
            with self.assertRaises(report.ReportDeadlineExceeded):
                report.call_claude('SYS', {})

    def test_other_stream_errors_propagate(self):
        class Broken(FakeStream):
            def __iter__(self):
                raise httpx.RemoteProtocolError('boom')
                yield  # pragma: no cover
        with mock.patch('booth.report.anthropic.Anthropic', return_value=fake_client(Broken())):
            with self.assertRaises(httpx.RemoteProtocolError):
                report.call_claude('SYS', {})


def sse(events):
    return ''.join('event: %s\ndata: %s\n\n' % (e['type'], json.dumps(e, ensure_ascii=False))
                   for e in events).encode('utf-8')


class SdkWireTests(ReportDbTestCase):
    """설치된 SDK 가 실제로 보내는 요청 본문·헤더를 확인한다(MockTransport, 네트워크 없음)."""

    def make_transport(self, captured, stop_reason='end_turn', text=None):
        text = json.dumps(EXAMPLE_REPORT, ensure_ascii=False) if text is None else text
        events = [
            {'type': 'message_start', 'message': {
                'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'model': 'claude-opus-5',
                'content': [], 'stop_reason': None, 'stop_sequence': None,
                'usage': {'input_tokens': 10, 'output_tokens': 0}}},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text[:30]}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text[30:]}},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None},
             'usage': {'output_tokens': 321}},
            {'type': 'message_stop'},
        ]

        def handler(request):
            captured['url'] = str(request.url)
            captured['headers'] = dict(request.headers)
            captured['body'] = json.loads(request.content.decode('utf-8'))
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=sse(events))

        return httpx.MockTransport(handler)

    def client_factory(self, transport):
        def factory(api_key, deadline_sec):
            return anthropic.Anthropic(api_key=api_key, base_url='http://booth-test.invalid', max_retries=0,
                                       http_client=httpx.Client(transport=transport))
        return factory

    def test_end_to_end_request_body(self):
        p = self.ready()
        captured = {}
        with mock.patch('booth.report._make_client', self.client_factory(self.make_transport(captured))):
            status = report.generate_report(p)
        self.assertEqual(status, Status.DONE)
        self.assertEqual(self.reload(p).report, validate_report(EXAMPLE_REPORT))

        body = captured['body']
        self.assertEqual(body['model'], conf.report_model())
        self.assertEqual(body['max_tokens'], conf.report_max_tokens())
        self.assertTrue(body['stream'])
        self.assertEqual(body['fallbacks'], 'default')
        self.assertEqual(body['thinking'], {'type': 'adaptive'})
        self.assertEqual(body['output_config']['effort'], 'medium')
        self.assertEqual(body['output_config']['format'], {'type': 'json_schema', 'schema': REPORT_SCHEMA})
        self.assertIn('server-side-fallback-2026-07-01', captured['headers'].get('anthropic-beta', ''))
        raw = json.dumps(body, ensure_ascii=False)
        self.assertNotIn(REAL_NAME, raw)
        self.assertNotIn(p.token, raw)
        self.assertNotIn(p.label, raw)
        # 실제 전송 본문에 체험 전 값은 있고(pre_measurement), 체험 후·화면 표시값 객체는 없다.
        self.assertIn('"heart_rate_bpm": 72', body['messages'][0]['content'])
        self.assertNotIn('"after":', body['messages'][0]['content'])
        self.assertNotIn('"displayed":', body['messages'][0]['content'])

    def test_wire_refusal(self):
        p = self.ready()
        captured = {}
        transport = self.make_transport(captured, stop_reason='refusal', text='{"headline":')
        with mock.patch('booth.report._make_client', self.client_factory(transport)):
            status = report.generate_report(p)
        self.assertEqual(status, Status.FAILED)
        self.assertEqual(self.reload(p).report_error, 'AI 거절(unknown)')

    def test_wire_watchdog_closes_real_sdk_stream(self):
        # message_start 만 보내고 멈춘 서버(내용 없는 thinking 구간)를 실제 SDK 스트림으로 흉내 낸다.
        first_chunk = sse([{'type': 'message_start', 'message': {
            'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'model': 'claude-opus-5',
            'content': [], 'stop_reason': None, 'stop_sequence': None,
            'usage': {'input_tokens': 10, 'output_tokens': 0}}}])

        class SilentBody(httpx.SyncByteStream):
            def __init__(self):
                self.closed = threading.Event()

            def __iter__(self):
                yield first_chunk
                self.closed.wait(5)          # 닫힐 때까지 대기(상한 5초)

            def close(self):
                self.closed.set()

        body = SilentBody()

        def handler(request):
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=body)

        with mock.patch('booth.report._make_client', self.client_factory(httpx.MockTransport(handler))), \
                mock.patch('booth.report.monotonic', Clock(0, DEADLINE)):
            with self.assertRaises(report.ReportDeadlineExceeded):
                report.call_claude('SYS', {})
        self.assertTrue(body.closed.is_set())

    def test_real_client_factory_never_reaches_network(self):
        # 안전망 확인: 테스트 설정은 base_url 을 닫힌 로컬 포트로 돌린다.
        self.assertEqual(os.environ.get('ANTHROPIC_BASE_URL'), 'http://127.0.0.1:9')
        client = report._make_client('sk-ant-test', 150)
        self.assertEqual(str(client.base_url).rstrip('/'), 'http://127.0.0.1:9')
        self.assertEqual(client.max_retries, 0)


# ---------------------------------------------------------------------------
# 관리 명령
# ---------------------------------------------------------------------------
class GeneratePendingCommandTests(ReportDbTestCase):

    def run_command(self, *args, **mock_kwargs):
        out = io.StringIO()
        with mock.patch('booth.report.call_claude', **mock_kwargs) as call:
            call_command('booth_generate_pending', *args, stdout=out)
        return out.getvalue(), call

    def test_generates_pending_and_stale(self):
        pending = self.ready()
        stale = self.ready()
        self.mark(stale, report_status=Status.GENERATING, report_attempts=1,
                  report_started_at=timezone.now() - timedelta(seconds=conf.STALE_GENERATING_SEC + 10))
        busy = self.ready()
        self.mark(busy, report_status=Status.GENERATING, report_attempts=1, report_started_at=timezone.now())
        done = self.ready()
        self.mark(done, report_status=Status.DONE, report=EXAMPLE_REPORT)
        no_consent = self.ready(consent='동의하지 않습니다')
        failed = self.ready()
        self.mark(failed, report_status=Status.FAILED, report_attempts=1)

        output, call = self.run_command(return_value=fake_message())
        self.assertEqual(call.call_count, 2)
        self.assertEqual(self.reload(pending).report_status, Status.DONE)
        self.assertEqual(self.reload(stale).report_status, Status.DONE)
        self.assertEqual(self.reload(busy).report_status, Status.GENERATING)
        self.assertEqual(self.reload(done).report_status, Status.DONE)
        self.assertEqual(self.reload(no_consent).report_status, Status.NO_CONSENT)
        self.assertEqual(self.reload(failed).report_status, Status.FAILED)
        self.assertIn('%s -> done' % pending.label, output)
        self.assertIn('생성 대상 2명: 완료 2, 실패 0, 기타 0', output)
        self.assertNotIn(REAL_NAME, output)

    def test_include_failed_and_limit(self):
        first = self.ready()
        self.mark(first, report_status=Status.FAILED, report_attempts=2)
        capped = self.ready()
        self.mark(capped, report_status=Status.FAILED, report_attempts=conf.MAX_REPORT_ATTEMPTS)
        second = self.ready()

        output, call = self.run_command('--include-failed', '--limit', '1', return_value=fake_message())
        self.assertEqual(call.call_count, 1)
        self.assertEqual(self.reload(first).report_status, Status.DONE)     # 번호 순서
        self.assertEqual(self.reload(second).report_status, Status.PENDING)
        self.assertEqual(self.reload(capped).report_status, Status.FAILED)

    def test_failures_are_counted(self):
        self.ready()
        output, _ = self.run_command(side_effect=RuntimeError('x'))
        self.assertIn('완료 0, 실패 1', output)

    def test_dry_run(self):
        self.ready()
        self.ready()
        output, call = self.run_command('--dry-run', return_value=fake_message())
        call.assert_not_called()
        self.assertIn('생성 대상 2명', output)
