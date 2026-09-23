# -*- coding:utf-8 -*-
"""설문 수면 지표(survey_metrics)와 체험 전 측정값 해석(measurement_metrics) 테스트. 순수 함수라 DB 가 없다.

반드시 --settings=backend.settings_booth_test 로 실행한다.
"""
import json
from datetime import date

from django.test import SimpleTestCase

from booth import measurement_metrics as mm
from booth import survey_metrics as sm

BEDTIME_BINS = ('밤 9시 이전', '밤 9시 ~ 10시', '밤 10시 ~ 11시', '밤 11시 ~ 12시', '밤 12시 ~ 새벽 1시',
                '새벽 1시 ~ 2시', '새벽 2시 이후')
WAKE_BINS = ('새벽 5시 이전', '새벽 5시 ~ 6시', '아침 6시 ~ 7시', '아침 7시 ~ 8시', '아침 8시 ~ 9시',
             '오전 9시 ~ 10시', '오전 10시 이후')


def full_survey(**overrides):
    answers = {
        '1. 어젯밤 잠자리에 든 시각': '밤 11시 ~ 12시',
        '2. 오늘 아침 잠자리에서 나온 시각': '아침 7시 ~ 8시',
        '3. 잠자리에 든 뒤 잠들기까지 걸린 시간': '16 ~ 30분',
        '4. 실제로 잠든 시간 (총 수면 시간)': '6 ~ 7시간',
        '5. 밤중에 깬 횟수 (기억나는 만큼)': '2회',
        '6. 오늘 아침 일어났을 때 개운한 정도': '조금 개운함',
        '7. 어젯밤 잠에 대한 만족도': '보통',
        '8. 지금 이 순간, 얼마나 개운한가요?': '약간 졸림',
        '9. 평소 취침 시각이 얼마나 일정한가요? (최근 한 달 기준)': '대체로 같음',
    }
    answers.update(overrides)
    return answers


# ---------------------------------------------------------------------------
# 보기 문구 해석
# ---------------------------------------------------------------------------
class ClockParsingTests(SimpleTestCase):

    def test_bedtime_bins_map_to_midpoints(self):
        expected = ['20:30', '21:30', '22:30', '23:30', '00:30', '01:30', '02:30']
        self.assertEqual([sm.format_clock(sm.parse_clock_time(b, evening=True)) for b in BEDTIME_BINS], expected)

    def test_wake_bins_map_to_midpoints(self):
        expected = ['04:30', '05:30', '06:30', '07:30', '08:30', '09:30', '10:30']
        self.assertEqual([sm.format_clock(sm.parse_clock_time(b)) for b in WAKE_BINS], expected)

    def test_wording_variants(self):
        cases = {
            '밤 11시~12시': 23.5, '밤11시 - 12시': 23.5, '오후 11시 ~ 12시': 23.5, '오후 10시 ~ 12시': 23.0,
            '23:00 ~ 24:00': 23.5, '밤 9시 ~ 새벽 1시': 23.0,
            '21:30': 21.5, '밤 10시 반': 22.5, '새벽 1시 30분': 1.5, '자정 ~ 새벽 1시': 0.5,
            '9시 ~ 10시': 21.5, '11시 ~ 12시': 23.5,     # 취침 문항에서 시간대 낱말이 없으면 저녁, 12시는 자정
            '12시 이후': 0.5,
        }
        for text, expected in cases.items():
            self.assertEqual(sm.parse_clock_time(text, evening=True), expected, text)
        self.assertEqual(sm.parse_clock_time('9시 ~ 10시'), 9.5)         # 기상 문항은 그대로 오전
        self.assertEqual(sm.parse_clock_time('오전 12시 이전'), 23.5)   # 0시 - 30분

    def test_unreadable_clock_is_none(self):
        for text in ('', None, '모르겠음', '늦게', ['밤'], '99시', 12, {'a': 1}):
            self.assertIsNone(sm.parse_clock_time(text, evening=True), repr(text))

    def test_time_in_bed_crosses_midnight(self):
        self.assertEqual(sm.hours_between(23.5, 7.5), 8.0)
        self.assertEqual(sm.hours_between(0.5, 6.5), 6.0)
        self.assertEqual(sm.hours_between(20.5, 4.5), 8.0)
        self.assertEqual(sm.hours_between(2.5, 10.5), 8.0)
        self.assertIsNone(sm.hours_between(7.5, 7.5))
        self.assertIsNone(sm.hours_between(None, 7.5))


