# -*- coding:utf-8 -*-
"""booth 기기용 API 테스트: X-Booth-Key 인증, 설문 수신, 대기 목록, 측정 수신, 상태 조회, 측정 취소.

반드시 --settings=backend.settings_booth_test 로 실행한다(모든 DB 가 로컬 SQLite).
API 는 Claude 를 부르지 않는다. 리포트 필드가 그대로인지만 확인한다.
"""
import json
import os
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.db import OperationalError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from booth import permissions, services
from booth.models import Participant
from booth.report_schema import EXAMPLE_REPORT
from booth.tests.test_services import CleanEnvMixin, booth_env, measurement_payload, side, survey

Status = Participant.ReportStatus

KEY = 'booth-test-key-0123456789'


def with_code(participant, **kwargs):
    """구글 폼이 미리 채운 확인 코드를 그대로 제출한 설문 응답. 이미 설문이 있으면 이것만 덮어쓸 수 있다."""
    answers = survey(**kwargs)
    answers['확인 코드'] = services.survey_code(participant)
    return answers


class ApiTestCase(CleanEnvMixin, TestCase):
    databases = {'default', 'booth'}

    def setUp(self):
        super().setUp()                                   # BOOTH_* env 비움(.env 영향 차단)
        env_patcher = mock.patch.dict(os.environ, {'BOOTH_API_KEY': KEY})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        sleep_patcher = mock.patch('booth.services.time.sleep')
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    # --- 요청 도우미 ---
    def post_json(self, name, body, key=KEY, raw=None, content_type='application/json'):
        extra = {} if key is None else {'HTTP_X_BOOTH_KEY': key}
        data = raw if raw is not None else json.dumps(body)
        return self.client.post(reverse(name), data=data, content_type=content_type, **extra)

    def get_api(self, url, key=KEY, **extra):
        if key is not None:
            extra['HTTP_X_BOOTH_KEY'] = key
        return self.client.get(url, **extra)

    def delete_api(self, url, key=KEY):
        extra = {} if key is None else {'HTTP_X_BOOTH_KEY': key}
        return self.client.delete(url, **extra)

    def detail_url(self, number):
        return reverse('booth:api_participant_detail', kwargs={'number': number})

    def measurement_url(self, number):
        return reverse('booth:api_participant_measurement', kwargs={'number': number})

    # --- 데이터 도우미 ---
    def make(self, number=None, source='phone'):
        if number is None:
            return services.issue_participant(source)
        return Participant.objects.using('booth').create(number=number, source=source)

    def reload(self, participant):
        return Participant.objects.using('booth').get(pk=participant.pk)

    def set_fields(self, participant, **fields):
        Participant.objects.using('booth').filter(pk=participant.pk).update(**fields)
        return self.reload(participant)

    def assertError(self, response, status_code):
        self.assertEqual(response.status_code, status_code, response.content)
        self.assertTrue(response['Content-Type'].startswith('application/json'), response['Content-Type'])
        body = response.json()
        self.assertEqual(set(body), {'error'}, body)
        self.assertIsInstance(body['error'], str)
        self.assertTrue(body['error'])
        self.assertTrue(any('가' <= ch <= '힣' for ch in body['error']), body['error'])   # 한국어 메시지
        return body['error']


