# -*- coding:utf-8 -*-
"""booth 체험자 페이지 테스트: 휴대폰 시작·쿠키, 키오스크, 설문 리다이렉트, 개인 페이지 상태,
리포트 렌더링(비교표 캡션·이스케이프·푸터), 상태/생성 JSON, 보안 헤더.

반드시 --settings=backend.settings_booth_test 로 실행한다(모든 DB 가 로컬 SQLite).
리포트 생성(booth.report.generate_report)은 항상 mock 한다. Claude 호출은 일어나지 않는다.
"""
import copy
import html
import re
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.db import OperationalError
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from booth import conf, services, views_pages
from booth.models import Participant
from booth.report_schema import EXAMPLE_REPORT
from booth.tests.test_services import CleanEnvMixin, booth_env, measurement_payload, survey

Status = Participant.ReportStatus

FORM_URL = 'https://docs.google.com/forms/d/e/FORM_ID/viewform'
FORM_ENTRY = 'entry.1234567'


class PageTestCase(CleanEnvMixin, TestCase):
    databases = {'default', 'booth'}

    def setUp(self):
        super().setUp()
        sleep_patcher = mock.patch('booth.services.time.sleep')
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    # --- 참가자 준비 ---
    def issue(self, source='phone'):
        return services.issue_participant(source)

    def with_survey(self, participant, **kwargs):
        services.apply_survey(participant, survey(**kwargs))
        return participant

    def ready(self, name='홍길동', consent='동의합니다', displayed=True):
        p = self.with_survey(self.issue(), name=name, consent=consent)
        payload = measurement_payload() if displayed else measurement_payload(displayed=None)
        services.apply_measurement(p, services.validate_measurement(payload))
        return p

    def mark(self, participant, **fields):
        Participant.objects.using('booth').filter(pk=participant.pk).update(**fields)
        return Participant.objects.using('booth').get(pk=participant.pk)

    def done(self, report=None, **kwargs):
        p = self.ready(**kwargs)
        return self.mark(p, report_status=Status.DONE, report=report or EXAMPLE_REPORT,
                         report_attempts=1, report_done_at=timezone.now())

    # --- URL·검증 ---
    def url(self, name, participant):
        return reverse('booth:%s' % name, kwargs={'token': participant.token})

    def kiosk_url(self, name, participant):
        return reverse('booth:%s' % name, kwargs={'token': participant.kiosk_token})

    def assertBoothHeaders(self, response):
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertEqual(response['X-Robots-Tag'], 'noindex')

    def html_text(self, response):
        return response.content.decode('utf-8')