class DurationParsingTests(SimpleTestCase):

    def test_total_sleep_bins(self):
        cases = {'5시간 미만': 4.5, '5 ~ 6시간': 5.5, '6 ~ 7시간': 6.5, '7 ~ 8시간': 7.5, '8시간 이상': 8.5,
                 '6시간 30분': 6.5, '7시간 반': 7.5, '약 7시간': 7.0, '9시간 초과': 9.5}
        for text, expected in cases.items():
            self.assertEqual(sm.parse_sleep_hours(text), expected, text)
        for text in ('', '모름', None, '30시간'):
            self.assertIsNone(sm.parse_sleep_hours(text), repr(text))

    def test_latency_bins_use_upper_bound(self):
        cases = {'15분 이내': 15, '16 ~ 30분': 30, '31 ~ 60분': 60, '1시간 초과': 61, '30분 이상': 31,
                 '1시간 ~ 2시간': 120, '10분': 10, '15분 이하': 15}
        for text, expected in cases.items():
            self.assertEqual(sm.parse_latency_minutes(text), expected, text)
        self.assertIsNone(sm.parse_latency_minutes('금방'))
        self.assertIsNone(sm.parse_latency_minutes(None))

    def test_awakenings(self):
        cases = {'0회': 0, '1회': 1, '2회': 2, '3회 이상': 3, '없음': 0, '5번': 5}
        for text, expected in cases.items():
            self.assertEqual(sm.parse_awakenings(text), expected, text)
        self.assertEqual(sm.parse_awakenings(['1회']), 1)          # 체크박스 응답(목록)도 읽는다
        for text in ('기억 안 남', '', None):
            self.assertIsNone(sm.parse_awakenings(text), repr(text))


class ComponentTests(SimpleTestCase):

    def test_psqi_components(self):
        self.assertEqual([sm.latency_component(m) for m in (15, 16, 30, 31, 60, 61, None)], [0, 1, 1, 2, 2, 3, None])
        self.assertEqual([sm.duration_component(h) for h in (7.5, 7.0, 6.0, 5.9, 5.0, 4.5, None)],
                         [0, 1, 1, 2, 2, 3, None])
        self.assertEqual([sm.efficiency_component(p) for p in (100.0, 85.0, 84.9, 75.0, 74.9, 65.0, 64.9, None)],
                         [0, 0, 1, 1, 2, 2, 3, None])
        self.assertEqual([sm.awakenings_component(n) for n in (0, 1, 2, 3, 7, None)], [0, 1, 2, 3, 3, None])

    def test_efficiency_is_capped_and_rounded(self):
        self.assertEqual(sm.sleep_efficiency(6.5, 8.0), 81.2)
        self.assertEqual(sm.sleep_efficiency(8.5, 8.0), 100.0)        # 구간 추정이라 100 을 넘을 수 있다
        self.assertEqual(sm.sleep_efficiency(4.5, 9.0), 50.0)
        self.assertIsNone(sm.sleep_efficiency(None, 8.0))
        self.assertIsNone(sm.sleep_efficiency(6.5, None))
        self.assertIsNone(sm.sleep_efficiency(6.5, 0))

    def test_score_bands(self):
        self.assertEqual([sm.score_band(s) for s in (0, 3, 4, 6, 7, 12, None)],
                         ['양호', '양호', '보통', '보통', '개선 여지', '개선 여지', None])