# ---------------------------------------------------------------------------
# 인증
# ---------------------------------------------------------------------------
class AuthTests(ApiTestCase):

    def endpoint_calls(self, key):
        """(설명, 호출 함수) 목록. 모든 API 가 같은 권한 규칙을 따르는지 한 번에 확인한다."""
        p = Participant.objects.using('booth').get_or_create(number=1, defaults={'source': 'phone'})[0]
        return [
            ('survey', lambda: self.post_json('booth:api_survey', {'number': 1, 'answers': survey()}, key=key)),
            ('pending', lambda: self.get_api(reverse('booth:api_pending'), key=key)),
            ('measurement', lambda: self.post_json(
                'booth:api_measurement', {'number': 1, 'measurement': measurement_payload()}, key=key)),
            ('detail', lambda: self.get_api(self.detail_url(p.number), key=key)),
            ('delete', lambda: self.delete_api(self.measurement_url(p.number), key=key)),
        ]

    def test_missing_server_key_returns_503_for_every_endpoint(self):
        for server_value in ('', '   '):
            with mock.patch.dict(os.environ, {'BOOTH_API_KEY': server_value}):
                for sent in (None, '', KEY):
                    for label, call in self.endpoint_calls(sent):
                        with self.assertLogs('booth.permissions', 'ERROR'):
                            response = call()
                        self.assertError(response, 503)
        with mock.patch.dict(os.environ, {}):
            del os.environ['BOOTH_API_KEY']
            with self.assertLogs('booth.permissions', 'ERROR'):
                self.assertError(self.get_api(reverse('booth:api_pending')), 503)
        p = Participant.objects.using('booth').get(number=1)
        self.assertFalse(p.has_survey)
        self.assertFalse(p.has_measurement)

    def test_missing_or_wrong_header_returns_403_for_every_endpoint(self):
        for sent in (None, '', 'wrong', KEY + 'x', KEY[:-1], KEY.upper(), ' ' + KEY, '키값'):
            for label, call in self.endpoint_calls(sent):
                with self.assertLogs('booth.permissions', 'WARNING'):
                    response = call()
                message = self.assertError(response, 403)
                self.assertEqual(message, permissions.API_KEY_INVALID_MESSAGE, label)
        p = Participant.objects.using('booth').get(number=1)
        self.assertFalse(p.has_survey)                   # 거부된 요청은 아무것도 저장하지 않는다
        self.assertFalse(p.has_measurement)

    def test_correct_key_is_accepted_without_jwt(self):
        for label, call in self.endpoint_calls(KEY):
            response = call()
            self.assertEqual(response.status_code, 200, (label, response.content))

    def test_key_is_compared_in_constant_time(self):
        with mock.patch('booth.permissions.hmac.compare_digest', wraps=permissions.hmac.compare_digest) as cmp:
            self.assertEqual(self.get_api(reverse('booth:api_pending')).status_code, 200)
        cmp.assert_called_once_with(KEY.encode(), KEY.encode())

    def test_api_key_matches_helper(self):
        self.assertTrue(permissions.api_key_matches('abc', 'abc'))
        self.assertTrue(permissions.api_key_matches('키', '키'))
        for provided, expected in (('abc', 'abd'), ('', 'abc'), (None, 'abc'), ('abc', ''), (123, '123'),
                                   ('\ud800', 'abc')):
            self.assertFalse(permissions.api_key_matches(provided, expected), (provided, expected))

    def test_server_key_is_stripped(self):
        with mock.patch.dict(os.environ, {'BOOTH_API_KEY': '  %s  ' % KEY}):
            self.assertEqual(self.get_api(reverse('booth:api_pending')).status_code, 200)

    def test_permission_is_checked_before_body_parsing(self):
        with self.assertLogs('booth.permissions', 'WARNING'):
            response = self.post_json('booth:api_survey', None, key='wrong', raw='{not json')
        self.assertError(response, 403)

    def test_all_api_views_use_booth_permission_only(self):
        from booth import views_api
        for cls in (views_api.SurveyIntakeView, views_api.PendingListView, views_api.MeasurementIntakeView,
                    views_api.ParticipantDetailView, views_api.ParticipantMeasurementView):
            self.assertEqual(cls.authentication_classes, [], cls)
            self.assertEqual(cls.permission_classes, [permissions.HasBoothApiKey], cls)


