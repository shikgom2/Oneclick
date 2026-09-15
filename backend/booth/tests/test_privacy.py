# -*- coding:utf-8 -*-
"""booth 개인정보 처리 테스트.

동의 거부자의 이름·응답·측정값 미저장, 동의 문항 여러 개, 이름 문항 제목 불일치 시 생성 거부(실패 쪽으로
닫힘), 설문 확인 코드(번호만으로 남의 설문을 덮어쓰지 못함), 자유 응답 속 신원 정보 지우기, 로그 내용.

Claude 는 부르지 않는다(call_claude mock). 반드시 --settings=backend.settings_booth_test 로 실행한다.
"""
import json
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase
from django.urls import reverse

from booth import conf, report, services, views_pages
from booth.models import Participant
from booth.report_schema import EXAMPLE_REPORT
from booth.tests.test_api import KEY, ApiTestCase, with_code
from booth.tests.test_report import REAL_NAME, ReportDbTestCase, fake_message
from booth.tests.test_services import CleanEnvMixin, booth_env, measurement_payload, survey

Status = Participant.ReportStatus

CODE_TITLE = '확인 코드'


# ---------------------------------------------------------------------------
# 동의 거부
# ---------------------------------------------------------------------------
class ConsentRefusalStorageTests(ApiTestCase):

    def refused(self, **extra):
        return survey(name='이수진', consent='동의하지 않습니다', **extra)

    def test_refusal_keeps_no_name_answers_or_earlier_measurement(self):
        p = self.make(1)
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        services.apply_survey(p, self.refused(**{'수면제 복용 여부': '매일 복용'}))
        stored = self.reload(p)
        self.assertTrue(stored.has_survey)
        self.assertFalse(stored.consent)
        self.assertEqual(stored.name, '')
        self.assertEqual(stored.survey_answers, {})
        self.assertIsNone(stored.measurement)
        self.assertIsNotNone(stored.measurement_received_at)       # '측정함'만 남긴다
        self.assertEqual(stored.report_status, Status.NO_CONSENT)

    def test_measurement_after_refusal_stores_only_receipt(self):
        p = self.make(1)
        services.apply_survey(p, self.refused())
        response = self.post_json('booth:api_measurement', {'number': 1, 'measurement': measurement_payload()})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['report_status'], 'no_consent')
        self.assertEqual(body['masked_name'], '')
        stored = self.reload(p)
        self.assertIsNone(stored.measurement)
        self.assertTrue(stored.has_measurement)
        pending = self.get_api(reverse('booth:api_pending')).json()['participants']
        self.assertEqual(pending, [])                               # 측정을 마쳐 대기 목록에서 빠진다

    def test_consent_after_refusal_needs_new_measurement(self):
        p = self.make(1)
        services.apply_survey(p, self.refused())
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        response = self.post_json('booth:api_survey', {'number': 1, 'answers': with_code(p, name='이수진')})
        self.assertEqual(response.status_code, 200, response.content)
        stored = self.reload(p)
        self.assertTrue(stored.consent)
        self.assertEqual(stored.name, '이수진')
        self.assertFalse(stored.has_measurement)                    # 값이 없는 측정으로 리포트를 만들지 않는다
        self.assertEqual(stored.report_status, Status.WAITING)
        pending = self.get_api(reverse('booth:api_pending')).json()['participants']
        self.assertEqual([item['number'] for item in pending], [1])

    def test_withdrawal_after_report_deletes_everything_personal(self):
        p = self.make(1)
        services.apply_survey(p, survey(name='이수진'))
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        self.set_fields(p, report_status=Status.DONE, report=EXAMPLE_REPORT, report_attempts=1)
        response = self.post_json('booth:api_survey', {'number': 1, 'answers': with_code(p, consent='거부')})
        self.assertEqual(response.status_code, 200, response.content)
        stored = self.reload(p)
        self.assertEqual((stored.name, stored.survey_answers, stored.measurement, stored.report),
                         ('', {}, None, None))
        self.assertEqual(stored.report_status, Status.NO_CONSENT)

    def test_intake_log_has_no_consent_state(self):
        p = self.make(1)
        with self.assertLogs('booth.services', 'INFO') as logs:
            services.apply_survey(p, self.refused())
        text = '\n'.join(logs.output)
        self.assertNotIn('consent', text)
        self.assertNotIn('no_consent', text)
        self.assertNotIn('이수진', text)