# ---------------------------------------------------------------------------
# 문항 찾기와 전체 계산
# ---------------------------------------------------------------------------
class FindQuestionsTests(SimpleTestCase):

    def test_all_nine_questions_are_found_despite_numbering_and_whitespace(self):
        answers = full_survey()
        found = sm.find_questions(answers)
        self.assertEqual(set(found), {'bedtime', 'wake_time', 'latency', 'total_sleep', 'awakenings',
                                      'morning_freshness', 'satisfaction', 'alertness', 'regularity'})
        self.assertEqual(found['bedtime'], '1. 어젯밤 잠자리에 든 시각')
        self.assertEqual(found['regularity'], '9. 평소 취침 시각이 얼마나 일정한가요? (최근 한 달 기준)')

    def test_variants(self):
        answers = {
            '①  어젯밤  잠자리에 든  시각 *': 'x', 'Q2) 오늘 아침 기상 시각': 'x', '3 잠자리에 든 뒤 잠드는 데 걸린 시간': 'x',
            '실제 수면 시간': 'x', '밤중에 깨어난 횟수': 'x', '아침에 개운했나요': 'x', '수면 만족도': 'x',
            '지금 얼마나 맑은가요': 'x', '취침 시각 규칙성': 'x', '이름': '홍길동', '하고 싶은 말': '잘 자고 싶어요',
        }
        found = sm.find_questions(answers)
        self.assertEqual(found, {
            'latency': '3 잠자리에 든 뒤 잠드는 데 걸린 시간', 'wake_time': 'Q2) 오늘 아침 기상 시각',
            'bedtime': '①  어젯밤  잠자리에 든  시각 *', 'total_sleep': '실제 수면 시간', 'awakenings': '밤중에 깨어난 횟수',
            'morning_freshness': '아침에 개운했나요', 'alertness': '지금 얼마나 맑은가요', 'satisfaction': '수면 만족도',
            'regularity': '취침 시각 규칙성',
        })

    def test_two_freshness_questions_are_told_apart(self):
        answers = {'지금 이 순간, 얼마나 개운한가요?': '매우 맑음', '오늘 아침 일어났을 때 개운한 정도': '전혀 개운하지 않음'}
        found = sm.find_questions(answers)
        self.assertEqual(found['alertness'], '지금 이 순간, 얼마나 개운한가요?')
        self.assertEqual(found['morning_freshness'], '오늘 아침 일어났을 때 개운한 정도')

    def test_non_dict_or_unknown_titles(self):
        self.assertEqual(sm.find_questions(None), {})
        self.assertEqual(sm.find_questions('x'), {})
        self.assertEqual(sm.find_questions({'24시간 중 낮잠': '있음', '': 'x', 12: 'y'}), {})


