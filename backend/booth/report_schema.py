# -*- coding:utf-8 -*-
"""AI 체험 리포트의 JSON 구조.

REPORT_SCHEMA 는 Claude structured outputs(output_config.format = json_schema)에 그대로
넘긴다. structured outputs 는 JSON Schema 일부만 받는다(문서 기준):
  - 사용 가능: type, properties, required, items, enum, const, anyOf, allOf, description,
    additionalProperties: false (모든 object 에 필수)
  - 사용 불가: minimum/maximum, minLength/maxLength, pattern, 복잡한 배열 제약
minItems 는 SDK 의 스키마 변환기가 0/1 만 남기고 나머지를 버리므로 1 만 쓴다.
글자 수·중복 금지는 스키마로 강제할 수 없어 프롬프트 지시와 validate_report(strict=True)로 대신한다.

속성 순서는 화면 순서와 같다. 모델은 대체로 속성 순서대로 써 내려가므로, 설문 풀이 -> 측정 풀이 -> 지표 -> 팁 순으로
앞에서 인용한 값을 뒤에서 이어 쓰기 쉽다.

스키마가 형식을 보장해도 거절(refusal)·max_tokens 중단 시에는 출력이 스키마와 다를 수
있으므로, 저장 전에는 항상 validate_report 로 한 번 더 확인한다.

sleep_analysis, autonomic_analysis, indices[].personal_note, caveats 는 나중에 더한 칸이다. 생성 시(strict)에는
필수이고, 이미 저장된 예전 리포트를 그릴 때(느슨한 검사)는 없어도 되며 화면은 그 칸만 건너뛴다.
"""

INDEX_KEYS = ('sleep_index', 'autonomic_balance', 'stress_recovery_index')

# 화면 표기용 지표 이름. 체험 태블릿 화면과 같은 영문 표기를 쓴다.
INDEX_LABELS = {
    'sleep_index': 'Sleep Index',
    'autonomic_balance': 'Autonomic Balance',
    'stress_recovery_index': 'Stress/Recovery Index',
}

# 영문 이름만으로는 무엇인지 알기 어려운 관람객을 위한 짧은 우리말 풀이(표 행·지표 제목 옆에 작게).
INDEX_GLOSSES = {
    'sleep_index': '이완 지표',
    'autonomic_balance': '자율신경 균형',
    'stress_recovery_index': '회복 지표',
}


def _obj(properties, description=None):
    """모든 속성을 required 로, additionalProperties 는 false 로 고정한 object 스키마."""
    schema = {
        'type': 'object',
        'properties': properties,
        'required': list(properties.keys()),
        'additionalProperties': False,
    }
    if description:
        schema['description'] = description
    return schema


def _str(description):
    return {'type': 'string', 'description': description}


def _str_list(description, item_description):
    return {'type': 'array', 'minItems': 1, 'description': description, 'items': _str(item_description)}


