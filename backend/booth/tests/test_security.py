# -*- coding:utf-8 -*-
"""booth 전송·노출 보안 테스트.

https 강제(BOOTH_ALLOW_INSECURE 없이), DEBUG=True 에서도 디버그 페이지가 나가지 않음(미정의 경로,
끝 '/' 없는 POST, 뷰 예외, 템플릿 실패), 키오스크 전용 토큰, __Secure- 쿠키, 번호 발급 상한,
폼/태블릿 키 분리, 상태 조회 API 의 report_url 기간.

반드시 --settings=backend.settings_booth_test 로 실행한다(모든 DB 가 로컬 SQLite).
"""
import json
from datetime import timedelta
from unittest import mock

from django.db import OperationalError
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from booth import conf, services, views_pages
from booth.models import Participant
from booth.tests.test_api import KEY, ApiTestCase
from booth.tests.test_pages import FORM_ENTRY, FORM_URL, PageTestCase
from booth.tests.test_services import booth_env, measurement_payload, survey

Status = Participant.ReportStatus

# Django 기술 404·500 화면에만 나오는 문구
DEBUG_MARKERS = ('Using the URLconf', 'Traceback', 'DEBUG = True', 'DATABASES', 'Request Method')


def insecure_env(**values):
    """로컬 개발용 예외(BOOTH_ALLOW_INSECURE)를 끈 운영과 같은 환경."""
    return booth_env(BOOTH_ALLOW_INSECURE=None, **values)


# ---------------------------------------------------------------------------
# https 강제
# ---------------------------------------------------------------------------
class SecurePageTests(PageTestCase):

    def test_pages_refuse_plain_http_with_korean_403(self):
        p = self.issue()
        kiosk = self.issue('kiosk')
        urls = [
            reverse('booth:start'),
            reverse('booth:kiosk_home'),
            self.url('personal', p),
            self.url('survey_redirect', p),
            reverse('booth:kiosk_participant', kwargs={'token': kiosk.kiosk_token}),
            '/booth/nope/',
        ]
        with insecure_env(BOOTH_FORM_URL=FORM_URL), self.assertLogs('booth.views_pages', 'WARNING'):
            for url in urls:
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403, url)
                self.assertContains(response, '보안 연결(HTTPS)이 필요합니다', status_code=403)
                self.assertBoothHeaders(response)
                self.assertNotIn(conf.TOKEN_COOKIE_NAME, response.cookies)
            response = self.client.post(reverse('booth:kiosk_new'))
            self.assertEqual(response.status_code, 403)
        self.assertEqual(Participant.objects.using('booth').count(), 2)       # 새 번호가 발급되지 않았다

    def test_page_json_refuses_plain_http(self):
        p = self.ready()
        with insecure_env(), self.assertLogs('booth.views_pages', 'WARNING'), \
                mock.patch('booth.report.generate_report') as generate:
            responses = [
                self.client.get(self.url('status', p)),
                self.client.post(self.url('generate', p)),
                self.client.get(reverse('booth:kiosk_status', kwargs={'token': 'x'})),
            ]
        generate.assert_not_called()
        for response in responses:
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json(), {'error': views_pages.HTTPS_REQUIRED_MESSAGE})
            self.assertBoothHeaders(response)

    def test_https_requests_work_without_override(self):
        p = self.issue()
        with insecure_env():
            self.assertEqual(self.client.get(self.url('personal', p), secure=True).status_code, 200)
            self.assertEqual(self.client.get(reverse('booth:start'), secure=True).status_code, 302)

    def test_refusal_log_has_no_token(self):
        p = self.issue()
        with insecure_env(), self.assertLogs('booth.views_pages', 'WARNING') as logs:
            self.client.get(self.url('personal', p))
        self.assertNotIn(p.token, '\n'.join(logs.output))