class ComputeSurveyMetricsTests(SimpleTestCase):

    def test_full_survey_rows_and_score(self):
        result = sm.compute_survey_metrics(full_survey())
        by_key = {row['key']: row for row in result.rows}
        self.assertEqual([row['key'] for row in result.rows],
                         ['bedtime', 'wake_time', 'time_in_bed', 'latency', 'total_sleep', 'efficiency', 'awakenings',
                          'morning_freshness', 'satisfaction', 'alertness', 'regularity'])
        self.assertEqual(by_key['bedtime']['value'], '약 23:30')
        self.assertEqual(by_key['wake_time']['value'], '약 07:30')
        self.assertEqual(by_key['time_in_bed'], {'key': 'time_in_bed', 'label': '잠자리에 머문 시간', 'answer': '-',
                                                 'value': '약 8시간 (추정)', 'reference': '취침·기상 시각 응답으로 계산',
                                                 'verdict': '-'})
        self.assertEqual((by_key['latency']['value'], by_key['latency']['verdict']), ('16 ~ 30분', '보통'))
        self.assertEqual((by_key['total_sleep']['value'], by_key['total_sleep']['verdict']),
                         ('약 6.5시간 (추정)', '조금 짧은 편'))
        self.assertEqual(by_key['efficiency'], {'key': 'efficiency', 'label': '수면 효율', 'answer': '-',
                                                'value': '약 81.2% (추정)', 'reference': '85% 이상 양호 (PSQI 구간)',
                                                'verdict': '보통'})
        self.assertEqual((by_key['awakenings']['value'], by_key['awakenings']['verdict']), ('2회', '잦은 편'))
        self.assertEqual(by_key['alertness'], {'key': 'alertness', 'label': '지금 이 순간의 개운함', 'answer': '약간 졸림',
                                               'value': '약간 졸림', 'reference': '5단계 자기평가', 'verdict': '-'})
        self.assertEqual(result.score, {'score': 5, 'max': 12, 'available': 4, 'total': 4, 'band': '보통',
                                        'summary': '간이 수면 점수 5점 / 12점 (4개 항목 중 4개로 계산) · 보통'})
        for row in result.rows:
            self.assertEqual(set(row), {'key', 'label', 'answer', 'value', 'reference', 'verdict'})
            for value in row.values():
                self.assertIsInstance(value, str)

    def test_score_bands_and_partial_components(self):
        good = full_survey(**{'3. 잠자리에 든 뒤 잠들기까지 걸린 시간': '15분 이내',
                              '4. 실제로 잠든 시간 (총 수면 시간)': '7 ~ 8시간',
                              '5. 밤중에 깬 횟수 (기억나는 만큼)': '0회'})
        result = sm.compute_survey_metrics(good)
        self.assertEqual((result.score['score'], result.score['band']), (0, '양호'))       # 7.5/8.0 = 93.8% -> 0
        self.assertEqual(result.payload['sleep_efficiency_percent'], 93.8)

        bad = full_survey(**{'1. 어젯밤 잠자리에 든 시각': '밤 9시 이전', '2. 오늘 아침 잠자리에서 나온 시각': '오전 10시 이후',
                             '3. 잠자리에 든 뒤 잠들기까지 걸린 시간': '1시간 초과',
                             '4. 실제로 잠든 시간 (총 수면 시간)': '5시간 미만',
                             '5. 밤중에 깬 횟수 (기억나는 만큼)': '3회 이상'})
        result = sm.compute_survey_metrics(bad)
        self.assertEqual(result.payload['time_in_bed_hours'], 14.0)
        self.assertEqual(result.payload['sleep_efficiency_percent'], 32.1)
        self.assertEqual(result.payload['components'], {'latency': 3, 'duration': 3, 'efficiency': 3, 'awakenings': 3})
        self.assertEqual((result.score['score'], result.score['band']), (12, '개선 여지'))

        # 취침·기상 없음 -> 효율 없음. 세 항목으로는 계산한다(항목 수를 밝힌다).
        three = {k: v for k, v in full_survey().items() if not k.startswith(('1.', '2.'))}
        result = sm.compute_survey_metrics(three)
        self.assertEqual(result.payload['components']['efficiency'], None)
        self.assertEqual(result.score['summary'], '간이 수면 점수 4점 / 12점 (4개 항목 중 3개로 계산) · 보통')
        self.assertNotIn('efficiency', [row['key'] for row in result.rows])

        # 두 항목뿐이면 계산 불가.
        two = {k: v for k, v in full_survey().items() if k.startswith(('3.', '5.', '6.'))}
        result = sm.compute_survey_metrics(two)
        self.assertIsNone(result.score['score'])
        self.assertIsNone(result.score['band'])
        self.assertEqual(result.score['summary'], '간이 수면 점수: 계산 불가 (4개 항목 중 2개만 계산됨)')
        self.assertIsNone(result.payload['simple_score']['score'])

    def test_blank_and_unreadable_answers(self):
        answers = full_survey(**{'1. 어젯밤 잠자리에 든 시각': '', '4. 실제로 잠든 시간 (총 수면 시간)': '기억이 안 나요',
                                 '5. 밤중에 깬 횟수 (기억나는 만큼)': ['3회 이상', '기타']})
        result = sm.compute_survey_metrics(answers)
        by_key = {row['key']: row for row in result.rows}
        self.assertEqual((by_key['bedtime']['answer'], by_key['bedtime']['value']), ('응답 없음', '-'))
        self.assertEqual((by_key['total_sleep']['answer'], by_key['total_sleep']['value'], by_key['total_sleep']['verdict']),
                         ('기억이 안 나요', '해석 불가', '-'))
        self.assertEqual(by_key['awakenings']['value'], '3회')
        self.assertNotIn('time_in_bed', by_key)
        self.assertNotIn('efficiency', by_key)
        self.assertIsNone(result.payload['bedtime'])
        self.assertIsNone(result.payload['total_sleep_hours'])
        self.assertEqual(result.payload['components'], {'latency': 1, 'duration': None, 'efficiency': None, 'awakenings': 3})
        self.assertIsNone(result.score['score'])                  # 2개 항목뿐

    def test_never_raises_and_is_json_serializable(self):
        weird = [None, 'text', 42, [], {}, {'x': 'y'}, {None: None}, {'1. 잠자리에 든 시각': 3.5},
                 {'잠자리에 든 시각': {'nested': 1}}, {'잠들기까지': 'x' * 10000}, {'총 수면 시간': '10**400시간'},
                 {'밤중에 깬 횟수': '99999999999999999999회'}, {'잠자리에서 나온 시각': '25:99'}]
        for answers in weird:
            result = sm.compute_survey_metrics(answers)
            json.dumps(result.payload, ensure_ascii=False)
            self.assertIsInstance(result.rows, list)
        self.assertEqual(sm.compute_survey_metrics({}).rows, [])
        self.assertIsNone(sm.compute_survey_metrics({}).score)
        self.assertEqual(sm.compute_survey_metrics({'밤중에 깬 횟수': '99999999999999999999회'}).payload['components']['awakenings'], 3)

    def test_payload_shape(self):
        payload = sm.compute_survey_metrics(full_survey()).payload
        self.assertEqual(set(payload), {'note', 'answers', 'bedtime', 'wake_time', 'time_in_bed_hours', 'total_sleep_hours',
                                        'sleep_efficiency_percent', 'sleep_latency_minutes_max', 'awakenings',
                                        'self_ratings', 'components', 'verdicts', 'simple_score', 'references'})
        self.assertIn('추정', payload['note'])
        self.assertEqual(payload['verdicts'], {'latency': '보통', 'duration': '조금 짧은 편', 'efficiency': '보통',
                                               'awakenings': '잦은 편'})
        self.assertEqual(payload['self_ratings'], {'morning_freshness': '조금 개운함', 'satisfaction': '보통',
                                                   'alertness': '약간 졸림', 'regularity': '대체로 같음'})
        self.assertEqual(payload['answers']['bedtime'], '밤 11시 ~ 12시')