# ---------------------------------------------------------------------------
# 휴대폰 시작 (쿠키)
# ---------------------------------------------------------------------------
class StartTests(PageTestCase):

    def test_issues_number_sets_cookie_and_redirects(self):
        response = self.client.get(reverse('booth:start'))
        p = Participant.objects.using('booth').get()
        self.assertEqual(p.source, 'phone')
        self.assertRedirects(response, self.url('personal', p), fetch_redirect_response=False)
        self.assertBoothHeaders(response)

        morsel = response.cookies[conf.TOKEN_COOKIE_NAME]
        self.assertEqual(morsel.value, p.token)
        self.assertTrue(morsel['httponly'])
        self.assertEqual(morsel['samesite'], 'Lax')
        self.assertEqual(int(morsel['max-age']), 43200)
        self.assertEqual(morsel['path'], '/booth/')
        self.assertTrue(morsel['secure'])                 # __Secure- 쿠키라 항상 Secure

    def test_secure_cookie_on_https(self):
        response = self.client.get(reverse('booth:start'), secure=True)
        self.assertTrue(response.cookies[conf.TOKEN_COOKIE_NAME]['secure'])

    def test_cookie_is_reused(self):
        first = self.client.get(reverse('booth:start'))
        second = self.client.get(reverse('booth:start'))      # 테스트 클라이언트가 쿠키를 다시 보낸다
        self.assertEqual(Participant.objects.using('booth').count(), 1)
        self.assertEqual(second['Location'], first['Location'])
        self.assertNotIn(conf.TOKEN_COOKIE_NAME, second.cookies)   # 재사용 때는 만료를 늘리지 않는다

    def test_new_param_issues_new_number_and_replaces_cookie(self):
        self.client.get(reverse('booth:start'))
        response = self.client.get(reverse('booth:start') + '?new=1')
        self.assertEqual(Participant.objects.using('booth').count(), 2)
        newest = Participant.objects.using('booth').order_by('-number').first()
        self.assertEqual(newest.number, 2)
        self.assertRedirects(response, self.url('personal', newest), fetch_redirect_response=False)
        self.assertEqual(response.cookies[conf.TOKEN_COOKIE_NAME].value, newest.token)
        # 이후 재방문은 새 번호로
        again = self.client.get(reverse('booth:start'))
        self.assertEqual(again['Location'], self.url('personal', newest))

    def test_cookie_older_than_12_hours_is_not_reused(self):
        self.client.get(reverse('booth:start'))
        old = Participant.objects.using('booth').get()
        self.mark(old, created_at=timezone.now() - timedelta(hours=12, minutes=1))
        response = self.client.get(reverse('booth:start'))
        self.assertEqual(Participant.objects.using('booth').count(), 2)
        self.assertNotEqual(response.cookies[conf.TOKEN_COOKIE_NAME].value, old.token)

    def test_unknown_cookie_token_issues_new(self):
        self.client.cookies[conf.TOKEN_COOKIE_NAME] = 'not-a-real-token'
        response = self.client.get(reverse('booth:start'))
        p = Participant.objects.using('booth').get()
        self.assertEqual(response.cookies[conf.TOKEN_COOKIE_NAME].value, p.token)

    def test_issue_failure_shows_korean_503(self):
        with mock.patch('booth.services.issue_participant', side_effect=OperationalError('database is locked')):
            with self.assertLogs('booth.views_pages', 'ERROR'):
                response = self.client.get(reverse('booth:start'))
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, '다시 시도', status_code=503)
        self.assertNotIn(conf.TOKEN_COOKIE_NAME, response.cookies)
        self.assertBoothHeaders(response)

    def test_post_not_allowed(self):
        response = self.client.post(reverse('booth:start'))
        self.assertEqual(response.status_code, 405)
        self.assertEqual(Participant.objects.using('booth').count(), 0)