class SecureApiTests(ApiTestCase):

    def test_api_refuses_plain_http_before_key_and_body(self):
        p = self.make(1)
        with insecure_env(BOOTH_API_KEY=KEY), self.assertLogs('booth', 'WARNING'), \
                mock.patch('booth.permissions.HasBoothApiKey.has_permission') as permission:
            responses = [
                self.post_json('booth:api_survey', None, raw='{broken'),
                self.get_api(reverse('booth:api_pending')),
                self.post_json('booth:api_measurement', {'number': 1, 'measurement': measurement_payload()}),
                self.get_api(self.detail_url(1)),
                self.delete_api(self.measurement_url(1)),
                self.get_api('/booth/api/nope/'),
            ]
        permission.assert_not_called()                  # 키 비교보다 먼저 거부한다
        for response in responses:
            self.assertIn('HTTPS', self.assertError(response, 403))
        self.assertFalse(self.reload(p).has_measurement)

    def test_api_works_over_https_and_report_url_is_https(self):
        p = self.make(1)
        services.apply_survey(p, survey())
        with insecure_env(BOOTH_API_KEY=KEY):
            self.assertEqual(self.client.get(reverse('booth:api_pending'), secure=True,
                                             HTTP_X_BOOTH_KEY=KEY).status_code, 200)
            response = self.client.post(
                reverse('booth:api_measurement'), secure=True, HTTP_X_BOOTH_KEY=KEY, content_type='application/json',
                data=json.dumps({'number': 1, 'measurement': measurement_payload()}))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['report_url'], 'https://testserver/booth/p/%s/' % p.token)


class ReportUrlSchemeTests(PageTestCase):

    def test_http_base_url_and_http_request_are_not_used(self):
        p = self.issue()
        path = '/booth/p/%s/' % p.token
        http_request = RequestFactory().get('/booth/api/measurement/')
        https_request = RequestFactory().get('/booth/api/measurement/', secure=True)
        with insecure_env(BOOTH_PUBLIC_BASE_URL='http://180.83.245.145:8000'):
            with self.assertLogs('booth.services', 'ERROR'):
                self.assertEqual(services.build_report_url(p, https_request), 'https://testserver' + path)
            with self.assertLogs('booth.services', 'ERROR'):
                self.assertEqual(services.build_report_url(p, http_request), '')
        with insecure_env(BOOTH_PUBLIC_BASE_URL='https://180.83.245.145/'):
            self.assertEqual(services.build_report_url(p, http_request), 'https://180.83.245.145' + path)