REPORT_SCHEMA = _obj({
    'headline': _obj({
        'one_liner': _str('리포트 전체를 요약하는 한 문장. 진단·효과 단정 없이.'),
        'detail': _str('한 줄 요약을 2~3문장으로 풀어 쓴 설명. 측정 숫자와 전·후 변화는 쓰지 않는다.'),
    }, description='리포트 맨 위 요약.'),
    'survey_insights': {
        'type': 'array',
        'minItems': 1,
        'description': '설문 응답에 근거한 관찰 2~4개. 응답에 없는 내용은 지어내지 않는다.',
        'items': _obj({
            'title': _str('짧은 소제목.'),
            'body': _str('설문 응답을 근거로 한 2~3문장 설명.'),
        }),
    },
    'autonomic_analysis': _str(
        'pre_measurement 를 바탕으로 한 체험 전 측정값 풀이 3~4문장. 리포트에서 숫자를 인용하는 칸은 여기 하나뿐이다. '
        '심박수와 RMSSD 를 참고 범위·판정과 함께 읽고, 나이대 기준(age_band)이 있으면 같은 나이대와 견준다. '
        '체험 전 약 60초 측정임을 밝히고, 체험 후 값·전·후 변화·자극 효과는 쓰지 않는다.'
    ),
    'indices': {
        'type': 'array',
        'minItems': 1,
        'description': '세 지표 각각에 대한 설명. 지표마다 정확히 한 항목.',
        'items': _obj({
            'key': {
                'type': 'string',
                'enum': list(INDEX_KEYS),
                'description': '지표 식별자.',
            },
            'meaning': _str('이 지표가 무엇을 뜻하는지 쉬운 말로 1~2문장.'),
            'reading': _str('이 지표를 읽는 방법 1~2문장(판정 구간, 짧은 측정의 한계). 전·후 변화는 쓰지 않는다.'),
        }),
    },
    'tips': _str_list('일상에서 바로 해볼 수 있는 수면·이완 팁 3~5개.', '한두 문장의 실천 팁.'),
    'closing': _str('마무리 인사 한두 문장.'),
})