# ---------------------------------------------------------------------------
# 체험 전 측정값 해석
# ---------------------------------------------------------------------------
def before(**overrides):
    values = {'heart_rate': 72, 'rmssd_ms': 35.5, 'sdnn_ms': 48.0, 'sleep_index': 55.0,
              'autonomic_balance': 48.0, 'stress_recovery_index': 60.0, 'rr_count': 300}
    values.update(overrides)
    return values


class CategorizeTests(SimpleTestCase):

    def test_bands_at_boundaries(self):
        cases = [
            ('heart_rate', (49, '낮은 편'), (59, '낮은 편'), (60, '참고 범위 안'), (100, '참고 범위 안'), (101, '높은 편')),
            ('rmssd_ms', (19.9, '낮은 편'), (20, '흔한 범위 안'), (50, '흔한 범위 안'), (50.1, '높은 편')),
            ('sdnn_ms', (29.9, '낮은 편'), (30, '보통'), (60, '보통'), (60.1, '높은 편')),
            ('rr_count', (39, '접촉 부족으로 신뢰도 낮음'), (40, '충분'), (300, '충분')),
            ('sleep_index', (39.9, '낮음'), (40, '중간'), (69.9, '중간'), (70, '높음')),
            ('autonomic_balance', (0, '낮음'), (50, '중간'), (93, '높음')),
            ('stress_recovery_index', (39.9, '낮음 (앱 등급 Alert)'), (40, '중간 (앱 등급 Warning)'),
             (69.9, '중간 (앱 등급 Warning)'), (70, '높음 (앱 등급 Good)')),
        ]
        for key, *pairs in cases:
            for value, expected in pairs:
                self.assertEqual(mm.categorize(key, value), expected, (key, value))
            self.assertEqual(mm.categorize(key, None), '링 접촉 부족으로 계산 불가')
        # 진단 표현은 어디에도 없다.
        for key in ('heart_rate', 'rmssd_ms', 'sdnn_ms', 'sleep_index', 'stress_recovery_index'):
            for value in (0, 30, 60, 100, 200):
                self.assertNotIn('정상', mm.categorize(key, value))


