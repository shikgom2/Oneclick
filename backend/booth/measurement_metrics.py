# -*- coding:utf-8 -*-
"""체험 전 측정값(measurement['before'])의 화면 표 행과 AI 프롬프트용 해석(순수 함수, Django 없음).

표와 AI 프롬프트에는 체험 '전' 값만 쓴다. 체험 후 값과 화면 표시값(displayed, 시연용 보정 포함)은 AI 프롬프트에
넣지 않는다. 전·후 차이를 자극 효과처럼 읽게 하지 않기 위해서다(report 모듈 docstring).
예외는 화면 '지표 이해하기'의 지표별 '내 값' 줄(describe_own_values) 하나다. 위의 전·후 비교표와 같은 값에 구간
이름만 서버가 붙이고, 값의 출처와 '효과로 볼 수 없음'을 함께 알린다. 이 값도 AI 에게는 가지 않는다.

판정 문구는 '참고 범위 안/밖, 낮은 편/높은 편, 낮음/중간/높음' 같은 서술만 쓴다. '정상/비정상' 같은 진단 표현은
쓰지 않는다. 참고 범위는 일반적인 안정 시 범위이고, 60초 남짓의 초단시간 측정이라 참고용이라는 점을 표 아래에 밝힌다.
표의 판정과 AI 가 받는 분류(payload['categories'])가 같은 함수에서 나오므로 리포트 문장과 표가 어긋나지 않는다.
"""
from collections import namedtuple

PreMeasurement = namedtuple('PreMeasurement', ['rows', 'payload', 'note'])

METHOD_NOTE = (
    '체험 전 약 60초 동안 손가락에 낀 링(PPG)으로 측정한 값입니다. 짧은 측정이라 자세·호흡·움직임에 따라 쉽게 달라지며, '
    '참고 범위는 일반적인 안정 시 범위일 뿐 진단 기준이 아닙니다.'
)

NOT_MEASURED = '측정되지 않음'
NOT_MEASURED_VERDICT = '링 접촉 부족으로 계산 불가'
RR_COUNT_SUFFICIENT = 40            # 60초 측정에서 이보다 적게 검출되면 접촉 부족으로 본다

# 화면 표의 참고 범위 문구. report._INDEX_GUIDE 의 구간 설명과 같은 숫자를 쓴다.
REFERENCES = {
    'heart_rate': '안정 시 60~100 bpm',
    'rmssd_ms': '성인 안정 시 흔한 범위 20~50 ms',   # 나이를 모를 때만 쓴다(rmssd_reference 참고)
    'sdnn_ms': '60초 측정 기준 30~60 ms',
    'rr_count': '60초에 %d회 이상이면 충분' % RR_COUNT_SUFFICIENT,
    'sleep_index': '40 미만 낮음 · 40~69 중간 · 70 이상 높음',
    'autonomic_balance': '40 미만 낮음 · 40~69 중간 · 70 이상 높음',
    'stress_recovery_index': '70 이상 Good · 40~69 Warning · 40 미만 Alert (앱 등급)',
}

# 나이대별 RMSSD 참고값.
#
# 출처: Voss A, Schroeder R, Heitmann A, Peters A, Perz S. Short-Term Heart Rate Variability -
# Influence of Gender and Age in Healthy Subjects. PLoS ONE 10(3):e0118308 (2015).
# 건강한 성인 1,906명(25~74세)을 누운 자세로 5분간 잰 심전도이고, 숫자는 부록 S1 의 중앙값·사분위수다.
# 남녀 표를 합쳤다: median 은 두 중앙값을 인원수로 가중한 값, low~high 는 남녀 각각의 1~3사분위 구간을
# 모두 덮는 범위(중간 절반)다.
#
# 고정 범위 하나(20~50 ms)만 쓰면 나이 든 참가자가 거의 늘 '낮은 편'이 된다. RMSSD 는 나이가 들수록
# 뚜렷하게 낮아지기 때문이다(25~34세 가운데값 37 ms vs 65세 이상 17 ms).
AgeBand = namedtuple('AgeBand', ['label', 'min_age', 'max_age', 'median', 'low', 'high'])

AGE_BANDS = (
    AgeBand('25~34세', 25, 34, 37, 26, 55),
    AgeBand('35~44세', 35, 44, 30, 21, 46),
    AgeBand('45~54세', 45, 54, 22, 16, 31),
    AgeBand('55~64세', 55, 64, 18, 12, 27),
    # 연구 대상은 74세까지다. 75세 이상은 가장 가까운 이 구간을 쓴다(아래 안내에 대상 나이를 밝힌다).
    AgeBand('65세 이상', 65, 200, 17, 11, 24),
)

AGE_REFERENCE_NOTE = (
    'RMSSD 의 나이대 참고값은 건강한 성인 1,906명(25~74세)을 누운 자세로 5분간 측정한 연구(Voss 2015)의 '
    '중앙값과 중간 절반 구간입니다. 이 체험은 앉은 자세에서 60초만 재기 때문에 대체로 더 낮게 나오며, '
    '중간 절반은 같은 나이대의 절반이 들어가는 구간일 뿐이라 그 밖이라고 문제라는 뜻이 아닙니다.'
)