# 페이지 미리보기·테스트에 쓰는 샘플(Claude 에는 보내지 않는다). 프롬프트 규칙(합쇼체와 높임, 체험자님 호칭,
# 1인칭·이모지 없음, 진단·자극 효과 단정 없음, 체험 전 값만 인용하고 체험 후 값·전·후 변화 없음, 구체적인 문장)을
# 그대로 따르는 모범 형태로 유지한다. 코드를 고치는 사람이 이 예시를 기준으로 삼으므로 지표 설명의 방향이 틀리면 안 된다.
# 숫자는 tests 의 measurement_payload()(체험 전 심박수 72, RMSSD 35.5, SDNN 48, 지표 55/48/60, 박동 300회)와
# 설문 예시(취침 23:30, 기상 07:30, 실제 수면 6~7시간, 잠들기 16~30분, 2회 각성)에 맞춰 두었다.
EXAMPLE_REPORT = {
    'headline': {
        'one_liner': '잠자리에 머문 시간에 비해 실제로 잠든 시간이 짧은 편이라, 잠드는 과정을 먼저 돌아볼 만합니다.',
        'detail': (
            '잠자리에 머문 시간에 비해 실제로 주무신 시간이 짧아, 잠드는 과정과 밤중에 잠이 끊기는 부분에 눈길이 갑니다. '
            '아침에 조금 개운했다고 답하신 것과도 이어지는 모습입니다. 아래 표와 설명으로 항목별로 살펴보겠습니다.'
        ),
    },
    'survey_insights': [
        {
            'title': '잠드는 데 걸리는 시간',
            'body': (
                '잠자리에 든 뒤 잠들기까지 16~30분이 걸린다고 답하셨습니다. 누워서 뒤척이는 시간이 길어지면 잠자리가 '
                '깨어 있는 공간으로 기억되기 쉬워, 잠드는 과정 자체를 다듬어 볼 만합니다.'
            ),
        },
        {
            'title': '밤중에 깨는 횟수',
            'body': (
                '밤중에 2회 깼다고 답하셔서 잠이 한 번에 이어지지 않는 편입니다. 아침 개운함을 보통으로 답하신 것과 '
                '함께 보면, 잠의 길이보다 이어짐이 아침 느낌에 더 영향을 주었을 수 있습니다.'
            ),
        },
        {
            'title': '취침 시각의 규칙성',
            'body': (
                '평소 취침 시각이 대체로 같다고 답하셨습니다. 생활 리듬의 바탕은 갖추어져 있으므로, 잠드는 과정과 '
                '밤중 각성을 줄이는 쪽에 집중하시면 효과를 보기 쉽습니다.'
            ),
        },
    ],
    'autonomic_analysis': (
        '체험 전 약 60초 동안 손가락 링으로 측정한 심박수는 분당 72회로 안정 시 참고 범위(60~100) 안이었습니다. '
        '심박변이도(심장 박동 간격이 조금씩 달라지는 정도) 중 RMSSD 는 35.5ms 로 같은 나이대에서 흔한 범위 안이었고, '
        '검출된 박동 수도 충분해 접촉 면에서는 믿을 만한 값입니다. 다만 앉은 자세에서 60초 남짓 한 번 잰 값이라 '
        '자세와 호흡, 부스의 소음에 따라 쉽게 달라지므로 참고용으로만 보시기 바랍니다.'
    ),
    'indices': [
        {
            'key': 'sleep_index',
            'meaning': (
                '휴식 모드(부교감신경) 쪽으로 얼마나 기울어 있는지를 5~95 사이로 나타낸 값입니다. 깨어 있는 동안 잠깐 '
                '측정한 값이라 수면의 질을 뜻하지는 않습니다.'
            ),
            'reading': (
                '40 미만은 낮음, 40~69 는 중간, 70 이상은 높음으로 나눕니다. 짧은 측정에서는 호흡 속도만 바뀌어도 값이 '
                '움직이므로 한 번의 값으로 판단하지 않습니다.'
            ),
        },
        {
            'key': 'autonomic_balance',
            'meaning': (
                '긴장 모드(교감신경)와 휴식 모드(부교감신경)의 비율이 1.0 에 가까워 균형에 가까울수록 높게 표시되는 '
                '값입니다. 50 은 균형을 뜻하지 않으며, 비율을 계산하지 못했을 때의 기본값일 수도 있습니다.'
            ),
            'reading': (
                '40 미만은 낮음, 40~69 는 중간, 70 이상은 높음으로 나눕니다. 자세나 호흡에 따라 쉽게 달라지므로 한 번의 '
                '값으로 판단하지 않습니다.'
            ),
        },
        {
            'key': 'stress_recovery_index',
            'meaning': (
                '심박수 점수와 RMSSD 점수의 평균으로, 긴장에서 회복된 정도를 나타낸 값입니다. 점수가 높다고 스트레스가 '
                '높다는 뜻이 아닙니다.'
            ),
            'reading': (
                '앱은 70 이상을 Good, 40~69 를 Warning, 40 미만을 Alert 로 표시합니다. Warning 은 진단이 아니라 중간 '
                '구간이라는 표시입니다.'
            ),
        },
    ],
    'tips': [
        (
            '잠자리에 든 뒤 20분이 지나도 잠이 오지 않으면 잠시 일어나 어두운 곳에서 조용히 계시다가 졸릴 때 다시 '
            '누워 보세요.'
        ),
        '잠들기 전 10분 동안 숨을 4초 들이쉬고 6초 내쉬는 느린 호흡을 해 보세요. 잠드는 시간을 줄이는 데 도움이 됩니다.',
        '밤중에 깨더라도 시계를 보지 마세요. 시간을 확인하면 다시 잠들기까지 더 걸리는 경우가 많습니다.',
        '잠자리에 드는 시각을 15~30분 앞당겨 보시고, 아침에 일어났을 때의 개운함을 며칠 동안 기록해 보세요.',
    ],
    'closing': '오늘 살펴본 내용 중 하나만 골라 이번 주에 해 보셔도 충분합니다. 편안한 밤 되시기 바랍니다.',
}


class ReportInvalid(ValueError):
    """AI 출력이 리포트 구조와 맞지 않을 때. 메시지는 report_error 에 들어가므로 개인정보 없이 짧게."""


def _text(obj, key, where, strict=False, optional=False):
    """문자열 칸 하나. optional 은 예전 리포트에 없는 새 칸: 느슨한 검사에서는 없어도 '' 로 두고, 엄격 검사에서는 필수."""
    value = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(value, str):
        if optional and not strict:
            return ''
        raise ReportInvalid('%s.%s 누락' % (where, key))
    value = value.strip()
    if strict and not value:
        raise ReportInvalid('%s.%s 비어 있음' % (where, key))
    return value