class InterpretPreMeasurementTests(SimpleTestCase):

    def test_rows_and_payload(self):
        result = mm.interpret_pre_measurement({'before': before(), 'after': before(heart_rate=61)})
        self.assertEqual([row['label'] for row in result.rows],
                         ['심박수', 'RMSSD', 'SDNN', '검출 박동 수', 'Sleep Index', 'Autonomic Balance', 'Stress/Recovery Index'])
        by_key = {row['key']: row for row in result.rows}
        self.assertEqual(by_key['heart_rate'], {'key': 'heart_rate', 'label': '심박수', 'gloss': '', 'value': '72 bpm',
                                                'reference': '안정 시 60~100 bpm', 'verdict': '참고 범위 안'})
        self.assertEqual(by_key['rr_count']['value'], '300회')
        self.assertEqual(by_key['sdnn_ms']['value'], '48 ms')
        self.assertEqual(by_key['sleep_index']['value'], '55')
        self.assertEqual(by_key['stress_recovery_index']['verdict'], '중간 (앱 등급 Warning)')
        self.assertIn('60초', result.note)
        payload = result.payload
        self.assertEqual(set(payload), {'method', 'values', 'categories', 'reference_ranges', 'missing',
                                        'reliability', 'age_band', 'age_band_note'})
        self.assertEqual(payload['values']['heart_rate_bpm'], 72)
        self.assertEqual(payload['missing'], [])
        self.assertIn('300회', payload['reliability'])
        self.assertNotIn('61', json.dumps(payload))               # 체험 후 값은 읽지 않는다

    def test_nulls_and_low_contact(self):
        result = mm.interpret_pre_measurement({'before': before(rmssd_ms=None, sdnn_ms=None, sleep_index=None, rr_count=12)})
        by_key = {row['key']: row for row in result.rows}
        self.assertEqual((by_key['rmssd_ms']['value'], by_key['rmssd_ms']['verdict']),
                         ('측정되지 않음', '링 접촉 부족으로 계산 불가'))
        self.assertEqual(by_key['rr_count']['verdict'], '접촉 부족으로 신뢰도 낮음')
        self.assertEqual(by_key['autonomic_balance']['verdict'], '중간 (HRV 계산 실패 시 앱 기본값일 수 있음)')
        self.assertEqual(by_key['heart_rate']['verdict'], '참고 범위 안')
        self.assertEqual(result.payload['missing'], ['RMSSD', 'SDNN', 'Sleep Index'])
        self.assertIn('신뢰도가 낮다', result.payload['reliability'])

    def test_garbage_input_never_raises(self):
        for measurement in (None, 'x', [], {}, {'before': None}, {'before': 'x'}, {'before': {'heart_rate': 'x'}},
                            {'before': {'heart_rate': True, 'rmssd_ms': float('nan')}}):
            result = mm.interpret_pre_measurement(measurement)
            self.assertEqual(len(result.rows), 7)
            json.dumps(result.payload)
        empty = mm.interpret_pre_measurement(None)
        self.assertTrue(all(row['value'] == '측정되지 않음' for row in empty.rows))
        self.assertEqual(len(empty.payload['missing']), 7)
        self.assertIn('판단할 수 없다', empty.payload['reliability'])

    def test_format_number(self):
        cases = [(72, '72'), (55.0, '55'), (35.5, '35.5'), (47.25, '47.2'), (0.0, '0'), (None, None), (True, None),
                 (float('nan'), None), ('70', None)]
        for value, expected in cases:
            self.assertEqual(mm.format_number(value), expected, value)