# ---------------------------------------------------------------------------
# 공용 키오스크
# ---------------------------------------------------------------------------
class KioskTests(PageTestCase):

    def iframe_src(self, response):
        match = re.search(r'<iframe[^>]*\ssrc="([^"]+)"', self.html_text(response))
        self.assertIsNotNone(match, 'iframe 이 없습니다')
        return html.unescape(match.group(1))

    def test_home_has_start_button_and_no_cookies(self):
        response = self.client.get(reverse('booth:kiosk_home'))
        self.assertContains(response, '새 체험자 시작')
        self.assertContains(response, 'action="%s"' % reverse('booth:kiosk_new'))
        self.assertEqual(len(response.cookies), 0)
        self.assertBoothHeaders(response)

    def test_new_issues_a_new_participant_every_time_and_ignores_cookie(self):
        phone = self.issue('phone')
        self.client.cookies[conf.TOKEN_COOKIE_NAME] = phone.token     # 누군가 공용 태블릿에서 QR 을 썼던 흔적

        tokens = []
        for expected_number in (2, 3):
            response = self.client.post(reverse('booth:kiosk_new'))
            p = Participant.objects.using('booth').get(number=expected_number)
            self.assertEqual(p.source, 'kiosk')
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response['Location'], self.kiosk_url('kiosk_participant', p))
            self.assertNotIn(conf.TOKEN_COOKIE_NAME, response.cookies)
            self.assertBoothHeaders(response)
            tokens.append(p.token)
        self.assertNotIn(phone.token, tokens)
        self.assertEqual(self.client.cookies[conf.TOKEN_COOKIE_NAME].value, phone.token)   # 건드리지 않았다

    def test_new_is_csrf_exempt(self):
        response = Client(enforce_csrf_checks=True).post(reverse('booth:kiosk_new'))
        self.assertEqual(response.status_code, 303)

    def test_new_get_redirects_home_without_issuing(self):
        response = self.client.get(reverse('booth:kiosk_new'))
        self.assertRedirects(response, reverse('booth:kiosk_home'), fetch_redirect_response=False)
        self.assertEqual(Participant.objects.using('booth').count(), 0)

    def test_new_issue_failure_shows_503_with_home_button(self):
        with mock.patch('booth.services.issue_participant', side_effect=OperationalError('database is locked')):
            with self.assertLogs('booth.views_pages', 'ERROR'):
                response = self.client.post(reverse('booth:kiosk_new'))
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, 'href="%s"' % reverse('booth:kiosk_home'), status_code=503)

    def test_participant_page_embeds_prefilled_form(self):
        p = self.issue('kiosk')
        with booth_env(BOOTH_FORM_URL=FORM_URL, BOOTH_FORM_NUMBER_ENTRY=FORM_ENTRY):
            response = self.client.get(self.kiosk_url('kiosk_participant', p))
        self.assertEqual(response.status_code, 200)
        src = urlsplit(self.iframe_src(response))
        self.assertEqual('%s://%s%s' % (src.scheme, src.netloc, src.path), FORM_URL)
        self.assertEqual(parse_qs(src.query), {'usp': ['pp_url'], FORM_ENTRY: ['SF-001'], 'embedded': ['true']})
        content = self.html_text(response)
        self.assertIn('referrerpolicy="no-referrer"', content)
        self.assertIn('<meta name="referrer" content="no-referrer">', content)
        self.assertIn('처음으로', content)
        self.assertIn('data-status-url="%s"' % self.kiosk_url('kiosk_status', p), content)
        self.assertIn('data-home-url="%s"' % reverse('booth:kiosk_home'), content)
        # 공용 화면에는 개인 페이지 토큰이 어디에도 없다(링크·상태 URL 모두 키오스크 토큰).
        self.assertNotIn(p.token, content)
        self.assertEqual(len(response.cookies), 0)
        self.assertBoothHeaders(response)

    def test_participant_page_after_survey_hides_form_and_never_shows_name(self):
        p = self.with_survey(self.issue('kiosk'), name='홍길동')
        with booth_env(BOOTH_FORM_URL=FORM_URL, BOOTH_FORM_NUMBER_ENTRY=FORM_ENTRY):
            response = self.client.get(self.kiosk_url('kiosk_participant', p))
        content = self.html_text(response)
        self.assertNotIn('<iframe', content)
        self.assertIn('설문이 접수되었습니다', content)
        self.assertIn('data-received="1"', content)
        self.assertNotIn('홍길동', content)
        self.assertNotIn('홍○동', content)

    def test_participant_page_without_form_url_is_503(self):
        p = self.issue('kiosk')
        response = self.client.get(self.kiosk_url('kiosk_participant', p))
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, '설문 주소', status_code=503)
        self.assertBoothHeaders(response)

    def test_participant_unknown_token_is_404(self):
        response = self.client.get(reverse('booth:kiosk_participant', kwargs={'token': 'nope'}))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, '페이지를 찾을 수 없습니다', status_code=404)
        self.assertContains(response, 'href="%s"' % reverse('booth:kiosk_home'), status_code=404)
        self.assertBoothHeaders(response)