class ErrorFormatTests(ApiTestCase):

    def test_wrong_method_is_json_405(self):
        self.assertError(self.get_api(reverse('booth:api_survey')), 405)
        self.assertError(self.post_json('booth:api_pending', {}), 405)
        self.assertError(self.delete_api(reverse('booth:api_measurement')), 405)

    def test_options_does_not_expose_metadata(self):
        response = self.client.options(reverse('booth:api_survey'), HTTP_X_BOOTH_KEY=KEY)
        self.assertError(response, 405)

    def test_json_even_when_browser_asks_for_html(self):
        response = self.get_api(reverse('booth:api_pending'), HTTP_ACCEPT='text/html')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('application/json'))
        response = self.get_api(self.detail_url(999), HTTP_ACCEPT='text/html')
        self.assertError(response, 404)

    def test_malformed_json_is_400(self):
        self.make(1)
        self.assertError(self.post_json('booth:api_survey', None, raw='{"number": 1,'), 400)
        self.assertError(self.post_json('booth:api_measurement', None, raw='{"number": NaN}'), 400)

    def test_non_object_body_is_400(self):
        self.make(1)
        for raw in ('[1, 2]', '"SF-001"', '42', 'null'):
            self.assertError(self.post_json('booth:api_survey', None, raw=raw), 400)
            self.assertError(self.post_json('booth:api_measurement', None, raw=raw), 400)

    def test_empty_body_is_400(self):
        self.assertError(self.post_json('booth:api_survey', None, raw=''), 400)

    def test_non_json_content_type_is_415(self):
        self.make(1)
        response = self.post_json('booth:api_survey', None, raw='number=1', content_type='text/plain')
        self.assertError(response, 415)
        response = self.post_json('booth:api_survey', None, raw='number=1',
                                  content_type='application/x-www-form-urlencoded')
        self.assertError(response, 415)

    def test_json_with_charset_is_accepted(self):
        self.make(1)
        response = self.post_json('booth:api_survey', {'number': 1, 'answers': survey()},
                                  content_type='application/json; charset=utf-8')
        self.assertEqual(response.status_code, 200, response.content)

    def test_unexpected_exception_is_json_500_without_details(self):
        self.make(1)
        with mock.patch('booth.services.apply_survey', side_effect=RuntimeError('secret internals 홍길동')):
            with self.assertLogs('booth.views_api', 'ERROR'):
                response = self.post_json('booth:api_survey', {'number': 1, 'answers': survey()})
        message = self.assertError(response, 500)
        self.assertNotIn('secret', message)
        self.assertNotIn('홍길동', response.content.decode())

    def test_db_lock_after_retries_is_503(self):
        self.make(1)
        with mock.patch('booth.services.apply_measurement',
                        side_effect=OperationalError('database is locked')):
            with self.assertLogs('booth.views_api', 'WARNING'):
                response = self.post_json('booth:api_measurement',
                                          {'number': 1, 'measurement': measurement_payload()})
        self.assertError(response, 503)