# ---------------------------------------------------------------------------
# DEBUG=True 에서도 디버그 페이지 없음
# ---------------------------------------------------------------------------
class NoDebugPageTests(PageTestCase):

    def assertNoDebugDetails(self, response, *secrets):
        text = response.content.decode('utf-8')
        for marker in DEBUG_MARKERS + secrets:
            self.assertNotIn(marker, text)

    @override_settings(DEBUG=True)
    def test_unknown_booth_paths_are_booth_404(self):
        for path in ('/booth/', '/booth/nope/', '/booth/p/', '/booth/kiosk/zzz/'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, path)
            self.assertContains(response, '페이지를 찾을 수 없습니다', status_code=404)
            self.assertNoDebugDetails(response, 'api/v1')
            self.assertBoothHeaders(response)
        kiosk = self.client.get('/booth/kiosk/zzz/')
        self.assertContains(kiosk, 'href="%s"' % reverse('booth:kiosk_home'), status_code=404)

        for path in ('/booth/api/nope/', '/booth/api/participants/SF-042/'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, path)
            self.assertEqual(response.json(), {'error': views_pages.NOT_FOUND_JSON_MESSAGE})
            self.assertNoDebugDetails(response, 'api/v1')

    @override_settings(DEBUG=True)
    def test_post_without_trailing_slash_is_404_not_debug_500(self):
        client = Client(enforce_csrf_checks=True)
        for path in ('/booth/kiosk/new', '/booth/api/survey', '/booth/api/measurement'):
            response = client.post(path, data='{}', content_type='application/json')
            self.assertEqual(response.status_code, 404, path)
            self.assertNoDebugDetails(response)
        self.assertEqual(Participant.objects.using('booth').count(), 0)

    def test_get_without_trailing_slash_redirects_to_booth_route(self):
        p = self.issue()
        response = self.client.get('/booth/start?new=1')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/booth/start/?new=1')
        response = self.client.get('/booth/p/%s' % p.token)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/booth/p/%s/' % p.token)
        self.assertBoothHeaders(response)
        self.assertEqual(Participant.objects.using('booth').count(), 1)       # 리다이렉트만, 발급 없음

    @override_settings(DEBUG=True)
    def test_exception_in_page_view_is_korean_500_without_details(self):
        p = self.with_survey(self.issue(), name='홍길동')
        with mock.patch('booth.services.deletion_date', side_effect=RuntimeError('홍길동 %s' % p.token)):
            with self.assertLogs('booth.views_pages', 'ERROR') as logs:
                response = self.client.get(self.url('personal', p))
        self.assertEqual(response.status_code, 500)
        self.assertContains(response, '일시적인 오류가 발생했습니다', status_code=500)
        self.assertNoDebugDetails(response, '홍길동', p.token)
        self.assertBoothHeaders(response)
        log_text = '\n'.join(logs.output)
        self.assertIn('RuntimeError', log_text)
        self.assertNotIn('홍길동', log_text)             # 예외 메시지·지역 변수는 로그에도 남기지 않는다
        self.assertNotIn(p.token, log_text)

    @override_settings(DEBUG=True)
    def test_database_error_in_json_view_is_json_500(self):
        with mock.patch('booth.services.get_participant_by_token',
                        side_effect=OperationalError('no such table: booth_participant')):
            with self.assertLogs('booth.views_pages', 'ERROR'):
                response = self.client.get(reverse('booth:status', kwargs={'token': 'abc'}))
        self.assertEqual(response.status_code, 500)
        self.assertEqual(set(response.json()), {'error'})
        self.assertNoDebugDetails(response, 'no such table')

    @override_settings(DEBUG=True)
    def test_kiosk_error_page_has_home_button(self):
        with mock.patch('booth.services.get_participant_by_kiosk_token', side_effect=RuntimeError('x')):
            with self.assertLogs('booth.views_pages', 'ERROR'):
                response = self.client.get(reverse('booth:kiosk_participant', kwargs={'token': 'abc'}))
        self.assertEqual(response.status_code, 500)
        self.assertContains(response, 'href="%s"' % reverse('booth:kiosk_home'), status_code=500)

    @override_settings(DEBUG=True)
    def test_django_request_log_does_not_record_token_paths(self):
        p = self.issue()
        with self.assertNoLogs('django.request', 'WARNING'):
            self.assertEqual(self.client.get(reverse('booth:personal', kwargs={'token': 'unknown-token'})).status_code,
                             404)
            self.assertEqual(self.client.get(self.url('generate', p)).status_code, 405)
            with mock.patch('booth.services.deletion_date', side_effect=RuntimeError('x')), \
                    self.assertLogs('booth.views_pages', 'ERROR'):
                self.assertEqual(self.client.get(self.url('personal', p)).status_code, 500)

    def test_template_failure_falls_back_to_plain_korean_html(self):
        p = self.issue()
        with mock.patch('booth.views_pages.render', side_effect=RuntimeError('template missing')):
            with self.assertLogs('booth.views_pages', 'ERROR'):
                response = self.client.get(self.url('personal', p))
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response['Content-Type'], 'text/html; charset=utf-8')
        self.assertContains(response, '일시적인 오류가 발생했습니다', status_code=500)
        self.assertNoDebugDetails(response, 'template missing')