# ---------------------------------------------------------------------------
# 설문 리다이렉트 (구글 폼 미리 채우기)
# ---------------------------------------------------------------------------
class SurveyRedirectTests(PageTestCase):

    def location_query(self, response):
        return urlsplit(response['Location'])

    def test_redirects_to_prefilled_form(self):
        p = self.issue()
        with booth_env(BOOTH_FORM_URL=FORM_URL, BOOTH_FORM_NUMBER_ENTRY=FORM_ENTRY):
            response = self.client.get(self.url('survey_redirect', p))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], FORM_URL + '?usp=pp_url&entry.1234567=SF-001')
        self.assertBoothHeaders(response)          # 302 의 no-referrer 가 다음 요청의 Referer 를 막는다

    def test_value_is_url_encoded_and_existing_params_are_kept_or_replaced(self):
        p = self.issue()
        form_url = FORM_URL + '?hl=ko&usp=sf_link&entry.1234567=old&embedded=true'
        with booth_env(BOOTH_FORM_URL=form_url, BOOTH_FORM_NUMBER_ENTRY=FORM_ENTRY, BOOTH_NUMBER_PREFIX='S F&'):
            response = self.client.get(self.url('survey_redirect', p))
        parts = self.location_query(response)
        self.assertIn('entry.1234567=S+F%26-001', parts.query)      # 공백·& 가 쿼리를 깨지 않는다
        self.assertEqual(parse_qs(parts.query), {'hl': ['ko'], 'usp': ['pp_url'], FORM_ENTRY: ['S F&-001']})

    def test_numeric_entry_setting_is_normalized(self):
        p = self.issue()
        with booth_env(BOOTH_FORM_URL=FORM_URL, BOOTH_FORM_NUMBER_ENTRY='1234567'):
            response = self.client.get(self.url('survey_redirect', p))
        self.assertEqual(parse_qs(self.location_query(response).query)[FORM_ENTRY], ['SF-001'])

    def test_missing_entry_still_redirects_without_prefill(self):
        p = self.issue()
        with booth_env(BOOTH_FORM_URL=FORM_URL):
            with self.assertLogs('booth.views_pages', 'WARNING'):
                response = self.client.get(self.url('survey_redirect', p))
        self.assertEqual(response['Location'], FORM_URL + '?usp=pp_url')

    def test_unset_or_non_http_form_url_is_503(self):
        p = self.issue()
        for value in ('', 'javascript:alert(1)'):
            with booth_env(BOOTH_FORM_URL=value, BOOTH_FORM_NUMBER_ENTRY=FORM_ENTRY):
                with self.assertLogs('booth.views_pages', 'WARNING') if value else _noop():
                    response = self.client.get(self.url('survey_redirect', p))
            self.assertEqual(response.status_code, 503, value)
            self.assertContains(response, '설문 주소가 아직 설정되지 않았습니다', status_code=503)
            self.assertBoothHeaders(response)

    def test_unknown_token_is_404(self):
        with booth_env(BOOTH_FORM_URL=FORM_URL):
            response = self.client.get(reverse('booth:survey_redirect', kwargs={'token': 'nope'}))
        self.assertEqual(response.status_code, 404)


class _noop:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# 개인 페이지 상태
# ---------------------------------------------------------------------------
class PersonalPageStateTests(PageTestCase):

    def get(self, participant):
        response = self.client.get(self.url('personal', participant))
        self.assertBoothHeaders(response)
        self.assertNotIn(conf.TOKEN_COOKIE_NAME, response.cookies)      # 개인 페이지는 쿠키를 심지 않는다
        return response

    def test_unknown_or_expired_token_is_korean_404(self):
        response = self.client.get(reverse('booth:personal', kwargs={'token': 'nope'}))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, '페이지를 찾을 수 없습니다', status_code=404)
        self.assertContains(response, 'lang="ko"', status_code=404)
        self.assertBoothHeaders(response)

        p = self.issue()
        self.mark(p, created_at=timezone.now() - timedelta(days=14, minutes=1))
        self.assertEqual(self.client.get(self.url('personal', p)).status_code, 404)

    def test_before_survey(self):
        p = self.issue()
        response = self.get(p)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-state="before_survey"')
        self.assertContains(response, '<p class="label-big">SF-001</p>', html=False)
        self.assertContains(
            response,
            '<a class="btn" href="%s" target="_blank" rel="noopener noreferrer">설문하기</a>' % self.url('survey_redirect', p),
        )
        self.assertContains(response, '<meta name="referrer" content="no-referrer">')

    def test_after_survey_shows_full_name(self):
        p = self.with_survey(self.issue(), name='홍길동')
        response = self.get(p)
        self.assertContains(response, 'data-state="after_survey"')
        self.assertContains(response, '홍길동님, 설문이 접수되었습니다')
        self.assertContains(response, '체험 태블릿')

    def test_no_consent_state_message(self):
        p = self.ready(consent='동의하지 않습니다')
        self.assertEqual(p.report_status, Status.NO_CONSENT)
        response = self.get(p)
        self.assertContains(response, 'data-state="no_consent"')
        self.assertContains(response, '개인정보 수집·이용에 동의하지 않으셔서 체험 리포트를 만들 수 없습니다.')
        self.assertContains(response, '설문 다시 작성하기')
        self.assertNotContains(response, 'class="spinner"')
        self.assertNotContains(response, '홍길동')          # 동의가 없으니 이름도 쓰지 않는다

    def test_no_consent_is_shown_before_measurement_too(self):
        p = self.with_survey(self.issue(), consent='거부')
        response = self.get(p)
        self.assertContains(response, 'data-state="no_consent"')

    def test_generating_state_for_pending_and_generating(self):
        p = self.ready()
        for current in (Status.PENDING, Status.GENERATING):
            p = self.mark(p, report_status=current)
            response = self.get(p)
            self.assertContains(response, 'data-state="generating"')
            self.assertContains(response, 'data-report-status="%s"' % current)
            self.assertContains(response, 'data-generate-url="%s"' % self.url('generate', p))
            self.assertContains(response, 'data-status-url="%s"' % self.url('status', p))
            self.assertContains(response, '홍길동님의 리포트를 만들고 있습니다')
            self.assertContains(response, 'class="spinner"')

    def test_failed_state_has_retry_until_attempts_exhausted(self):
        p = self.mark(self.ready(), report_status=Status.FAILED, report_attempts=1, report_error='AI 거절(x)')
        response = self.get(p)
        self.assertContains(response, 'data-state="failed"')
        self.assertContains(response, 'id="retry-btn"')
        self.assertNotContains(response, 'AI 거절')          # 내부 오류 문구는 체험자에게 보이지 않는다

        p = self.mark(p, report_attempts=conf.MAX_REPORT_ATTEMPTS)
        response = self.get(p)
        self.assertContains(response, 'data-state="failed_final"')
        self.assertNotContains(response, 'id="retry-btn"')

    def test_deletion_notice_on_waiting_pages(self):
        p = self.issue()
        response = self.get(p)
        self.assertContains(response, '입력하신 정보는 %s에 삭제됩니다.' % services.deletion_date(p).isoformat())


