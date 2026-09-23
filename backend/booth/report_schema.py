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
        'detail': _str('한 줄 요약을 3~4문장으로 풀어 쓴 설명. 설문 지표와 체험 전 측정값의 핵심 숫자를 인용한다. '
                       '체험 후 값과 전·후 변화는 쓰지 않는다.'),
    }, description='리포트 맨 위 요약.'),
    'sleep_analysis': _str(
        'survey_metrics 를 바탕으로 한 수면 상태 풀이 5~8문장. 취침·기상 시각, 잠자리에 머문 시간, 총 수면 시간, '
        '수면 효율, 잠들기까지 걸린 시간, 밤중에 깬 횟수, 자기평가, 간이 수면 점수를 값과 판정과 함께 짚고 서로 잇는다. '
        '없는 항목은 없다고 짧게 밝힌다.'
    ),
    'survey_insights': {
        'type': 'array',
        'minItems': 1,
        'description': '설문 응답에 근거한 관찰 3~4개. 응답에 없는 내용은 지어내지 않는다.',
        'items': _obj({
            'title': _str('짧은 소제목.'),
            'body': _str('설문 응답을 근거로 한 3~5문장 설명. 응답 값을 인용하고 다른 자료와 잇는다.'),
        }),
    },
    'autonomic_analysis': _str(
        'pre_measurement 를 바탕으로 한 체험 전 측정값 풀이 5~8문장. 심박수·RMSSD·SDNN·검출 박동 수와 세 지표를 '
        '참고 범위·판정과 함께 읽고 설문 지표와 이어서 설명한다. 체험 전 약 60초 측정임을 밝히고, 체험 후 값·전·후 변화·'
        '자극 효과는 쓰지 않는다.'
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
            'meaning': _str('이 지표가 무엇을 뜻하는지 쉬운 말로 2~3문장.'),
            'reading': _str('이 지표를 읽는 방법 2~3문장(판정 구간, 짧은 측정의 한계). 전·후 변화는 쓰지 않는다.'),
            'personal_note': _str('체험자의 체험 전 값과 그 판정 구간을 인용해 설문 자료와 이어 주는 2~3문장.'),
        }),
    },
    'tips': _str_list('일상에서 바로 해볼 수 있는 수면·이완 팁 5~6개. 팁마다 체험자의 응답이나 측정값과 연결한다.',
                      '2~3문장의 실천 팁.'),
    'caveats': _str_list('리포트를 읽을 때 유의점 3~4개: 60초 측정의 한계, 자기 보고 설문의 한계, '
                         '화면 전·후 표시값의 시연용 보정, 진단이 아님.',
                         '한두 문장의 유의점.'),
    'closing': _str('마무리 인사 2~3문장.'),
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
            '설문으로 계산한 수면 지표에서 잠자리에 머문 시간은 약 8시간, 실제 수면은 약 6.5시간으로 수면 효율이 '
            '약 81%(양호 기준 85% 미만)였습니다. 잠들기까지 16~30분이 걸리고 밤중에 2회 깼다고 답하셔서, 잠드는 과정과 '
            '잠의 이어짐에 개선 여지가 보입니다. 체험 전 약 60초 동안 손가락 링으로 측정한 심박수는 분당 72회로 참고 범위'
            '(60~100) 안이었고, RMSSD 35.5ms 는 성인 안정 시 흔한 범위에 있었습니다. 아래에서 항목별로 자세히 살펴봅니다.'
        ),
    },
    'sleep_analysis': (
        '어젯밤 잠자리에 든 시각은 밤 11시~12시(약 23:30), 오늘 아침 잠자리에서 나온 시각은 아침 7시~8시(약 07:30)로 '
        '답하셔서 잠자리에 머문 시간은 약 8시간으로 추정됩니다. 그중 실제로 잠든 시간은 6~7시간(약 6.5시간)이라고 답하셔서, '
        '수면 효율(잠자리에 머문 시간 중 실제로 잔 시간의 비율)은 약 81%로 양호 기준인 85%에 조금 못 미칩니다. '
        '잠들기까지 16~30분이 걸렸다는 응답은 PSQI(수면의 질 설문) 구간에서 보통(1점)에 해당하고, 밤중에 2회 깼다는 응답은 '
        '부스 간이 기준으로 잦은 편(2점)입니다. 총 수면 시간 6~7시간은 조금 짧은 편(1점)이며, 네 항목을 더한 간이 수면 점수는 '
        '12점 만점에 5점으로 보통 범위입니다. 아침에 일어났을 때 조금 개운했다고 답하신 것과 어젯밤 잠 만족도를 보통으로 '
        '답하신 것은, 잠든 시간 자체보다 잠이 중간에 끊긴 것이 아침 느낌에 영향을 주었을 가능성을 시사합니다. 평소 취침 시각이 '
        '대체로 같다고 답하셔서 생활 리듬의 바탕은 갖추어져 있으므로, 잠드는 과정과 밤중 각성을 줄이는 쪽에 집중해 볼 만합니다. '
        '이 값들은 응답 구간의 중앙값으로 추정한 것이라 실제와 다를 수 있습니다.'
    ),
    'survey_insights': [
        {
            'title': '잠들기까지 걸리는 시간',
            'body': (
                '잠자리에 든 뒤 잠들기까지 16~30분이 걸린다고 답하셨습니다. 이 정도는 PSQI 구간에서 보통에 해당하지만, '
                '잠자리에 머문 8시간에 비해 실제 수면이 6.5시간인 점을 함께 보면 누워 있는 시간의 일부가 잠들지 못한 채 '
                '지나가고 있습니다. 잠들기 전 1시간 동안의 활동(휴대폰 화면, 늦은 업무, 카페인)이 잠드는 시간을 늘리는 흔한 '
                '요인이므로 저녁 루틴을 점검해 보실 만합니다. 누워서 20분 넘게 잠이 오지 않을 때 잠시 일어나 조용한 활동을 '
                '하다가 다시 눕는 방법도 도움이 됩니다.'
            ),
        },
        {
            'title': '밤중에 깨는 횟수',
            'body': (
                '밤중에 2회 깼다고 답하셨습니다. 부스 간이 기준으로는 잦은 편에 해당하며, 잠이 중간에 끊기면 총 수면 시간이 '
                '같아도 아침 개운함이 떨어지기 쉽습니다. 늦은 저녁의 수분·음주·카페인, 침실의 소음과 빛, 취침 전 걱정거리가 '
                '흔한 원인입니다. 깬 뒤 시계를 확인하지 않는 것만으로도 다시 잠드는 데 걸리는 시간이 줄어드는 경우가 많습니다.'
            ),
        },
        {
            'title': '아침 개운함과 잠 만족도',
            'body': (
                '오늘 아침 일어났을 때 조금 개운했고 어젯밤 잠에 대한 만족도는 보통이라고 답하셨습니다. 지금 이 순간은 약간 '
                '졸리다고 답하셔서 낮 동안의 맑은 정도도 완전히 회복되지는 않은 것으로 보입니다. 총 수면 시간이 6.5시간으로 '
                '조금 짧은 편인 점과 밤중 각성이 겹쳐 나타난 결과로 읽을 수 있습니다. 며칠 동안 같은 시각에 일어나며 수면 시간을 '
                '30분씩 늘려 보면 아침 느낌의 변화를 스스로 확인해 보실 수 있습니다.'
            ),
        },
        {
            'title': '취침 시각의 규칙성',
            'body': (
                '평소 취침 시각이 대체로 같다고 답하셨습니다. 규칙적인 취침·기상 시각은 몸의 생체 시계를 안정시켜 잠드는 시간을 '
                '줄이는 데 가장 기본이 되는 습관입니다. 이미 갖추신 이 습관을 유지하면서, 주말에도 기상 시각을 평일과 1시간 '
                '이내로 맞추면 그 바탕이 더 잘 유지됩니다.'
            ),
        },
    ],
    'autonomic_analysis': (
        '체험 전 약 60초 동안 손가락 링으로 측정한 심박수는 분당 72회로, 안정 시 참고 범위(60~100)의 가운데쯤에 있었습니다. '
        '심박변이도(심장 박동 간격이 조금씩 달라지는 정도) 중 RMSSD 는 35.5ms 로 성인이 안정 시 흔히 보이는 20~50ms 범위 '
        '안이었고, SDNN(심박 간격 전체의 흔들림 폭)은 48ms 로 60초 측정 기준 보통 범위였습니다. 검출된 박동 수가 300회로 '
        '충분해 이 값들은 링 접촉 면에서는 믿을 만합니다. 세 지표 중 Sleep Index 는 55, Autonomic Balance 는 48로 모두 중간 '
        '구간(40~69)에 있었고, Stress/Recovery Index 는 60으로 앱 등급 Warning 에 해당하는 중간 구간이었습니다. 심박수 '
        '점수와 RMSSD 점수의 평균인 Stress/Recovery Index 가 중간에 머문 것은, 심박수는 차분한 편이지만 RMSSD 가 아주 높은 '
        '편은 아니라는 뜻으로 읽을 수 있습니다. 설문에서 밤중 각성과 조금 짧은 수면이 보인 점을 함께 생각하면, 이완 쪽으로 더 '
        '기울 여지가 있는 상태로 볼 수 있습니다. 다만 60초 남짓의 한 번 측정이라 자세와 호흡, 부스의 소음에 따라 쉽게 달라지므로 '
        '참고용으로만 보시기 바랍니다.'
    ),
    'indices': [
        {
            'key': 'sleep_index',
            'meaning': (
                '심박변이도(심장 박동 간격이 조금씩 달라지는 정도) 중 RMSSD 를 바탕으로, 몸이 얼마나 이완된 상태인지를 '
                '5~95 사이로 나타낸 값입니다. RMSSD 5~80ms 를 그대로 환산한 것이라 높을수록 휴식 모드(부교감신경) 쪽입니다. '
                '깨어 있는 동안 잠깐 측정한 값이라 수면의 질을 뜻하지는 않습니다.'
            ),
            'reading': (
                '40 미만은 낮음, 40~69 는 중간, 70 이상은 높음으로 나눕니다. 짧은 측정에서는 호흡 속도만 바뀌어도 값이 '
                '움직이므로 한 번의 값으로 이완 능력을 판단하지 않습니다.'
            ),
            'personal_note': (
                '체험자님의 체험 전 Sleep Index 는 55로 중간 구간이었습니다. RMSSD 35.5ms 가 흔한 범위 안에 있었던 것과 같은 '
                '방향의 결과입니다. 잠들기까지 시간이 걸린다는 응답과 함께 보면, 잠들기 전 이완 연습이 어울리는 상태입니다.'
            ),
        },
        {
            'key': 'autonomic_balance',
            'meaning': (
                '긴장 모드(교감신경)와 휴식 모드(부교감신경)의 균형을 나타낸 값입니다. 두 모드의 비율(LF/HF)이 1.0 에 가까워 '
                '균형에 가까울수록 높게 표시되고, 어느 쪽으로든 치우칠수록 낮아집니다. 50 은 균형을 뜻하지 않으며, 비율을 '
                '계산하지 못했을 때 표시되는 기본값일 수도 있습니다.'
            ),
            'reading': (
                '40 미만은 낮음, 40~69 는 중간, 70 이상은 높음으로 나눕니다. 짧은 측정에서는 자세나 호흡에 따라 쉽게 '
                '달라지므로 한 번의 값으로 판단하지 않습니다.'
            ),
            'personal_note': (
                '체험자님의 체험 전 Autonomic Balance 는 48로 중간 구간이었습니다. 두 모드의 비율이 1.0 에서 어느 정도 떨어져 '
                '있었다는 뜻이며, 60초 측정에서는 흔히 보이는 값입니다. 검출 박동 수가 충분했으므로 기본값이 아니라 실제로 '
                '계산된 값으로 볼 수 있습니다.'
            ),
        },
        {
            'key': 'stress_recovery_index',
            'meaning': (
                '심박수 점수(55bpm=100, 90bpm=0)와 RMSSD 점수(20ms=0, 80ms=100)의 평균으로, 긴장에서 회복된 정도를 '
                '나타낸 값입니다. 높을수록 이완·회복 쪽이며, 심박수가 낮고 RMSSD 가 클수록 높게 나옵니다. 점수가 높다고 '
                '스트레스가 높다는 뜻이 아닙니다.'
            ),
            'reading': (
                '앱은 70 이상을 Good, 40~69 를 Warning, 40 미만을 Alert 로 표시합니다. Warning 은 진단이 아니라 중간 '
                '구간이라는 표시이며, 평소 상태를 알려면 같은 조건에서 여러 번 측정해야 합니다.'
            ),
            'personal_note': (
                '체험자님의 체험 전 Stress/Recovery Index 는 60으로 중간 구간(앱 등급 Warning)이었습니다. 심박수는 참고 '
                '범위 안에서 차분한 편이었지만 RMSSD 가 높은 편은 아니어서 평균이 중간에 머문 것으로 읽을 수 있습니다. '
                'RMSSD 는 느린 호흡 같은 이완 습관으로 올려 볼 여지가 있는 값입니다.'
            ),
        },
    ],
    'tips': [
        (
            '잠자리에 든 뒤 20분이 지나도 잠이 오지 않으면 잠시 일어나 어두운 곳에서 조용한 활동을 하다가 졸릴 때 다시 누워 '
            '보세요. 잠들기까지 16~30분이 걸린다고 답하신 체험자님께는 누워서 뒤척이는 시간을 줄이는 것이 수면 효율(약 81%)을 '
            '올리는 가장 직접적인 방법입니다.'
        ),
        (
            '잠들기 1시간 전에는 휴대폰 화면 밝기를 낮추거나 화면을 멀리 두어 보세요. 밝은 화면은 잠드는 시각을 늦추기 쉬워, '
            '잠들기까지의 시간과 밤중 각성 모두에 영향을 줄 수 있습니다.'
        ),
        (
            '4초 들이마시고 6초 내쉬는 느린 호흡을 잠자리에서 5분 정도 해 보세요. 체험 전 RMSSD 가 35.5ms 로 흔한 범위 '
            '안이었지만 높은 편은 아니었으므로, 이런 호흡은 휴식 모드를 돕는 습관으로 어울립니다.'
        ),
        (
            '저녁 늦은 시간의 수분 섭취와 카페인·술을 줄이고, 침실의 빛과 소음을 점검해 보세요. 밤중에 2회 깼다고 답하셨는데, '
            '이런 요인들이 밤중 각성의 흔한 원인입니다.'
        ),
        (
            '지금처럼 취침 시각을 일정하게 유지하되, 주말 기상 시각도 평일과 1시간 이내로 맞춰 보세요. 이미 규칙적인 취침 '
            '습관을 갖추셨으므로 기상 시각까지 맞추면 잠드는 시간이 더 짧아지기 쉽습니다.'
        ),
        (
            '총 수면 시간이 6~7시간으로 조금 짧은 편이므로, 잠자리에 드는 시각을 15~30분 앞당겨 보세요. 아침에 일어났을 때의 '
            '개운함을 며칠 동안 스스로 기록해 보시면 변화를 확인해 보실 수 있습니다.'
        ),
    ],
    'caveats': [
        (
            '체험 전 측정값은 약 60초 동안 한 번 잰 값이라 자세·호흡·움직임에 따라 쉽게 달라지며, 평소 상태를 알려면 같은 '
            '조건에서 여러 번 측정해야 합니다.'
        ),
        '설문 지표는 응답한 구간의 중앙값과 본인 기억으로 추정한 값이라 실제 수면과 다를 수 있습니다.',
        '체험 화면의 전·후 표시값에는 시연용 보정이 들어 있어, 전·후 차이를 개인의 변화나 자극의 효과로 볼 수 없습니다.',
        (
            '이 리포트는 체험용 참고 자료이며 의학적 진단이나 치료 권고가 아닙니다. 수면 문제가 계속되면 전문가와 상담해 '
            '보시기 바랍니다.'
        ),
    ],
    'closing': (
        '오늘 체험이 체험자님의 잠드는 과정과 밤중 각성을 돌아보는 계기가 되었기를 바랍니다. 규칙적인 취침 습관이라는 좋은 '
        '바탕을 이미 갖추셨으니, 작은 변화 하나씩 더해 보시기 바랍니다. 편안한 밤 보내세요.'
    ),
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
            'personal_note': _text(item, 'personal_note', where, strict, optional=True),
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
        'sleep_analysis': _text(data, 'sleep_analysis', 'report', strict, optional=True),
        'survey_insights': insights,
        'autonomic_analysis': _text(data, 'autonomic_analysis', 'report', strict, optional=True),
        'indices': indices,
        'tips': _string_items(data, 'tips', strict),
        'caveats': _string_items(data, 'caveats', strict, optional=True),
        'closing': _text(data, 'closing', 'report', strict),
    }