# ---------------------------------------------------------------------------
# 설문 수신
# ---------------------------------------------------------------------------
class SurveyIntakeTests(ApiTestCase):

    def test_success_response_and_stored_fields(self):
        p = self.make(42)
        response = self.post_json('booth:api_survey', {
            'number': 'SF-042', 'answers': survey(), 'submitted_at': '2026-09-14T05:00:00Z',
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {'ok': True, 'number': 42, 'label': 'SF-042'})
        self.assertNotIn('홍길동', response.content.decode())

        stored = self.reload(p)
        self.assertEqual(stored.name, '홍길동')
        self.assertTrue(stored.consent)
        self.assertEqual(stored.survey_revision, 1)
        self.assertEqual(stored.survey_received_at, datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc))
        self.assertEqual(stored.survey_answers['평소 잠들기까지 걸리는 시간'], '30분 이상')
        self.assertEqual(stored.report_status, Status.WAITING)          # 측정 전

    def test_number_formats(self):
        p = self.make(42)
        for value in (42, '42', 'SF-042', 'sf042', ' SF-42 ', 'SF 42', 42.0):
            response = self.post_json('booth:api_survey', {'number': value, 'answers': with_code(p)})
            self.assertEqual(response.status_code, 200, (value, response.content))
            self.assertEqual(response.json()['number'], 42)
        self.assertEqual(self.reload(p).survey_revision, 7)

    def test_invalid_numbers_are_400(self):
        self.make(42)
        for value in ('AB-042', 'abc', '', 0, -1, True, 42.5, [42], {'n': 42}, '12345678901'):
            response = self.post_json('booth:api_survey', {'number': value, 'answers': survey()})
            self.assertError(response, 400)
        self.assertError(self.post_json('booth:api_survey', {'answers': survey()}), 400)
        self.assertError(self.post_json('booth:api_survey', {'number': None, 'answers': survey()}), 400)
        self.assertFalse(Participant.objects.using('booth').get(number=42).has_survey)

    def test_prefix_follows_env(self):
        self.make(42)
        with booth_env(BOOTH_API_KEY=KEY, BOOTH_NUMBER_PREFIX='AB'):
            response = self.post_json('booth:api_survey', {'number': 'AB-042', 'answers': survey()})
            self.assertEqual(response.json(), {'ok': True, 'number': 42, 'label': 'AB-042'})
            self.assertError(self.post_json('booth:api_survey', {'number': 'SF-042', 'answers': survey()}), 400)

    def test_invalid_answers_are_400(self):
        p = self.make(1)
        for answers in (None, 'text', ['a'], 3):
            self.assertError(self.post_json('booth:api_survey', {'number': 1, 'answers': answers}), 400)
        self.assertError(self.post_json('booth:api_survey', {'number': 1}), 400)
        self.assertFalse(self.reload(p).has_survey)

    def test_unknown_or_expired_number_is_404(self):
        self.assertError(self.post_json('booth:api_survey', {'number': 'SF-099', 'answers': survey()}), 404)
        p = self.make(5)
        self.set_fields(p, created_at=timezone.now() - timedelta(days=14, minutes=1))
        self.assertError(self.post_json('booth:api_survey', {'number': 5, 'answers': survey()}), 404)
        self.assertFalse(self.reload(p).has_survey)

    def test_row_deleted_during_write_is_404(self):
        self.make(1)
        with mock.patch('booth.services.apply_survey', side_effect=Participant.DoesNotExist):
            response = self.post_json('booth:api_survey', {'number': 1, 'answers': survey()})
        self.assertError(response, 404)

    def test_consent_extraction(self):
        cases = (
            ('동의합니다', True),
            (['동의함'], True),
            ('동의하지 않습니다', False),
            ('미동의', False),
            (['거부'], False),
        )
        for index, (answer, expected) in enumerate(cases, start=1):
            p = self.make(index)
            response = self.post_json('booth:api_survey', {'number': index, 'answers': survey(consent=answer)})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.reload(p).consent, expected, answer)

    def test_missing_consent_question_means_no_consent(self):
        p = self.make(1)
        self.post_json('booth:api_survey', {'number': 1, 'answers': {'이름': '홍길동', '질문': '답'}})
        stored = self.reload(p)
        self.assertFalse(stored.consent)
        self.assertEqual(stored.name, '')                 # 동의가 확인되지 않으면 이름·응답을 저장하지 않는다
        self.assertEqual(stored.survey_answers, {})

    def test_title_variants_and_list_name(self):
        p = self.make(1)
        answers = {' 이름 *': ['  홍  길동 '], '개인정보 수집·이용 동의 ': ['동의합니다'], '질문': ['a', 'b']}
        self.post_json('booth:api_survey', {'number': 1, 'answers': answers})
        stored = self.reload(p)
        self.assertEqual(stored.name, '홍 길동')
        self.assertTrue(stored.consent)
        self.assertEqual(stored.survey_answers['질문'], ['a', 'b'])

    def test_titles_follow_env(self):
        p = self.make(1)
        with booth_env(BOOTH_API_KEY=KEY, BOOTH_SURVEY_NAME_TITLE='성함', BOOTH_SURVEY_CONSENT_TITLE='동의'):
            self.post_json('booth:api_survey', {'number': 1, 'answers': {'성함': '이수', '동의': '동의합니다'}})
        stored = self.reload(p)
        self.assertEqual(stored.name, '이수')
        self.assertTrue(stored.consent)

    def test_resubmission_overwrites(self):
        p = self.make(1)
        self.post_json('booth:api_survey', {'number': 1, 'answers': survey(name='홍길동')})
        response = self.post_json('booth:api_survey', {'number': 1, 'answers': with_code(p, name='박지민')})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.reload(p).name, '박지민')
        self.post_json('booth:api_survey', {'number': 1, 'answers': with_code(p, name='박지민', consent='동의하지 않습니다')})
        stored = self.reload(p)
        self.assertEqual(stored.name, '')                 # 동의 철회: 이름을 지운다
        self.assertFalse(stored.consent)
        self.assertEqual(stored.survey_revision, 3)

    def test_invalid_submitted_at_uses_server_time(self):
        p = self.make(1)
        before = timezone.now()
        for value in ('garbage', 12345, None):
            response = self.post_json('booth:api_survey', {'number': 1, 'answers': with_code(p), 'submitted_at': value})
            self.assertEqual(response.status_code, 200)
            self.assertGreaterEqual(self.reload(p).survey_received_at, before)

    def test_survey_after_measurement_makes_report_pending(self):
        p = self.make(1)
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        self.post_json('booth:api_survey', {'number': 1, 'answers': survey()})
        self.assertEqual(self.reload(p).report_status, Status.PENDING)

        q = self.make(2)
        services.apply_measurement(q, services.validate_measurement(measurement_payload()))
        self.post_json('booth:api_survey', {'number': 2, 'answers': survey(consent='동의하지 않습니다')})
        self.assertEqual(self.reload(q).report_status, Status.NO_CONSENT)

    def test_resubmission_resets_done_report(self):
        p = self.make(1)
        services.apply_survey(p, survey())
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        self.set_fields(p, report_status=Status.DONE, report=EXAMPLE_REPORT, report_attempts=1)
        self.post_json('booth:api_survey', {'number': 1, 'answers': with_code(p, **{'질문': '새 답'})})
        stored = self.reload(p)
        self.assertEqual(stored.report_status, Status.PENDING)
        self.assertIsNone(stored.report)