class AgeTests(SimpleTestCase):
    """나이 읽기와 나이대별 RMSSD 기준.

    RMSSD 는 나이가 들수록 낮아진다. 고정 범위(20~50ms)만 쓰면 60대 참가자가 거의 늘 '낮은 편'이 되어
    사실과 다르게 읽히므로, 나이를 알면 같은 나이대 기준으로 판정한다(measurement_metrics.AGE_BANDS).
    """

    TODAY = date(2026, 9, 23)

    def test_age_from_text_accepts_every_form_we_might_ask_for(self):
        cases = [
            ('1965-03-05', 61), ('1965.3.5', 61), ('1965년 3월 5일', 61),
            ('1965-12-31', 60),                     # 생일 전이면 한 살 적다
            ('1965-09-23', 61),                     # 생일 당일
            ('1965', 61), ('62세', 62), ('62', 62),
            ('45~54세', 49), ('45 ~ 54', 49), ('50대', 55), ('65세 이상', 70), ('25세 미만', 20),
        ]
        for text, expected in cases:
            self.assertEqual(sm.age_from_text(text, self.TODAY), expected, text)

    def test_age_from_text_gives_up_quietly(self):
        for text in (None, '', '   ', '모름', '비밀입니다', '1800-01-01', '2999', '999세'):
            self.assertIsNone(sm.age_from_text(text, self.TODAY), text)

    def test_parse_age_finds_the_question_by_its_wording(self):
        for title in ('생년월일', '10. 생년월일을 적어 주세요', '출생 연도', '만 나이', '연령대'):
            answers = full_survey()
            answers[title] = '1965-03-05'
            self.assertEqual(sm.parse_age(answers, self.TODAY), 61, title)
        self.assertIsNone(sm.parse_age(full_survey(), self.TODAY))
        for bad in (None, 'x', [], {'생년월일': None}, {'생년월일': ['1965-03-05']}):
            sm.parse_age(bad, self.TODAY)      # 예외를 내지 않는다

    def test_age_band_boundaries(self):
        self.assertIsNone(mm.age_band_for(24))          # 연구 대상(25세~) 밖이면 고정 범위
        self.assertIsNone(mm.age_band_for(None))
        self.assertIsNone(mm.age_band_for(True))
        self.assertEqual(mm.age_band_for(25).label, '25~34세')
        self.assertEqual(mm.age_band_for(54).label, '45~54세')
        self.assertEqual(mm.age_band_for(55).label, '55~64세')
        self.assertEqual(mm.age_band_for(83).label, '65세 이상')

    def test_same_rmssd_reads_differently_by_age(self):
        measurement = {'before': {'heart_rate': 72, 'rmssd_ms': 18.0, 'rr_count': 60}}

        no_age = mm.interpret_pre_measurement(measurement)
        old = mm.interpret_pre_measurement(measurement, age=61)
        young = mm.interpret_pre_measurement(measurement, age=30)

        def rmssd_row(result):
            return next(row for row in result.rows if row['key'] == 'rmssd_ms')

        self.assertEqual(rmssd_row(no_age)['verdict'], '낮은 편')
        self.assertEqual(rmssd_row(no_age)['reference'], '성인 안정 시 흔한 범위 20~50 ms')
        self.assertEqual(rmssd_row(old)['verdict'], '같은 나이대 중간 절반 안')
        self.assertIn('55~64세', rmssd_row(old)['reference'])
        self.assertEqual(rmssd_row(young)['verdict'], '같은 나이대보다 낮은 편')

    def test_age_only_moves_rmssd(self):
        measurement = {'before': {'heart_rate': 72, 'rmssd_ms': 18.0, 'sdnn_ms': 25.0, 'rr_count': 60,
                                  'sleep_index': 38.0}}
        old = mm.interpret_pre_measurement(measurement, age=61)
        no_age = mm.interpret_pre_measurement(measurement)
        for key in ('heart_rate', 'sdnn_ms', 'rr_count', 'sleep_index'):
            self.assertEqual(old.payload['categories'][key], no_age.payload['categories'][key], key)
            self.assertEqual(old.payload['reference_ranges'][key], no_age.payload['reference_ranges'][key], key)

    def test_payload_carries_the_band_name_only(self):
        result = mm.interpret_pre_measurement({'before': {'rmssd_ms': 18.0}}, age=61)
        self.assertEqual(result.payload['age_band'], '55~64세')
        self.assertIn('Voss', result.payload['age_band_note'])
        self.assertIn('누운 자세', result.note)
        # 나이대 이름만 가고 생년월일·나이 숫자는 가지 않는다.
        self.assertNotIn('age', [k for k in result.payload if k not in ('age_band', 'age_band_note')])
        json.dumps(result.payload)

        without = mm.interpret_pre_measurement({'before': {'rmssd_ms': 18.0}})
        self.assertIsNone(without.payload['age_band'])
        self.assertIsNone(without.payload['age_band_note'])
        self.assertNotIn('누운 자세', without.note)

    def test_bands_follow_the_published_order(self):
        medians = [band.median for band in mm.AGE_BANDS]
        self.assertEqual(medians, sorted(medians, reverse=True), '나이가 많을수록 낮아야 한다')
        for band in mm.AGE_BANDS:
            self.assertLess(band.low, band.median)
            self.assertLess(band.median, band.high)