class MultipleConsentTests(CleanEnvMixin, SimpleTestCase):
    TITLES = '개인정보 수집·이용 동의|민감정보 처리 동의|개인정보 국외 이전 동의'

    def answers(self, sensitive='동의합니다', overseas='동의합니다'):
        answers = survey()
        answers['민감정보 처리 동의'] = sensitive
        answers['개인정보 국외 이전 동의'] = overseas
        return answers

    def test_every_configured_consent_must_be_given(self):
        with booth_env(BOOTH_SURVEY_CONSENT_TITLES=self.TITLES):
            self.assertTrue(services.extract_survey_fields(self.answers())[1])
            self.assertFalse(services.extract_survey_fields(self.answers(overseas='동의하지 않습니다'))[1])
            self.assertFalse(services.extract_survey_fields(self.answers(sensitive='거부'))[1])
            missing = self.answers()
            del missing['개인정보 국외 이전 동의']
            self.assertFalse(services.extract_survey_fields(missing)[1])       # 문항이 없으면 동의 아님

    def test_all_consent_titles_are_excluded_from_ai(self):
        with booth_env(BOOTH_SURVEY_CONSENT_TITLES=self.TITLES):
            self.assertEqual(services.survey_answers_for_ai(self.answers()),
                             {'평소 잠들기까지 걸리는 시간': '30분 이상'})

    def test_single_title_setting_still_works(self):
        with booth_env(BOOTH_SURVEY_CONSENT_TITLE='동의'):
            self.assertEqual(conf.survey_consent_titles(), ['동의'])
            self.assertTrue(services.extract_survey_fields({'이름': '가', '동의': '동의합니다'})[1])
        with booth_env(BOOTH_SURVEY_CONSENT_TITLES=' | '):        # 비어 있는 목록은 단일 제목으로
            self.assertEqual(conf.survey_consent_titles(), ['개인정보 수집·이용 동의'])


# ---------------------------------------------------------------------------
# 이름 문항 제목 불일치
# ---------------------------------------------------------------------------
class NameTitleFailClosedTests(ReportDbTestCase):

    def ready_with(self, answers):
        with self.assertLogs('booth.services', 'WARNING'):
            return self.ready(answers=answers)

    def test_name_under_other_title_is_never_sent(self):
        variants = [
            {'성함': REAL_NAME},
            {'이름(필수)': REAL_NAME},
        ]
        for extra in variants:
            answers = {'참가자 번호': 'SF-001', '개인정보 수집·이용 동의': '동의합니다', '질문': '답'}
            answers.update(extra)
            p = self.ready_with(answers)
            with mock.patch('booth.report.call_claude', return_value=fake_message()) as call, \
                    self.assertLogs('booth.report', 'WARNING'):
                status = report.generate_report(p)
            call.assert_not_called()
            self.assertEqual(status, Status.FAILED)
            self.assertEqual(self.reload(p).report_error, report._ERROR_NAME_TITLE)

    def test_duplicate_name_question_is_excluded(self):
        answers = survey(name=REAL_NAME)
        answers['이름 (2)'] = '김영수'
        self.assertEqual(services.survey_answers_for_ai(answers), {'평소 잠들기까지 걸리는 시간': '30분 이상'})

    def test_form_without_name_question_can_opt_out(self):
        answers = {'참가자 번호': 'SF-001', '개인정보 수집·이용 동의': '동의합니다', '질문': '답'}
        with booth_env(BOOTH_SURVEY_NAME_TITLE='-'):
            p = self.ready(answers=answers)
            with mock.patch('booth.report.call_claude', return_value=fake_message()) as call:
                status = report.generate_report(p)
        call.assert_called_once()
        self.assertEqual(status, Status.DONE)