# ---------------------------------------------------------------------------
# 대기 목록
# ---------------------------------------------------------------------------
class PendingListTests(ApiTestCase):

    def pending(self):
        response = self.get_api(reverse('booth:api_pending'))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(set(body), {'participants', 'unsurveyed_omitted'})
        return body['participants'], response.content.decode()

    def test_empty(self):
        self.assertEqual(self.pending()[0], [])

    def test_ordering_and_exclusion(self):
        now = timezone.now()
        a = self.make(1)     # 설문 없음, 가장 먼저 발급
        b = self.make(2)     # 설문 늦게
        c = self.make(3)     # 설문 먼저
        d = self.make(4)     # 설문 없음, 나중 발급
        e = self.make(5)     # 설문 + 측정 -> 제외
        f = self.make(6)     # 측정만 -> 제외
        g = self.make(7)     # 창 밖 -> 제외
        for obj, minutes in ((a, 50), (b, 40), (c, 30), (d, 20), (e, 10), (f, 5)):
            self.set_fields(obj, created_at=now - timedelta(minutes=minutes))
        self.set_fields(g, created_at=now - timedelta(hours=12, minutes=1))

        services.apply_survey(b, survey(name='박지민'), submitted_at=(now - timedelta(minutes=2)).isoformat())
        services.apply_survey(c, survey(name='홍길동'), submitted_at=(now - timedelta(minutes=8)).isoformat())
        services.apply_survey(e, survey(name='이수'))
        services.apply_measurement(e, services.validate_measurement(measurement_payload()))
        services.apply_measurement(f, services.validate_measurement(measurement_payload()))
        services.apply_survey(g, survey(name='최민수'))

        items, _ = self.pending()
        # 설문을 낸 사람은 최근 설문 순(b 2분 전, c 8분 전), 그 뒤 설문 전인 사람은 최근 발급 순(d, a)
        self.assertEqual([item['number'] for item in items], [2, 3, 4, 1])

    def test_item_shape_and_masking(self):
        p = self.make(3)
        services.apply_survey(p, survey(name='홍길동'), submitted_at='2026-09-14T05:00:00Z')
        self.set_fields(p, survey_received_at=timezone.now() - timedelta(minutes=5))
        q = self.make(4)
        services.apply_survey(q, survey(name='이수', consent='동의하지 않습니다'))
        self.make(5)

        items, text = self.pending()
        keys = {'number', 'label', 'masked_name', 'survey_received', 'survey_received_at', 'consent'}
        for item in items:
            self.assertEqual(set(item), keys)

        newest, older, third = items                         # q(방금 설문) -> p(5분 전) -> 설문 전
        self.assertEqual(older['number'], 3)
        self.assertEqual(older['label'], 'SF-003')
        self.assertEqual(older['masked_name'], '홍○동')
        self.assertTrue(older['survey_received'])
        self.assertTrue(older['consent'])
        self.assertTrue(older['survey_received_at'].endswith('+09:00'), older['survey_received_at'])
        self.assertEqual(services.parse_iso_datetime(older['survey_received_at']).replace(microsecond=0),
                         self.reload(p).survey_received_at.replace(microsecond=0))

        self.assertEqual(newest['number'], 4)
        self.assertEqual(newest['masked_name'], '')          # 동의하지 않은 사람의 이름은 저장하지 않는다
        self.assertTrue(newest['survey_received'])
        self.assertFalse(newest['consent'])                  # 동의하지 않은 사람도 측정 대상이라 목록에 있다

        self.assertEqual(third, {'number': 5, 'label': 'SF-005', 'masked_name': '',
                                 'survey_received': False, 'survey_received_at': None, 'consent': False})

        for secret in ('홍길동', '이수"', p.token, q.token, 'token', '"name"'):
            self.assertNotIn(secret, text)

    def test_window_follows_env_but_not_past_retention(self):
        p = self.make(1)
        self.set_fields(p, created_at=timezone.now() - timedelta(hours=20))
        self.assertEqual(self.pending()[0], [])
        with mock.patch.dict(os.environ, {'BOOTH_PENDING_WINDOW_HOURS': '24'}):
            self.assertEqual([i['number'] for i in self.pending()[0]], [1])
        with mock.patch.dict(os.environ, {'BOOTH_PENDING_WINDOW_HOURS': '1000', 'BOOTH_RETENTION_DAYS': '14'}):
            self.set_fields(p, created_at=timezone.now() - timedelta(days=15))
            self.assertEqual(self.pending()[0], [])

    def test_cleared_measurement_returns_to_list(self):
        p = self.make(1)
        services.apply_survey(p, survey())
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        self.assertEqual(self.pending()[0], [])
        services.clear_measurement(p)
        self.assertEqual([i['number'] for i in self.pending()[0]], [1])