# ---------------------------------------------------------------------------
# 키오스크 전용 토큰
# ---------------------------------------------------------------------------
class KioskTokenTests(PageTestCase):

    def kiosk_url(self, name, participant):
        return reverse('booth:%s' % name, kwargs={'token': participant.kiosk_token})

    def test_kiosk_flow_never_exposes_personal_token(self):
        with booth_env(BOOTH_FORM_URL=FORM_URL, BOOTH_FORM_NUMBER_ENTRY=FORM_ENTRY):
            response = self.client.post(reverse('booth:kiosk_new'))
            p = Participant.objects.using('booth').get()
            self.assertTrue(p.kiosk_token)
            self.assertNotEqual(p.kiosk_token, p.token)
            self.assertEqual(response['Location'], self.kiosk_url('kiosk_participant', p))
            page = self.client.get(response['Location'])
        self.assertEqual(page.status_code, 200)
        content = page.content.decode('utf-8')
        self.assertNotIn(p.token, content)
        self.assertIn('data-status-url="%s"' % self.kiosk_url('kiosk_status', p), content)

    def test_kiosk_token_does_not_open_personal_routes(self):
        p = self.with_survey(self.issue('kiosk'), name='최민호')
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        with booth_env(BOOTH_FORM_URL=FORM_URL):
            for name in ('personal', 'status', 'survey_redirect'):
                response = self.client.get(reverse('booth:%s' % name, kwargs={'token': p.kiosk_token}))
                self.assertEqual(response.status_code, 404, name)
                self.assertNotIn('최민호', response.content.decode('utf-8'))
            with mock.patch('booth.report.generate_report') as generate:
                response = self.client.post(reverse('booth:generate', kwargs={'token': p.kiosk_token}))
            self.assertEqual(response.status_code, 404)
            generate.assert_not_called()
            # 반대로 개인 페이지 토큰으로 키오스크 화면도 열리지 않는다.
            self.assertEqual(self.client.get(reverse('booth:kiosk_participant', kwargs={'token': p.token})).status_code,
                             404)

    def test_kiosk_status_returns_only_survey_flag(self):
        p = self.issue('kiosk')
        url = self.kiosk_url('kiosk_status', p)
        self.assertEqual(self.client.get(url).json(), {'survey_received': False})
        self.with_survey(p, name='홍길동')
        response = self.client.get(url)
        self.assertEqual(response.json(), {'survey_received': True})
        self.assertBoothHeaders(response)
        for secret in (p.token, '홍길동', 'SF-001'):
            self.assertNotIn(secret, response.content.decode('utf-8'))

    def test_kiosk_token_expires_after_abandon_window_or_after_survey(self):
        grace = conf.KIOSK_TOKEN_GRACE_SEC
        p = self.issue('kiosk')
        self.mark(p, created_at=timezone.now() - timedelta(seconds=conf.KIOSK_ABANDON_SEC + grace + 5))
        with booth_env(BOOTH_FORM_URL=FORM_URL):
            response = self.client.get(self.kiosk_url('kiosk_participant', p))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'href="%s"' % reverse('booth:kiosk_home'), status_code=404)

        q = self.with_survey(self.issue('kiosk'))
        self.assertEqual(self.client.get(self.kiosk_url('kiosk_status', q)).status_code, 200)
        self.mark(q, survey_received_at=timezone.now() - timedelta(seconds=conf.KIOSK_RETURN_DELAY_SEC + grace + 5))
        self.assertEqual(self.client.get(self.kiosk_url('kiosk_status', q)).status_code, 404)

    def test_phone_participants_have_no_kiosk_token(self):
        p = self.issue('phone')
        self.assertIsNone(p.kiosk_token)
        for value in (None, '', 'x' * 65, p.token):
            self.assertIsNone(services.get_participant_by_kiosk_token(value))

    def test_kiosk_new_refuses_cross_site_post(self):
        with self.assertLogs('booth.views_pages', 'WARNING'):
            response = self.client.post(reverse('booth:kiosk_new'), HTTP_SEC_FETCH_SITE='cross-site')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Participant.objects.using('booth').count(), 0)
        for site in ('same-origin', 'none'):
            self.assertEqual(self.client.post(reverse('booth:kiosk_new'), HTTP_SEC_FETCH_SITE=site).status_code, 303)


# ---------------------------------------------------------------------------
# 쿠키·발급 상한
# ---------------------------------------------------------------------------
class TokenCookieTests(PageTestCase):

    def test_cookie_uses_secure_prefix_and_flag(self):
        self.assertTrue(conf.TOKEN_COOKIE_NAME.startswith('__Secure-'))
        response = self.client.get(reverse('booth:start'))
        self.assertTrue(response.cookies[conf.TOKEN_COOKIE_NAME]['secure'])

    def test_unprefixed_cookie_is_ignored(self):
        # 평문 응답으로 심을 수 있는 접두어 없는 이름은 읽지 않는다.
        p = self.issue()
        self.client.cookies['booth_token'] = p.token
        response = self.client.get(reverse('booth:start'))
        self.assertEqual(Participant.objects.using('booth').count(), 2)
        self.assertNotEqual(response['Location'], self.url('personal', p))