# ---------------------------------------------------------------------------
# 리포트 렌더링
# ---------------------------------------------------------------------------
class ReportRenderingTests(PageTestCase):

    def get(self, participant):
        response = self.client.get(self.url('personal', participant))
        self.assertEqual(response.status_code, 200)
        self.assertBoothHeaders(response)
        return response

    def test_done_report_layout(self):
        p = self.done()
        response = self.get(p)
        content = self.html_text(response)
        self.assertContains(response, 'data-state="done"')
        self.assertContains(response, '홍길동님의 체험 리포트')
        self.assertContains(response, '체험일 %s' % services.kst(p.measurement_received_at).strftime('%Y-%m-%d'))
        self.assertContains(response, EXAMPLE_REPORT['headline']['one_liner'])
        self.assertContains(response, EXAMPLE_REPORT['closing'])
        for tip in EXAMPLE_REPORT['tips']:
            self.assertContains(response, tip)
        for insight in EXAMPLE_REPORT['survey_insights']:
            self.assertContains(response, insight['title'])
        for label, gloss in (('Sleep Index', '이완 지표'), ('Autonomic Balance', '자율신경 균형'),
                             ('Stress/Recovery Index', '회복 지표')):
            self.assertContains(response, '<h3>%s <span class="gloss-inline">(%s)</span></h3>' % (label, gloss))

        # 비교표: 서버가 displayed 값으로 만든다. 캡션이 숫자 바로 위(표 caption)에 있다.
        self.assertInHTML('<caption>체험 화면 표시값 (시연용 보정 포함)</caption>', content)
        self.assertLess(content.index('<caption>'), content.index('<tbody>'))
        self.assertInHTML('<tr><th scope="row">Sleep Index<span class="gloss">이완 지표</span></th>'
                          '<td>55</td><td>70</td></tr>', content)
        self.assertInHTML('<tr><th scope="row">Autonomic Balance<span class="gloss">자율신경 균형</span></th>'
                          '<td>48</td><td>48</td></tr>', content)
        self.assertInHTML('<tr><th scope="row">Stress/Recovery Index<span class="gloss">회복 지표</span></th>'
                          '<td>60</td><td>60</td></tr>', content)
        self.assertInHTML('<tr><th scope="row">심박수 (bpm)</th><td>72</td><td>72</td></tr>', content)
        self.assertNotIn('<caption>측정값</caption>', content)

        # 고정 푸터와 삭제일, 인쇄 버튼
        self.assertContains(response, '이 리포트는 체험용이며 의학적 진단이나 효과 판정이 아닙니다.')
        self.assertContains(response, '이 리포트는 %s에 삭제됩니다.' % services.deletion_date(p).isoformat())
        self.assertContains(response, 'PDF로 저장')
        self.assertContains(response, 'window.print()')
        self.assertContains(response, '.no-print { display: none !important; }')

    def test_caption_switches_to_raw_values_when_displayed_missing(self):
        p = self.done(displayed=False)
        content = self.html_text(self.get(p))
        self.assertInHTML('<caption>측정값</caption>', content)
        self.assertNotIn('체험 화면 표시값 (시연용 보정 포함)', content)
        # before/after 원 측정값: after 의 sleep_index 62, heart_rate 68
        self.assertInHTML('<tr><th scope="row">Sleep Index<span class="gloss">이완 지표</span></th>'
                          '<td>55</td><td>62</td></tr>', content)
        self.assertInHTML('<tr><th scope="row">심박수 (bpm)</th><td>72</td><td>68</td></tr>', content)

    def test_ai_text_is_escaped(self):
        evil = copy.deepcopy(EXAMPLE_REPORT)
        evil['headline']['one_liner'] = '<script>alert(1)</script>'
        evil['headline']['detail'] = '<img src=x onerror=alert(2)>'
        evil['survey_insights'][0]['title'] = '"><b>bold</b>'
        evil['indices'][0]['reading'] = '<iframe src="https://evil.example"></iframe>'
        evil['tips'][0] = '<a href="javascript:alert(3)">tip</a>'
        evil['closing'] = '</p><script>alert(4)</script>'
        p = self.done(report=evil)
        content = self.html_text(self.get(p))
        for raw in ('<script>alert(1)</script>', '<img src=x', '<b>bold</b>', '<iframe src="https://evil.example"',
                    '<a href="javascript:alert(3)"', '<script>alert(4)'):
            self.assertNotIn(raw, content)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', content)
        self.assertIn('&lt;img src=x onerror=alert(2)&gt;', content)
        self.assertIn('&quot;&gt;&lt;b&gt;bold&lt;/b&gt;', content)

    def test_name_is_escaped(self):
        p = self.done(name='<b>홍길동</b>')
        content = self.html_text(self.get(p))
        self.assertIn('&lt;b&gt;홍길동&lt;/b&gt;님의 체험 리포트', content)
        self.assertNotIn('<b>홍길동</b>', content)

    def test_empty_name_uses_generic_title(self):
        p = self.done(name='')
        self.assertContains(self.get(p), '체험자님의 체험 리포트')

    def test_invalid_stored_report_does_not_crash(self):
        p = self.done(report={'unexpected': True})
        with self.assertLogs('booth.views_pages', 'WARNING'):
            response = self.get(p)
        self.assertContains(response, 'data-state="broken"')
        self.assertContains(response, '리포트를 표시할 수 없습니다')