# ---------------------------------------------------------------------------
# 측정 수신
# ---------------------------------------------------------------------------
class MeasurementIntakeTests(ApiTestCase):

    def send(self, number, measurement=None, **extra):
        body = {'number': number, 'measurement': measurement if measurement is not None else measurement_payload()}
        body.update(extra)
        return self.post_json('booth:api_measurement', body)

    def surveyed(self, number=1, **survey_kwargs):
        p = self.make(number)
        services.apply_survey(p, survey(**survey_kwargs))
        return p

    def test_success_response(self):
        p = self.surveyed(42)
        response = self.send('SF-042')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {
            'ok': True, 'number': 42, 'label': 'SF-042', 'masked_name': '홍○동',
            'report_status': 'pending', 'report_url': 'http://testserver/booth/p/%s/' % p.token,
        })
        self.assertNotIn('홍길동', response.content.decode())

    def test_report_url_uses_public_base_url(self):
        p = self.surveyed(1)
        with mock.patch.dict(os.environ, {'BOOTH_PUBLIC_BASE_URL': 'https://180.83.245.145/'}):
            response = self.send(1)
        self.assertEqual(response.json()['report_url'], 'https://180.83.245.145/booth/p/%s/' % p.token)

    def test_stored_measurement_is_normalized(self):
        p = self.surveyed(1)
        payload = measurement_payload()
        payload['before']['unknown'] = 1
        payload['surprise'] = 'drop'
        payload['after']['heart_rate'] = 67.6
        del payload['after']['sdnn_ms']
        self.assertEqual(self.send(1, payload).status_code, 200)
        stored = self.reload(p)
        self.assertTrue(stored.has_measurement)
        self.assertEqual(set(stored.measurement), {'measured_at', 'device_id', 'app_version',
                                                   'before', 'after', 'displayed'})
        self.assertNotIn('unknown', stored.measurement['before'])
        self.assertEqual(stored.measurement['after']['heart_rate'], 68)
        self.assertIsNone(stored.measurement['after']['sdnn_ms'])
        self.assertEqual(stored.measurement['displayed']['after']['sleep_index'], 70.0)
        self.assertEqual(stored.report_attempts, 0)               # API 는 리포트를 만들지 않는다
        self.assertIsNone(stored.report)

    def test_status_without_survey_or_consent(self):
        self.make(1)
        self.assertEqual(self.send(1).json()['report_status'], 'waiting')
        self.assertEqual(self.send(1).status_code, 409)
        self.surveyed(2, consent='동의하지 않습니다')
        body = self.send(2).json()
        self.assertEqual(body['report_status'], 'no_consent')
        self.assertEqual(body['masked_name'], '')            # 거부자의 이름은 저장하지 않는다

    def test_invalid_number_is_400_and_unknown_is_404(self):
        for value in ('AB-001', None, True, 'x'):
            self.assertError(self.send(value), 400)
        self.assertError(self.post_json('booth:api_measurement', {'measurement': measurement_payload()}), 400)
        self.assertError(self.send(77), 404)
        p = self.make(3)
        self.set_fields(p, created_at=timezone.now() - timedelta(days=15))
        self.assertError(self.send(3), 404)

    def test_invalid_payloads_are_400(self):
        p = self.surveyed(1)
        bad_measurements = [
            'text',
            [],
            measurement_payload(measured_at=None),
            measurement_payload(measured_at='yesterday'),
            measurement_payload(before=None),
            measurement_payload(after='x'),
            measurement_payload(displayed={'before': side()}),
            measurement_payload(device_id=123),
            measurement_payload(before=side(heart_rate=True)),
            measurement_payload(before=side(heart_rate='72')),
            measurement_payload(before=side(heart_rate=29)),
            measurement_payload(after=side(heart_rate=221)),
            measurement_payload(before=side(sleep_index=100.5)),
            measurement_payload(before=side(autonomic_balance=-0.1)),
            measurement_payload(after=side(stress_recovery_index=101)),
            measurement_payload(before=side(rmssd_ms=-1)),
            measurement_payload(before=side(sdnn_ms=500.1)),
            measurement_payload(before=side(rr_count=1001)),
            measurement_payload(displayed={'before': side(), 'after': side(heart_rate=500)}),
        ]
        for measurement in bad_measurements:
            response = self.post_json('booth:api_measurement', {'number': 1, 'measurement': measurement})
            self.assertError(response, 400)
        self.assertError(self.post_json('booth:api_measurement', {'number': 1}), 400)
        self.assertFalse(self.reload(p).has_measurement)

    def test_invalid_payload_message_names_the_field(self):
        self.surveyed(1)
        message = self.assertError(self.send(1, measurement_payload(before=side(heart_rate=25))), 400)
        self.assertIn('before.heart_rate', message)

    def test_null_values_are_allowed(self):
        p = self.surveyed(1)
        nulls = {key: None for key in services.SIDE_FIELDS}
        payload = measurement_payload(before=nulls, after=dict(nulls), displayed=None)
        self.assertEqual(self.send(1, payload).status_code, 200)
        self.assertIsNone(self.reload(p).measurement['displayed'])

    def test_already_measured_is_409_and_keeps_data(self):
        p = self.surveyed(1)
        self.assertEqual(self.send(1, measurement_payload(device_id='tab-1')).status_code, 200)
        message = self.assertError(self.send(1, measurement_payload(device_id='tab-2')), 409)
        self.assertIn('overwrite', message)
        self.assertError(self.send(1, measurement_payload(device_id='tab-2'), overwrite=False), 409)
        self.assertEqual(self.reload(p).measurement['device_id'], 'tab-1')

    def test_overwrite_replaces_and_resets_report(self):
        p = self.surveyed(1)
        self.send(1, measurement_payload(device_id='tab-1'))
        self.set_fields(p, report_status=Status.DONE, report=EXAMPLE_REPORT, report_attempts=2,
                        report_done_at=timezone.now())
        response = self.send(1, measurement_payload(device_id='tab-2'), overwrite=True)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['report_status'], 'pending')
        stored = self.reload(p)
        self.assertEqual(stored.measurement['device_id'], 'tab-2')
        self.assertIsNone(stored.report)
        self.assertEqual(stored.report_attempts, 0)

    def test_overwrite_must_be_boolean(self):
        p = self.surveyed(1)
        self.send(1, measurement_payload(device_id='tab-1'))
        for value in ('true', 'false', 1, 0, 'yes'):
            self.assertError(self.send(1, measurement_payload(device_id='tab-2'), overwrite=value), 400)
        self.assertEqual(self.reload(p).measurement['device_id'], 'tab-1')
        self.assertError(self.send(1, overwrite=None), 409)           # null 은 false 로 본다

    def test_invalid_payload_checked_before_lookup(self):
        # 없는 번호라도 본문이 틀리면 400 이 먼저다(태블릿 앱 버그를 먼저 드러낸다).
        self.assertError(self.send(99, measurement_payload(before=None)), 400)