def age_band_for(age):
    """만 나이 -> AgeBand. 나이를 모르거나 25세 미만이면 None(고정 범위를 쓴다)."""
    if isinstance(age, bool) or not isinstance(age, int):
        return None
    for band in AGE_BANDS:
        if band.min_age <= age <= band.max_age:
            return band
    return None


def rmssd_reference(age_band):
    """표의 '참고 범위' 칸 문구. 나이대를 모르면 일반 성인 범위."""
    if age_band is None:
        return REFERENCES['rmssd_ms']
    return '%s 가운데값 %d ms · 중간 절반 %d~%d ms' % (
        age_band.label, age_band.median, age_band.low, age_band.high)


# (키, 표 이름, 우리말 풀이, 단위)
ROWS = (
    ('heart_rate', '심박수', '', 'bpm'),
    ('rmssd_ms', 'RMSSD', '심박 간격 변동', 'ms'),
    ('sdnn_ms', 'SDNN', '심박 간격 흔들림 폭', 'ms'),
    ('rr_count', '검출 박동 수', '측정에 쓰인 박동', '회'),
    ('sleep_index', 'Sleep Index', '이완 지표', ''),
    ('autonomic_balance', 'Autonomic Balance', '자율신경 균형', ''),
    ('stress_recovery_index', 'Stress/Recovery Index', '회복 지표', ''),
)

_INDEX_KEYS = ('sleep_index', 'autonomic_balance', 'stress_recovery_index')


def format_number(value):
    """정수는 그대로, 실수는 소수 첫째 자리까지(.0 은 뗀다). 숫자가 아니면 None."""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int):
        return str(value)
    if value != value or value in (float('inf'), float('-inf')):
        return None
    rounded = round(value, 1)
    return str(int(rounded)) if rounded.is_integer() else '%.1f' % rounded


def _inclusive_band(value, low, high, labels):
    """value < low -> labels[0], low <= value <= high -> labels[1], value > high -> labels[2]."""
    if value < low:
        return labels[0]
    if value > high:
        return labels[2]
    return labels[1]


def _threshold_band(value, low, high, labels):
    """value < low -> labels[0], low <= value < high -> labels[1], value >= high -> labels[2]."""
    if value < low:
        return labels[0]
    if value >= high:
        return labels[2]
    return labels[1]


def categorize(key, value, age_band=None):
    """측정값 하나의 판정 문구. None 이면 '링 접촉 부족으로 계산 불가'.

    age_band 를 주면 RMSSD 만 그 나이대 기준으로 판정한다. 다른 지표는 나이와 무관하게 그대로다.
    """
    if value is None:
        return NOT_MEASURED_VERDICT
    if key == 'heart_rate':
        return _inclusive_band(value, 60, 100, ('낮은 편', '참고 범위 안', '높은 편'))
    if key == 'rmssd_ms':
        if age_band is not None:
            return _inclusive_band(value, age_band.low, age_band.high,
                                   ('같은 나이대보다 낮은 편', '같은 나이대 중간 절반 안', '같은 나이대보다 높은 편'))
        return _inclusive_band(value, 20, 50, ('낮은 편', '흔한 범위 안', '높은 편'))
    if key == 'sdnn_ms':
        return _inclusive_band(value, 30, 60, ('낮은 편', '보통', '높은 편'))
    if key == 'rr_count':
        return '충분' if value >= RR_COUNT_SUFFICIENT else '접촉 부족으로 신뢰도 낮음'
    if key in ('sleep_index', 'autonomic_balance'):
        return _threshold_band(value, 40, 70, ('낮음', '중간', '높음'))
    if key == 'stress_recovery_index':
        return _threshold_band(value, 40, 70, ('낮음 (앱 등급 Alert)', '중간 (앱 등급 Warning)', '높음 (앱 등급 Good)'))
    return '-'


# '지표 이해하기'의 지표별 '내 값' 줄 아래에 한 번만 붙는 안내. 전·후 값을 나란히 보여 주면 차이를 효과로
# 읽기 쉬우므로, 값의 출처(화면 표시값이면 시연용 보정 포함)와 효과로 볼 수 없다는 점을 항상 함께 알린다.
OWN_VALUES_NOTE_DISPLAYED = (
    '위 \'내 값\'은 체험 화면에 표시된 값이며 시연을 위한 보정이 들어 있습니다. '
    '전·후 차이를 개인의 변화나 자극의 효과로 볼 수 없습니다.'
)
OWN_VALUES_NOTE_RAW = (
    '위 \'내 값\'은 짧은 체험 중 한 번씩 측정한 값이라 자세·호흡에 따라 쉽게 달라집니다. '
    '전·후 차이를 개인의 변화나 자극의 효과로 볼 수 없습니다.'
)