def _list(obj, key):
    value = obj.get(key)
    if not isinstance(value, list):
        raise ReportInvalid('%s 누락' % key)
    return value


def _string_items(data, key, strict=False, optional=False):
    """문자열 목록 칸(tips, caveats). 느슨한 검사에서는 빈 항목을 빼고, optional 이면 칸이 없어도 [] 로 둔다."""
    value = data.get(key)
    if not isinstance(value, list):
        if optional and not strict:
            return []
        raise ReportInvalid('%s 누락' % key)
    items = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise ReportInvalid('%s[%d] 가 문자열이 아님' % (key, i))
        item = item.strip()
        if not item:
            if strict:
                raise ReportInvalid('%s[%d] 비어 있음' % (key, i))
            continue
        items.append(item)
    if strict and not items:
        raise ReportInvalid('%s 비어 있음' % key)
    return items


def validate_report(data, strict=False):
    """AI 출력(dict)을 검사하고 스키마에 있는 키만 남긴 새 dict 를 돌려준다. 잘못되면 ReportInvalid.

    strict=True  생성 직후 저장 전(report.interpret_message). 지표 세 개가 정확히 한 번씩 있어야 하고
                 모든 문장(새 칸 포함)이 비어 있지 않아야 한다. 어기면 이번 시도를 실패로 기록해 다시 생성한다.
    strict=False 저장된 리포트를 화면에 그릴 때. 구조만 확인하고, 중복 지표는 첫 항목만, 빈 팁·유의점은
                 빼고 그린다. 나중에 더한 칸(sleep_analysis, autonomic_analysis, personal_note, caveats)이 없는
                 예전 리포트는 그 칸을 ''/[] 로 돌려주어 화면이 건너뛴다. 검증이 나중에 엄격해져도 이미 저장된
                 리포트가 '표시할 수 없음'으로 바뀌어 스태프 복구가 필요해지는 일을 막기 위해서다.
    지표는 두 경우 모두 INDEX_KEYS 순서로 돌려준다. 페이지는 이 함수를 통과한 구조만 렌더링한다.
    """
    if not isinstance(data, dict):
        raise ReportInvalid('최상위가 객체가 아님')

    headline = data.get('headline')
    if not isinstance(headline, dict):
        raise ReportInvalid('headline 누락')

    insights = []
    for i, item in enumerate(_list(data, 'survey_insights')):
        where = 'survey_insights[%d]' % i
        insights.append({
            'title': _text(item, 'title', where, strict),
            'body': _text(item, 'body', where, strict),
        })
    if strict and not insights:
        raise ReportInvalid('survey_insights 비어 있음')

    by_key = {}
    for i, item in enumerate(_list(data, 'indices')):
        where = 'indices[%d]' % i
        key = _text(item, 'key', where)
        if key not in INDEX_KEYS:
            raise ReportInvalid('%s.key 값이 알 수 없는 지표' % where)
        if key in by_key:
            if strict:
                raise ReportInvalid('indices 에 %s 가 두 번 있음' % key)
            continue
        by_key[key] = {
            'key': key,
            'meaning': _text(item, 'meaning', where, strict),
            'reading': _text(item, 'reading', where, strict),
        }
    if strict:
        missing = [key for key in INDEX_KEYS if key not in by_key]
        if missing:
            raise ReportInvalid('indices 에 %s 없음' % ', '.join(missing))
    indices = [by_key[key] for key in INDEX_KEYS if key in by_key]

    return {
        'headline': {
            'one_liner': _text(headline, 'one_liner', 'headline', strict),
            'detail': _text(headline, 'detail', 'headline', strict),
        },
        'survey_insights': insights,
        'autonomic_analysis': _text(data, 'autonomic_analysis', 'report', strict, optional=True),
        'indices': indices,
        'tips': _string_items(data, 'tips', strict),
        'closing': _text(data, 'closing', 'report', strict),
    }