class FormatValueTests(SimpleTestCase):

    def test_format_value(self):
        cases = [(72, '72'), (55.0, '55'), (62.4, '62.4'), (0.0, '0'), (None, '-'),
                 (True, '-'), (float('nan'), '-'), ('70', '-')]
        for value, expected in cases:
            self.assertEqual(views_pages.format_value(value), expected, value)


# ---------------------------------------------------------------------------
# 상태·생성 JSON
# ---------------------------------------------------------------------------
class StatusJsonTests(PageTestCase):

    def test_status_keys_and_no_name(self):
        p = self.ready(name='홍길동')
        response = self.client.get(self.url('status', p))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'survey_received': True, 'measurement_received': True, 'report_status': 'pending',
            'state': 'generating',
        })
        body = self.html_text(response)
        for secret in ('홍길동', '홍○동', p.token, 'SF-001'):
            self.assertNotIn(secret, body)
        self.assertBoothHeaders(response)

    def test_status_before_anything(self):
        p = self.issue()
        self.assertEqual(self.client.get(self.url('status', p)).json(), {
            'survey_received': False, 'measurement_received': False, 'report_status': 'waiting',
            'state': 'before_survey',
        })

    def test_unknown_token_is_404_json(self):
        response = self.client.get(reverse('booth:status', kwargs={'token': 'nope'}))
        self.assertEqual(response.status_code, 404)
        self.assertIn('error', response.json())
        self.assertBoothHeaders(response)