class IssuanceLimitTests(PageTestCase):

    def test_hourly_issue_limit_returns_503(self):
        start_new = reverse('booth:start') + '?new=1'
        with booth_env(BOOTH_MAX_ISSUE_PER_HOUR='2'):
            for _ in range(2):
                self.assertEqual(self.client.get(start_new).status_code, 302)
            with self.assertLogs('booth.services', 'WARNING'):
                response = self.client.get(start_new)
            self.assertEqual(response.status_code, 503)
            self.assertContains(response, '발급이 많아', status_code=503)
            with self.assertLogs('booth.services', 'WARNING'):
                response = self.client.post(reverse('booth:kiosk_new'))
            self.assertEqual(response.status_code, 503)
            self.assertContains(response, 'href="%s"' % reverse('booth:kiosk_home'), status_code=503)
        self.assertEqual(Participant.objects.using('booth').count(), 2)

        # 1시간이 지난 발급은 세지 않는다.
        Participant.objects.using('booth').update(created_at=timezone.now() - timedelta(minutes=61))
        with booth_env(BOOTH_MAX_ISSUE_PER_HOUR='2'):
            self.assertEqual(self.client.get(start_new).status_code, 302)


# ---------------------------------------------------------------------------
# 키 분리·report_url 기간
# ---------------------------------------------------------------------------
class ApiKeyScopeTests(ApiTestCase):
    FORM_KEY = 'booth-form-key-9876543210'

    def test_form_key_opens_only_survey(self):
        self.make(1)
        with booth_env(BOOTH_API_KEY=KEY, BOOTH_FORM_API_KEY=self.FORM_KEY):
            response = self.post_json('booth:api_survey', {'number': 1, 'answers': survey()}, key=self.FORM_KEY)
            self.assertEqual(response.status_code, 200, response.content)
            with self.assertLogs('booth.permissions', 'WARNING'):
                self.assertError(self.post_json('booth:api_survey', {'number': 1, 'answers': survey()}, key=KEY), 403)
            form_key_calls = [
                lambda: self.get_api(reverse('booth:api_pending'), key=self.FORM_KEY),
                lambda: self.get_api(self.detail_url(1), key=self.FORM_KEY),
                lambda: self.post_json('booth:api_measurement', {'number': 1, 'measurement': measurement_payload()},
                                       key=self.FORM_KEY),
                lambda: self.delete_api(self.measurement_url(1), key=self.FORM_KEY),
            ]
            for call in form_key_calls:
                with self.assertLogs('booth.permissions', 'WARNING'):
                    self.assertError(call(), 403)
            self.assertEqual(self.get_api(reverse('booth:api_pending'), key=KEY).status_code, 200)

    def test_survey_falls_back_to_tablet_key_with_warning(self):
        self.make(1)
        with mock.patch('booth.permissions._form_key_fallback_warned', False):
            with self.assertLogs('booth.permissions', 'WARNING') as logs:
                response = self.post_json('booth:api_survey', {'number': 1, 'answers': survey()})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('BOOTH_FORM_API_KEY', '\n'.join(logs.output))


class DetailReportUrlWindowTests(ApiTestCase):

    def test_report_url_only_shortly_after_measurement(self):
        p = self.make(1)
        body = self.get_api(self.detail_url(1)).json()
        self.assertIsNone(body['report_url'])

        services.apply_survey(p, survey())
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        self.assertIn(p.token, self.get_api(self.detail_url(1)).json()['report_url'])

        self.set_fields(p, measurement_received_at=timezone.now() - timedelta(minutes=31))
        response = self.get_api(self.detail_url(1))
        self.assertIsNone(response.json()['report_url'])
        self.assertNotIn(p.token, response.content.decode('utf-8'))