# ---------------------------------------------------------------------------
# 상태 조회
# ---------------------------------------------------------------------------
class ParticipantDetailTests(ApiTestCase):

    def test_shape_and_flags(self):
        p = self.make(7)
        response = self.get_api(self.detail_url(7))
        self.assertEqual(response.json(), {
            'number': 7, 'label': 'SF-007', 'masked_name': '', 'survey_received': False,
            'measurement_received': False, 'report_status': 'waiting',
            'report_url': None,                              # 측정 직후 30분 동안만 준다
        })

        services.apply_survey(p, survey(name='홍길동'))
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        response = self.get_api(self.detail_url(7))
        body = response.json()
        self.assertEqual(body['masked_name'], '홍○동')
        self.assertTrue(body['survey_received'])
        self.assertTrue(body['measurement_received'])
        self.assertEqual(body['report_status'], 'pending')

        text = response.content.decode()
        self.assertNotIn('홍길동', text)
        self.assertNotIn('"name"', text)
        self.assertNotIn('"token"', text)
        # 토큰은 체험자가 찍어 갈 report_url 안에만 있다.
        self.assertEqual(text.count(p.token), 1)
        self.assertIn(p.token, body['report_url'])

    def test_report_status_reflects_db(self):
        p = self.make(1)
        self.set_fields(p, report_status=Status.GENERATING)
        self.assertEqual(self.get_api(self.detail_url(1)).json()['report_status'], 'generating')

    def test_unknown_expired_and_huge_numbers_are_404(self):
        self.assertError(self.get_api(self.detail_url(1)), 404)
        p = self.make(2)
        self.set_fields(p, created_at=timezone.now() - timedelta(days=15))
        self.assertError(self.get_api(self.detail_url(2)), 404)
        self.assertError(self.get_api('/booth/api/participants/99999999999/'), 404)
        self.assertError(self.get_api('/booth/api/participants/0/'), 404)