# ---------------------------------------------------------------------------
# 설문 확인 코드
# ---------------------------------------------------------------------------
class SurveyCodeTests(ApiTestCase):

    def test_code_shape_and_uniqueness(self):
        a, b = self.make(1), self.make(2)
        code = services.survey_code(a)
        self.assertRegex(code, r'^[A-Z2-7]{8}$')
        self.assertEqual(code, services.survey_code(a))
        self.assertNotEqual(code, services.survey_code(b))

    def test_code_is_prefilled_in_form_url(self):
        p = self.make(1)
        form_url = 'https://docs.google.com/forms/d/e/FORM_ID/viewform'
        with booth_env(BOOTH_FORM_URL=form_url, BOOTH_FORM_NUMBER_ENTRY='entry.1', BOOTH_FORM_CODE_ENTRY='2222'):
            query = parse_qs(urlsplit(views_pages.build_form_url(p)).query)
        self.assertEqual(query['entry.1'], ['SF-001'])
        self.assertEqual(query['entry.2222'], [services.survey_code(p)])

    def test_wrong_code_is_400_and_changes_nothing(self):
        p = self.make(1)
        self.assertEqual(self.post_json('booth:api_survey', {'number': 1, 'answers': with_code(p)}).status_code, 200)
        attack = survey(name='공격자', consent='동의하지 않습니다')
        attack[CODE_TITLE] = 'AAAAAAAA'
        with self.assertLogs('booth.views_api', 'WARNING'):
            message = self.assertError(self.post_json('booth:api_survey', {'number': 1, 'answers': attack}), 400)
        self.assertIn('확인 코드', message)
        stored = self.reload(p)
        self.assertEqual(stored.name, '홍길동')
        self.assertTrue(stored.consent)

    def test_overwrite_without_code_is_409(self):
        p = self.make(1)
        self.assertEqual(self.post_json('booth:api_survey', {'number': 1, 'answers': survey()}).status_code, 200)
        with self.assertLogs('booth.views_api', 'WARNING'):
            self.assertError(self.post_json('booth:api_survey', {'number': 1, 'answers': survey(name='공격자')}), 409)
        self.assertEqual(self.reload(p).name, '홍길동')
        response = self.post_json('booth:api_survey', {'number': 1, 'answers': with_code(p, name='박지민')})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.reload(p).name, '박지민')

    def test_missing_code_is_refused_when_form_prefills_it(self):
        p = self.make(1)
        with booth_env(BOOTH_API_KEY=KEY, BOOTH_FORM_CODE_ENTRY='entry.2222'):
            with self.assertLogs('booth.views_api', 'WARNING'):
                self.assertError(self.post_json('booth:api_survey', {'number': 1, 'answers': survey()}), 400)
            self.assertFalse(self.reload(p).has_survey)
            code = services.survey_code(p)
            answers = survey()
            answers[' 확인 코드 *'] = ' %s-%s ' % (code[:4].lower(), code[4:])      # 대소문자·공백·대시 무시
            self.assertEqual(self.post_json('booth:api_survey', {'number': 1, 'answers': answers}).status_code, 200)

    def test_code_is_not_sent_to_ai(self):
        p = self.make(1)
        code = services.survey_code(p)
        answers = with_code(p, **{'하고 싶은 말': '코드는 %s 입니다' % code})
        services.apply_survey(p, answers)
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        dumped = json.dumps(report.build_user_payload(self.reload(p)), ensure_ascii=False)
        self.assertNotIn(code, dumped)
        self.assertNotIn(CODE_TITLE, dumped)


# ---------------------------------------------------------------------------
# 자유 응답 속 신원 정보
# ---------------------------------------------------------------------------
class FreeTextScrubTests(ReportDbTestCase):

    def payload_text(self, name, text):
        p = services.issue_participant('phone')
        services.apply_survey(p, survey(name=name, **{'하고 싶은 말': text}))
        services.apply_measurement(p, services.validate_measurement(measurement_payload()))
        return report.build_user_payload(self.reload(p))['survey_answers']['하고 싶은 말']

    def test_given_name_phone_and_email_are_scrubbed(self):
        text = self.payload_text('홍길동', '길동이는 요즘 잠을 못 자요. 010-1234-5678 이나 me.kim@example.com 으로 연락 주세요.')
        for secret in ('길동', '010-1234-5678', 'me.kim@example.com'):
            self.assertNotIn(secret, text)
        self.assertIn('요즘 잠을 못 자요', text)

    def test_given_name_without_honorific_is_kept(self):
        # '지원' 같은 흔한 낱말까지 지우지 않도록, 호칭·조사가 붙은 경우만 지운다.
        text = self.payload_text('김지원', '지원이 필요해요. 지원씨라고 불러 주세요.')
        self.assertIn('지원이 필요해요', text)
        self.assertNotIn('지원씨', text)