def describe_own_values(key, before, after):
    """지표 하나의 체험 전·후 값과 구간 판정. 화면 '지표 이해하기'에서 지표마다 한 줄로 보여 준다.

    값은 위의 전·후 비교표와 같은 것(services.display_sides)을 받아야 한다. 표와 다른 숫자가 나오면 체험자가
    헷갈린다. AI 에게는 넘기지 않는다: 체험 후 화면 표시값에는 시연용 보정이 들어 있어, AI 가 그 숫자를 개인의
    결과처럼 풀어 쓰게 하지 않기 위해서다. 그래서 여기서는 숫자와 구간 이름만 서버가 기계적으로 붙인다.
    값이 없으면 '측정되지 않음'으로 두고 구간은 비운다.
    """
    def one(value):
        number = _number(value)
        text = format_number(number)
        if text is None:
            return {'value': NOT_MEASURED, 'band': ''}
        return {'value': text, 'band': categorize(key, number)}

    return {'before': one(before), 'after': one(after)}


def _before_side(measurement):
    if not isinstance(measurement, dict):
        return {}
    before = measurement.get('before')
    return before if isinstance(before, dict) else {}


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def interpret_pre_measurement(measurement, age=None):
    """저장된 measurement -> PreMeasurement(rows, payload, note). 어떤 입력에도 예외를 내지 않는다.

    rows: 표 행 {key, label, gloss, value, reference, verdict} 7개(순서 고정). 값이 없으면 '측정되지 않음'.
    payload: AI 프롬프트용 dict(측정 방법, 숫자, 분류, 참고 범위, 빠진 항목, 검출 박동 수에 따른 신뢰도).
    note: 표 아래에 두는 측정 방법·한계 문구.

    age(만 나이)를 주면 RMSSD 의 참고 범위와 판정만 그 나이대 기준으로 바뀐다. 나이는 설문에서 얻고
    (survey_metrics.parse_age), 없으면 지금까지와 똑같이 고정 범위를 쓴다.
    """
    age_band = age_band_for(age)
    references = dict(REFERENCES)
    references['rmssd_ms'] = rmssd_reference(age_band)
    before = _before_side(measurement)
    values = {key: _number(before.get(key)) for key, _, _, _ in ROWS}
    hrv_missing = values['rmssd_ms'] is None

    rows = []
    categories = {}
    for key, label, gloss, unit in ROWS:
        value = values[key]
        verdict = categorize(key, value, age_band if key == 'rmssd_ms' else None)
        if value is not None and key in _INDEX_KEYS and hrv_missing:
            # 링 접촉이 나빠 RMSSD 를 못 구했는데 지표만 있으면 앱의 기본값(22, 50, 50)일 수 있다.
            verdict += ' (HRV 계산 실패 시 앱 기본값일 수 있음)'
        categories[key] = verdict
        text = format_number(value)
        rows.append({
            'key': key, 'label': label, 'gloss': gloss,
            'value': (text + (' ' + unit if unit and unit != '회' else unit)) if text is not None else NOT_MEASURED,
            'reference': references[key],
            'verdict': verdict,
        })

    rr_count = values['rr_count']
    if rr_count is None:
        reliability = '검출 박동 수를 받지 못해 신뢰도를 판단할 수 없다.'
    elif rr_count >= RR_COUNT_SUFFICIENT:
        reliability = '검출 박동 수 %d회로 60초 측정으로는 충분하다.' % rr_count
    else:
        reliability = '검출 박동 수가 %d회로 적어(%d회 미만) 링 접촉 부족이 의심되며 값의 신뢰도가 낮다.' % (
            rr_count, RR_COUNT_SUFFICIENT)

    payload = {
        'method': (
            '체험 전(자극 전)에 손가락 링(PPG)으로 약 60초 동안 한 번 측정한 값이다. 초단시간 측정이라 참고용이며, '
            '참고 범위는 일반적인 안정 시 범위이고 진단 기준이 아니다. 체험 후 값은 이 자료에 없다.'
        ),
        'values': {
            'heart_rate_bpm': values['heart_rate'],
            'rmssd_ms': values['rmssd_ms'],
            'sdnn_ms': values['sdnn_ms'],
            'rr_count': values['rr_count'],
            'sleep_index': values['sleep_index'],
            'autonomic_balance': values['autonomic_balance'],
            'stress_recovery_index': values['stress_recovery_index'],
        },
        'categories': categories,
        'reference_ranges': references,
        # 나이를 모르면 None. AI 에는 나이대 이름만 가고 생년월일·나이 숫자는 가지 않는다.
        'age_band': age_band.label if age_band else None,
        'age_band_note': AGE_REFERENCE_NOTE if age_band else None,
        'missing': [label for key, label, _, _ in ROWS if values[key] is None],
        'reliability': reliability,
    }
    note = METHOD_NOTE + (' ' + AGE_REFERENCE_NOTE if age_band else '')
    return PreMeasurement(rows, payload, note)
