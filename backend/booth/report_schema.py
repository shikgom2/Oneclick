# -*- coding:utf-8 -*-
"""AI 체험 리포트의 JSON 구조.

REPORT_SCHEMA 는 Claude structured outputs(output_config.format = json_schema)에 그대로
넘긴다. structured outputs 는 JSON Schema 일부만 받는다(문서 기준):
  - 사용 가능: type, properties, required, items, enum, const, anyOf, allOf, description,
    additionalProperties: false (모든 object 에 필수)
  - 사용 불가: minimum/maximum, minLength/maxLength, pattern, 복잡한 배열 제약
minItems 는 SDK 의 스키마 변환기가 0/1 만 남기고 나머지를 버리므로 1 만 쓴다.
글자 수·중복 금지는 스키마로 강제할 수 없어 프롬프트 지시와 validate_report(strict=True)로 대신한다.

스키마가 형식을 보장해도 거절(refusal)·max_tokens 중단 시에는 출력이 스키마와 다를 수
있으므로, 저장 전에는 항상 validate_report 로 한 번 더 확인한다.
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


REPORT_SCHEMA = _obj({
    'headline': _obj({
        'one_liner': _str('리포트 전체를 요약하는 한 문장. 진단·효과 단정과 측정 숫자 없이.'),
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
            'meaning': _str('이 지표가 무엇을 뜻하는지 쉬운 말로.'),
            'reading': _str('이 지표를 읽는 방법(일반 설명). 측정·화면 숫자와 전·후 변화는 쓰지 않는다.'),
        }),
    },
    'tips': {
        'type': 'array',
        'minItems': 1,
        'description': '일상에서 바로 해볼 수 있는 수면·이완 팁 3~5개.',
        'items': _str('한두 문장의 실천 팁.'),
    },
    'closing': _str('마무리 인사 한두 문장.'),
})


# 페이지 미리보기·테스트에 쓰는 샘플(Claude 에는 보내지 않는다). 프롬프트 규칙(합쇼체와 높임,
# 체험자님 호칭, 1인칭·이모지 없음, 진단·자극 효과 단정 없음, 측정 숫자 없음)을 그대로 따르는
# 모범 형태로 유지한다. 코드를 고치는 사람이 이 예시를 기준으로 삼으므로 지표 설명의 방향이 틀리면 안 된다.
EXAMPLE_REPORT = {
    'headline': {
        'one_liner': '평소 잠드는 과정과 긴장을 푸는 습관을 차분히 돌아볼 좋은 기회입니다.',
        'detail': (
            '설문에서 체험자님은 잠들기까지 시간이 걸리는 날이 많다고 답하셨습니다. '
            '오늘 체험 화면의 값은 시연용 보정이 포함된 참고치라서, 수치의 오르내림보다는 '
            '평소 생활 리듬을 함께 살펴보는 데 의미가 있습니다.'
        ),
    },
    'survey_insights': [
        {
            'title': '잠들기까지 걸리는 시간',
            'body': (
                '잠자리에 누운 뒤 잠들기까지 30분 이상 걸린다고 답하셨습니다. '
                '잠들기 전 1시간 동안의 활동(휴대폰 사용, 늦은 업무 등)이 영향을 줄 수 있어 '
                '저녁 루틴을 점검해 보는 것이 도움이 됩니다.'
            ),
        },
        {
            'title': '낮 동안의 긴장감',
            'body': (
                '하루 중 긴장되거나 쫓기는 느낌이 자주 든다고 답하셨습니다. '
                '긴장이 저녁까지 이어지면 몸이 쉬는 상태로 넘어가는 데 시간이 더 걸릴 수 있습니다.'
            ),
        },
    ],
    'indices': [
        {
            'key': 'sleep_index',
            'meaning': (
                '심박변이도(심장 박동 간격이 조금씩 달라지는 정도) 중 RMSSD 를 바탕으로, '
                '몸이 얼마나 이완된 상태인지를 5~95 사이로 나타낸 값입니다. 높을수록 이완 쪽이며, '
                '깨어 있는 동안 잠깐 측정한 값이라 수면의 질을 뜻하지는 않습니다.'
            ),
            'reading': (
                '화면 표시값에는 시연용 보정이 포함되어 있어 개인의 상태 변화로 해석하지 않습니다. '
                '이완 정도를 숫자로 볼 수 있다는 점을 체험하는 참고치로 보시면 됩니다.'
            ),
        },
        {
            'key': 'autonomic_balance',
            'meaning': (
                '긴장 모드(교감신경)와 휴식 모드(부교감신경)의 균형을 나타낸 값입니다. '
                '두 모드의 비율이 균형에 가까울수록 높게 표시됩니다.'
            ),
            'reading': '짧은 측정에서는 자세나 호흡에 따라 쉽게 달라지므로 한 번의 값으로 판단하지 않습니다.',
        },
        {
            'key': 'stress_recovery_index',
            'meaning': (
                '심박수와 RMSSD 를 함께 반영해 긴장에서 회복된 정도를 나타낸 값입니다. '
                '높을수록 이완·회복 쪽이며, 일반적으로 심박수가 낮고 RMSSD 가 높을수록 높게 나옵니다.'
            ),
            'reading': '오늘 값은 체험용 참고치이며, 평소 상태를 알려면 같은 조건에서 여러 번 측정해야 합니다.',
        },
    ],
    'tips': [
        '잠들기 1시간 전에는 화면 밝기를 낮추거나 휴대폰을 멀리 두어 보세요.',
        '4초 들이마시고 6초 내쉬는 느린 호흡을 5분 정도 해 보시면 몸이 쉬는 상태로 넘어가는 데 도움이 됩니다.',
        '주말에도 일어나는 시간을 평일과 비슷하게 맞추면 수면 리듬이 안정되기 쉽습니다.',
    ],
    'closing': '오늘 체험이 체험자님의 휴식 습관을 돌아보는 계기가 되었기를 바랍니다. 편안한 밤 보내세요.',
}


class ReportInvalid(ValueError):
    """AI 출력이 리포트 구조와 맞지 않을 때. 메시지는 report_error 에 들어가므로 개인정보 없이 짧게."""


def _text(obj, key, where, strict=False):
    value = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(value, str):
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


def validate_report(data, strict=False):
    """AI 출력(dict)을 검사하고 스키마에 있는 키만 남긴 새 dict 를 돌려준다. 잘못되면 ReportInvalid.

    strict=True  생성 직후 저장 전(report.interpret_message). 지표 세 개가 정확히 한 번씩 있어야 하고
                 모든 문장이 비어 있지 않아야 한다. 어기면 이번 시도를 실패로 기록해 다시 생성한다.
    strict=False 저장된 리포트를 화면에 그릴 때. 구조만 확인하고, 중복 지표는 첫 항목만, 빈 팁은
                 빼고 그린다. 검증이 나중에 엄격해져도 이미 저장된 리포트가 '표시할 수 없음'으로
                 바뀌어 스태프 복구가 필요해지는 일을 막는다.
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

    tips = []
    for i, tip in enumerate(_list(data, 'tips')):
        if not isinstance(tip, str):
            raise ReportInvalid('tips[%d] 가 문자열이 아님' % i)
        tip = tip.strip()
        if not tip:
            if strict:
                raise ReportInvalid('tips[%d] 비어 있음' % i)
            continue
        tips.append(tip)
    if strict and not tips:
        raise ReportInvalid('tips 비어 있음')

    return {
        'headline': {
            'one_liner': _text(headline, 'one_liner', 'headline', strict),
            'detail': _text(headline, 'detail', 'headline', strict),
        },
        'survey_insights': insights,
        'indices': indices,
        'tips': tips,
        'closing': _text(data, 'closing', 'report', strict),
    }