# ---------------------------------------------------------------------------
# 측정 취소
# ---------------------------------------------------------------------------
class ParticipantMeasurementDeleteTests(ApiTestCase):

    def test_delete_clears_measurement_and_report(self):
        p = self.make(1)
        services.apply_survey(p, survey())
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        self.set_fields(p, report_status=Status.DONE, report=EXAMPLE_REPORT, report_attempts=1,
                        report_done_at=timezone.now())

        response = self.delete_api(self.measurement_url(1))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {'ok': True})

        stored = self.reload(p)
        self.assertIsNone(stored.measurement)
        self.assertIsNone(stored.measurement_received_at)
        self.assertIsNone(stored.report)
        self.assertIsNone(stored.report_done_at)
        self.assertEqual(stored.report_status, Status.WAITING)
        self.assertTrue(stored.has_survey)                       # 설문은 그대로
        self.assertEqual(stored.name, '홍길동')

        # 다시 대기 목록에 나타나고, overwrite 없이 올바른 측정을 다시 보낼 수 있다.
        pending = self.get_api(reverse('booth:api_pending')).json()['participants']
        self.assertEqual([item['number'] for item in pending], [1])
        response = self.post_json('booth:api_measurement', {'number': 1, 'measurement': measurement_payload()})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['report_status'], 'pending')

    def test_delete_without_measurement_is_idempotent(self):
        p = self.make(1)
        for _ in range(2):
            response = self.delete_api(self.measurement_url(1))
            self.assertEqual(response.json(), {'ok': True})
        self.assertEqual(self.reload(p).report_status, Status.WAITING)

    def test_delete_unknown_is_404(self):
        self.assertError(self.delete_api(self.measurement_url(5)), 404)

    def test_delete_requires_delete_method(self):
        self.make(1)
        self.assertError(self.get_api(self.measurement_url(1)), 405)