class GenerateTests(PageTestCase):

    def fake_generate(self, final_status=Status.DONE):
        def _generate(participant):
            fields = {'report_status': final_status}
            if final_status == Status.DONE:
                fields['report'] = EXAMPLE_REPORT
            Participant.objects.using('booth').filter(pk=participant.pk).update(**fields)
            return final_status
        return _generate

    def test_generate_calls_report_module(self):
        p = self.ready()
        with mock.patch('booth.report.generate_report', side_effect=self.fake_generate()) as generate:
            response = self.client.post(self.url('generate', p))
        generate.assert_called_once()
        self.assertEqual(generate.call_args[0][0].pk, p.pk)
        self.assertEqual(response.status_code, 200)
        # report 모듈이 인스턴스를 갱신하지 않아도 DB 의 최종 상태를 돌려준다
        self.assertEqual(response.json(), {
            'survey_received': True, 'measurement_received': True, 'report_status': 'done', 'state': 'done',
        })
        self.assertBoothHeaders(response)

    def test_generate_called_for_generating_and_retryable_failed(self):
        for current, attempts in ((Status.GENERATING, 1), (Status.FAILED, 2)):
            p = self.mark(self.ready(), report_status=current, report_attempts=attempts)
            with mock.patch('booth.report.generate_report', side_effect=self.fake_generate(Status.FAILED)) as generate:
                response = self.client.post(self.url('generate', p))
            generate.assert_called_once()
            self.assertEqual(response.json()['report_status'], 'failed')

    def test_generate_not_called_when_not_ready_or_finished(self):
        waiting = self.with_survey(self.issue())
        no_consent = self.ready(consent='동의하지 않습니다')
        done = self.done()
        exhausted = self.mark(self.ready(), report_status=Status.FAILED, report_attempts=conf.MAX_REPORT_ATTEMPTS)
        expected = {waiting.pk: 'waiting', no_consent.pk: 'no_consent', done.pk: 'done', exhausted.pk: 'failed'}
        with mock.patch('booth.report.generate_report') as generate:
            for p in (waiting, no_consent, done, exhausted):
                response = self.client.post(self.url('generate', p))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['report_status'], expected[p.pk])
        generate.assert_not_called()

    def test_generate_exception_returns_503_json(self):
        p = self.ready(name='홍길동')
        with mock.patch('booth.report.generate_report', side_effect=RuntimeError('홍길동 boom')):
            with self.assertLogs('booth.views_pages', 'ERROR') as logs:
                response = self.client.post(self.url('generate', p))
        self.assertEqual(response.status_code, 503)
        data = response.json()
        self.assertEqual(data['report_status'], 'pending')
        self.assertIn('error', data)
        self.assertNotIn('홍길동', self.html_text(response))
        self.assertNotIn('홍길동', '\n'.join(logs.output))       # 예외 메시지(개인정보 가능)는 로그에 남기지 않는다
        self.assertBoothHeaders(response)

    def test_generate_requires_post(self):
        p = self.ready()
        with mock.patch('booth.report.generate_report') as generate:
            response = self.client.get(self.url('generate', p))
        self.assertEqual(response.status_code, 405)
        generate.assert_not_called()

    def test_generate_is_csrf_exempt(self):
        p = self.ready()
        with mock.patch('booth.report.generate_report', side_effect=self.fake_generate()):
            response = Client(enforce_csrf_checks=True).post(self.url('generate', p))
        self.assertEqual(response.status_code, 200)

    def test_generate_unknown_token_is_404_json(self):
        with mock.patch('booth.report.generate_report') as generate:
            response = self.client.post(reverse('booth:generate', kwargs={'token': 'nope'}))
        self.assertEqual(response.status_code, 404)
        self.assertIn('error', response.json())
        generate.assert_not_called()
