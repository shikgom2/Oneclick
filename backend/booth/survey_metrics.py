# -*- coding:utf-8 -*-
"""설문 응답으로 계산하는 수면 지표(순수 함수, Django 없음).

구글 폼의 수면 문항(취침·기상 시각, 잠들기까지 걸린 시간, 총 수면 시간, 밤중에 깬 횟수, 5단계 자기평가)을
제목의 핵심어로 찾아, 보기 문구를 숫자로 바꾸고 PSQI 방식의 구성 점수와 간이 수면 점수를 계산한다.

- 문항 제목은 앞의 번호("1.", "①")와 공백을 지운 뒤 핵심어로 찾는다. 폼에서 제목을 조금 고쳐도 걸리게 하되,
  두 문항에 같은 낱말이 들어가는 경우('개운': 아침 개운함과 지금 개운함)는 다른 핵심어를 함께 요구한다.
- 보기 문구는 정확한 문자열이 아니라 숫자를 정규식으로 읽는다("밤 11시 ~ 12시", "16 ~ 30분"). 폼 보기의 띄어쓰기나
  기호가 바뀌어도 동작한다. 구간은 중앙값으로, '이전/이후/미만/이상'은 경계 ± 30분(시각)·± 0.5시간(수면 시간)으로 대표한다.
- 어떤 입력에도 예외를 내지 않는다. 읽을 수 없는 응답은 그 지표만 None 으로 둔다. 리포트 화면은 이 결과가 없어도
  나머지를 그려야 하고, 리포트 생성이 설문 문구 하나 때문에 실패해서는 안 된다.
- 결과는 화면 표 행(rows)과 AI 프롬프트용 dict(payload) 두 가지다. 표의 판정 문구와 AI 가 받는 분류가 같은 함수에서
  나오므로 리포트 문장과 표가 어긋나지 않는다.
"""
import re
from collections import namedtuple
from datetime import date

SurveyMetrics = namedtuple('SurveyMetrics', ['rows', 'score', 'payload'])

# 설문 지표는 응답 구간의 중앙값과 자기 보고로 추정한 값이다. 표와 프롬프트 양쪽에 같은 문구를 쓴다.
ESTIMATE_NOTE = (
    '값은 응답한 구간의 중앙값으로 추정한 것이라 실제와 다를 수 있습니다. 판정은 수면의 질 설문(PSQI)의 구간을 '
    '참고한 것이며, 밤중에 깬 횟수는 부스용으로 단순화한 기준입니다. 진단 기준이 아닙니다.'
)

SCORE_MAX = 12
SCORE_COMPONENTS = ('latency', 'duration', 'efficiency', 'awakenings')
SCORE_MIN_COMPONENTS = 3            # 이보다 적으면 간이 점수를 '계산 불가'로 둔다

# --- 문항 찾기 ---------------------------------------------------------------
# (지표 키, 핵심어 묶음들(한 묶음의 낱말이 모두 있으면 일치), 있으면 제외할 낱말). 위에서부터 차례로 찾고,
# 한 번 쓰인 문항은 다시 쓰지 않는다. 구체적인 핵심어를 가진 문항을 먼저 두어 '잠자리에 든 뒤 잠들기까지'가
# 취침 시각 문항으로 잡히지 않게 한다.
_QUESTIONS = (
    ('latency', (('잠들기까지',), ('잠드는데', '걸'), ('잠드는데까지',)), ()),
    ('wake_time', (('잠자리에서나온',), ('나온시각',), ('일어난시각',), ('기상시각',), ('기상시간',)), ()),
    ('bedtime', (('잠자리에든시각',), ('잠자리에든시간',), ('취침시각',), ('취침시간',), ('잠자리에든',)),
     ('잠들기까지', '나온', '일정', '규칙')),
    ('total_sleep', (('총수면',), ('실제로잠든',), ('실제수면',), ('실제로잔',)), ()),
    ('awakenings', (('깬횟수',), ('깬', '횟수'), ('깨', '횟수')), ()),
    ('morning_freshness', (('일어났을때', '개운'), ('일어', '개운'), ('아침', '개운')), ('지금',)),
    ('alertness', (('지금이순간',), ('지금', '개운'), ('지금', '맑'), ('지금', '졸')), ()),
    ('satisfaction', (('만족',),), ()),
    ('regularity', (('일정',), ('규칙',)), ()),
)

# 앞에 붙은 문항 번호: "1.", "1)", "1 ", "Q1.", "문항 1:", "①". 뒤에 구분 기호나 공백이 있을 때만 번호로 본다
# ("24시간" 처럼 숫자로 시작하는 낱말은 남긴다).
_LEADING_NUMBER = re.compile(r'^\s*(?:[qQ]|문항|문)?\s*\d{1,2}\s*(?:[.)\]:：]\s*|\s+)')
_CIRCLED_NUMBER = re.compile(r'^\s*[①-⑳]\s*')


def _normalize_title(title):
    text = _LEADING_NUMBER.sub('', str(title or ''), count=1)
    text = _CIRCLED_NUMBER.sub('', text, count=1)
    return re.sub(r'\s+', '', text).strip('*').lower()


def _matches(normalized, alternatives, exclusions):
    if any(word in normalized for word in exclusions):
        return False
    return any(all(word in normalized for word in words) for words in alternatives)


def find_questions(answers):
    """{지표 키: 실제 문항 제목}. answers 가 dict 가 아니거나 문항이 없으면 빈 dict."""
    if not isinstance(answers, dict):
        return {}
    normalized = [(title, _normalize_title(title)) for title in answers]
    found = {}
    used = set()
    for key, alternatives, exclusions in _QUESTIONS:
        for title, norm in normalized:
            if title in used or not norm:
                continue
            if _matches(norm, alternatives, exclusions):
                found[key] = title
                used.add(title)
                break
    return found


# --- 나이 --------------------------------------------------------------------
# RMSSD 참고값이 나이대마다 크게 다르다(measurement_metrics.AGE_BANDS). 생년월일을 묻든 출생 연도를
# 묻든 나이대를 묻든 같은 함수로 만 나이를 뽑고, 못 찾으면 None 을 돌려 고정 범위로 돌아가게 한다.
_AGE_TITLE_WORDS = (('생년월일',), ('생년',), ('출생',), ('태어난',), ('나이',), ('연령',))

_FULL_DATE = re.compile(r'(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})')
_YEAR = re.compile(r'(?:^|[^\d])(19\d{2}|20\d{2})(?:[^\d]|$)')
_AGE_RANGE = re.compile(r'(\d{1,3})\s*세?\s*[~–]\s*(\d{1,3})\s*세?')
_AGE_RANGE_HYPHEN = re.compile(r'(\d{1,3})\s*세?\s*-\s*(\d{1,3})\s*세')
_DECADE = re.compile(r'(\d{1,3})\s*대')
_AT_LEAST = re.compile(r'(\d{1,3})\s*세?\s*이상')
_UNDER = re.compile(r'(\d{1,3})\s*세?\s*(?:미만|이하)')
_PLAIN_AGE = re.compile(r'(?:^|[^\d])(\d{1,3})\s*세')
_BARE_NUMBER = re.compile(r'^\s*(\d{1,3})\s*$')


def find_age_title(answers):
    """나이·생년월일 문항의 실제 제목. 없으면 None."""
    if not isinstance(answers, dict):
        return None
    for title in answers:
        norm = _normalize_title(title)
        if norm and _matches(norm, _AGE_TITLE_WORDS, ()):
            return title
    return None


def parse_age(answers, today=None):
    """설문에서 만 나이를 뽑는다. 문항이 없거나 알아볼 수 없으면 None. 예외를 내지 않는다.

    받아들이는 형태: 생년월일(1965-03-05, 1965.3.5, 1965년 3월 5일), 출생 연도(1965),
    나이(62, 62세), 나이대 보기(45~54세, 50대, 65세 이상, 25세 미만).
    """
    try:
        title = find_age_title(answers)
        if title is None:
            return None
        return age_from_text(_answer_text(answers.get(title)), today)
    except Exception:
        return None


def age_from_text(text, today=None):
    """문구 하나에서 만 나이를 뽑는다. 알아볼 수 없으면 None."""
    text = (text or '').strip()
    if not text:
        return None
    today = today or date.today()

    match = _FULL_DATE.search(text)
    if match:
        year, month, day = (int(g) for g in match.groups())
        if 1900 <= year <= today.year and 1 <= month <= 12 and 1 <= day <= 31:
            age = today.year - year - ((today.month, today.day) < (month, day))
            return _sane_age(age)

    match = _AGE_RANGE.search(text) or _AGE_RANGE_HYPHEN.search(text)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        if low <= high and high <= 120:
            return _sane_age((low + high) // 2)

    match = _DECADE.search(text)
    if match:
        return _sane_age(int(match.group(1)) + 5)      # '50대' -> 55세로 본다

    match = _AT_LEAST.search(text)
    if match:
        return _sane_age(int(match.group(1)) + 5)      # '65세 이상' -> 70세로 본다

    match = _UNDER.search(text)
    if match:
        return _sane_age(max(int(match.group(1)) - 5, 0))

    match = _YEAR.search(text)
    if match:
        year = int(match.group(1))
        if 1900 <= year <= today.year:
            return _sane_age(today.year - year)        # 생일 전인지는 알 수 없어 연도 차이로 둔다

    match = _PLAIN_AGE.search(text) or _BARE_NUMBER.search(text)
    if match:
        return _sane_age(int(match.group(1)))
    return None


def _sane_age(age):
    return age if 0 <= age <= 120 else None


def _answer_text(value):
    if isinstance(value, (list, tuple)):
        return ' '.join(str(v) for v in value if v is not None).strip()
    return '' if value is None else str(value).strip()


# --- 보기 문구 해석 ------------------------------------------------------------
_EARLIER = ('이전', '미만', '이하', '이내', '전에')
_LATER = ('이후', '이상', '초과', '넘', '후에')

# "밤 9시", "새벽 1시 30분", "9시 반", "21:30". 시간대 낱말이 없으면 앞 숫자의 시간대를 물려받는다("밤 9시 ~ 10시").
_CLOCK = re.compile(
    r'(밤|저녁|오후|낮|새벽|아침|오전|정오|자정)?\s*(\d{1,2})\s*(?::\s*(\d{2})|시\s*(?:(\d{1,2})\s*분|(반))?)'
)


def _to_24h(period, hour, minute):
    if hour > 24 or minute >= 60:
        return None
    if hour == 24:
        hour = 0
    if period == '정오':
        hour = 12
    elif period == '자정':
        hour = 0
    elif period in ('밤', '저녁', '오후', '낮'):
        if hour < 12:
            hour += 12
        elif period == '밤' and hour == 12:     # 밤 12시 = 자정
            hour = 0
    elif period in ('새벽', '오전', '아침') and hour == 12:
        hour = 0
    return hour + minute / 60.0


def _clock_values(text, evening):
    """문구에 나온 시각들을 24시간제 실수(시)로. evening 이면 시간대 낱말이 전혀 없는 6~11시를 저녁으로 본다."""
    # 숫자 없이 쓴 '자정'·'정오'("자정 ~ 새벽 1시")는 숫자 시각으로 바꿔 같은 규칙으로 읽는다.
    text = re.sub(r'자정(?!\s*\d)', '자정 0시', text)
    text = re.sub(r'정오(?!\s*\d)', '정오 12시', text)
    values = []
    period = None
    labelled = False
    for match in _CLOCK.finditer(text):
        period = match.group(1) or period
        labelled = labelled or bool(match.group(1))
        hour = int(match.group(2))
        minute = int(match.group(3) or match.group(4) or 0)
        if match.group(5):
            minute = 30
        value = _to_24h(period, hour, minute)
        if value is not None:
            values.append(value)
    if evening and values and not labelled:
        # 취침 시각 보기에 "9시 ~ 10시" 처럼 시간대가 없으면 오전이 아니라 저녁이고, "12시" 는 낮이 아니라 자정이다.
        values = [v + 12 if 6 <= v < 12 else (v - 12 if 12 <= v < 13 else v) for v in values]
    return values


def parse_clock_time(text, evening=False):
    """시각 보기 -> 대표 시각(0 이상 24 미만의 실수, 시). "밤 11시 ~ 12시" -> 23.5, "새벽 2시 이후" -> 2.5. 못 읽으면 None."""
    text = _answer_text(text)
    values = _clock_values(text, evening)
    if not values:
        return None
    if len(values) == 1:
        value = values[0]
        if any(word in text for word in _EARLIER):
            value -= 0.5
        elif any(word in text for word in _LATER):
            value += 0.5
    else:
        start, end = values[0], values[1]
        if end < start:
            # 끝이 시작보다 앞서면 자정을 넘는 구간이다. "오후 11시 ~ 12시" 의 12 는 낮 12시(12)로 읽혔지만 뜻은 24시이므로
            # 12를 더해 시작 뒤로 보내고, 그래도 앞서면("밤 11시 ~ 12시" 의 0시) 하루(24)를 더한다.
            end = end + 12 if end + 12 > start else end + 24
        value = (start + end) / 2.0
    return round(value % 24, 2)


def format_clock(value):
    """23.5 -> '23:30'."""
    if value is None:
        return None
    total = int(round(value * 60)) % (24 * 60)
    return '%02d:%02d' % (total // 60, total % 60)


def hours_between(bedtime, wake_time):
    """취침 시각에서 기상 시각까지(자정을 넘어도) 시간. 같은 시각이면 None."""
    if bedtime is None or wake_time is None:
        return None
    hours = round((wake_time - bedtime) % 24, 1)
    return hours if hours > 0 else None


_HOURS_MINUTES = re.compile(r'(\d+(?:\.\d+)?)\s*시간\s*(?:(\d{1,2})\s*분|(반))')
_NUMBER = re.compile(r'\d+(?:\.\d+)?')


def parse_sleep_hours(text):
    """총 수면 시간 보기 -> 대표 시간. "5시간 미만" -> 4.5, "6 ~ 7시간" -> 6.5, "8시간 이상" -> 8.5. 못 읽으면 None."""
    text = _answer_text(text)
    match = _HOURS_MINUTES.search(text)
    if match:
        value = float(match.group(1)) + (30 if match.group(3) else int(match.group(2) or 0)) / 60.0
    else:
        numbers = [float(n) for n in _NUMBER.findall(text)]
        if not numbers:
            return None
        if len(numbers) >= 2:
            value = (numbers[0] + numbers[1]) / 2.0
        elif any(word in text for word in _EARLIER):
            value = numbers[0] - 0.5
        elif any(word in text for word in _LATER):
            value = numbers[0] + 0.5
        else:
            value = numbers[0]
    if not 0 < value <= 24:
        return None
    return round(value, 1)


_DURATION_TOKEN = re.compile(r'(\d+(?:\.\d+)?)\s*(시간|분)?')


def parse_latency_minutes(text):
    """잠들기까지 걸린 시간 보기 -> 구간의 위쪽 경계(분). "15분 이내" -> 15, "16 ~ 30분" -> 30, "1시간 초과" -> 61."""
    text = _answer_text(text)
    tokens = [(float(n), unit) for n, unit in _DURATION_TOKEN.findall(text)]
    if not tokens:
        return None
    # "16 ~ 30분" 의 16 처럼 단위가 없는 숫자는 뒤에 나오는 단위를 따른다.
    minutes = []
    for i, (number, unit) in enumerate(tokens):
        if not unit:
            unit = next((u for _, u in tokens[i + 1:] if u), '분')
        minutes.append(number * 60 if unit == '시간' else number)
    if len(minutes) >= 2:
        value = max(minutes[0], minutes[1])
    elif any(word in text for word in _LATER):
        value = minutes[0] + 1
    else:
        value = minutes[0]
    return int(round(value))


def parse_awakenings(text):
    """밤중에 깬 횟수 보기 -> 정수. "3회 이상" -> 3, "없음" -> 0. 못 읽으면 None."""
    text = _answer_text(text)
    match = re.search(r'\d+', text)
    if match:
        return int(match.group())
    if '없' in text:
        return 0
    return None


# --- PSQI 방식 구성 점수 (0~3) ----------------------------------------------------
def latency_component(minutes):
    if minutes is None:
        return None
    if minutes <= 15:
        return 0
    if minutes <= 30:
        return 1
    if minutes <= 60:
        return 2
    return 3


def duration_component(hours):
    if hours is None:
        return None
    if hours > 7:
        return 0
    if hours >= 6:
        return 1
    if hours >= 5:
        return 2
    return 3


def efficiency_component(percent):
    if percent is None:
        return None
    if percent >= 85:
        return 0
    if percent >= 75:
        return 1
    if percent >= 65:
        return 2
    return 3


def awakenings_component(count):
    """부스용 단순화: 0회 0, 1회 1, 2회 2, 3회 이상 3 (PSQI 의 빈도 문항과 다르다)."""
    if count is None:
        return None
    return min(max(int(count), 0), 3)


def sleep_efficiency(total_sleep, time_in_bed):
    """총 수면 / 잠자리에 머문 시간 x 100. 구간 추정이라 100 을 넘으면 100 으로 자른다."""
    if total_sleep is None or not time_in_bed:
        return None
    return round(min(total_sleep / time_in_bed * 100.0, 100.0), 1)


def score_band(score):
    if score is None:
        return None
    if score <= 3:
        return '양호'
    if score <= 6:
        return '보통'
    return '개선 여지'


_LATENCY_VERDICTS = ('양호', '보통', '긴 편', '매우 긴 편')
_DURATION_VERDICTS = ('양호', '조금 짧은 편', '짧은 편', '매우 짧은 편')
_EFFICIENCY_VERDICTS = ('양호', '보통', '낮은 편', '매우 낮은 편')
_AWAKENINGS_VERDICTS = ('양호', '보통', '잦은 편', '매우 잦은 편')

REFERENCES = {
    'bedtime': '응답 구간의 중앙값',
    'wake_time': '응답 구간의 중앙값',
    'time_in_bed': '취침·기상 시각 응답으로 계산',
    'latency': '15분 이내 양호 (PSQI 구간)',
    'total_sleep': '7시간 초과 양호 (PSQI 구간)',
    'efficiency': '85% 이상 양호 (PSQI 구간)',
    'awakenings': '0회 양호 (부스 간이 기준)',
    'scale': '5단계 자기평가',
}

_SCALE_LABELS = (
    ('morning_freshness', '아침에 일어났을 때 개운한 정도'),
    ('satisfaction', '어젯밤 잠에 대한 만족도'),
    ('alertness', '지금 이 순간의 개운함'),
    ('regularity', '평소 취침 시각의 규칙성'),
)

NO_ANSWER = '응답 없음'
UNREADABLE = '해석 불가'


def _verdict(component, labels):
    return labels[component] if component is not None else '-'


def _hours_text(hours):
    return None if hours is None else ('약 %s시간' % ('%g' % hours))


def _safe(parser, text, **kwargs):
    # 정규식 해석은 예외를 내지 않지만, 이 모듈의 약속(어떤 입력에도 예외 없음)을 지키기 위한 마지막 안전망이다.
    try:
        return parser(text, **kwargs)
    except (TypeError, ValueError, OverflowError, AttributeError):
        return None


def _row(key, label, answer, value, reference, verdict):
    return {'key': key, 'label': label, 'answer': answer, 'value': value, 'reference': reference, 'verdict': verdict}


def _answered_row(key, label, answer, value, reference, verdict):
    """응답이 비었거나 읽지 못한 문항의 표 행. 값 자리에 그 사정을 적는다."""
    if not answer:
        return _row(key, label, NO_ANSWER, '-', reference, '-')
    if value is None:
        return _row(key, label, answer, UNREADABLE, reference, '-')
    return _row(key, label, answer, value, reference, verdict)


def compute_survey_metrics(answers):
    """설문 응답 dict -> SurveyMetrics(rows, score, payload). 어떤 입력에도 예외를 내지 않는다.

    rows: 찾은 문항(과 계산할 수 있었던 파생 지표)만 순서대로. 각 행은 {key, label, answer, value, reference, verdict}.
    score: 간이 수면 점수 {'score', 'max', 'available', 'total', 'band', 'summary'} (문항이 하나도 없으면 None).
    payload: AI 프롬프트용 dict. 숫자·분류·구성 점수·참고 기준과 '구간 추정' 안내가 들어 있다.
    """
    found = find_questions(answers)
    texts = {key: _answer_text(answers[title]) for key, title in found.items()}

    bedtime = _safe(parse_clock_time, texts.get('bedtime'), evening=True) if 'bedtime' in found else None
    wake_time = _safe(parse_clock_time, texts.get('wake_time')) if 'wake_time' in found else None
    time_in_bed = hours_between(bedtime, wake_time)
    latency_minutes = _safe(parse_latency_minutes, texts.get('latency')) if 'latency' in found else None
    total_sleep = _safe(parse_sleep_hours, texts.get('total_sleep')) if 'total_sleep' in found else None
    efficiency = sleep_efficiency(total_sleep, time_in_bed)
    awakenings = _safe(parse_awakenings, texts.get('awakenings')) if 'awakenings' in found else None

    components = {
        'latency': latency_component(latency_minutes),
        'duration': duration_component(total_sleep),
        'efficiency': efficiency_component(efficiency),
        'awakenings': awakenings_component(awakenings),
    }
    verdicts = {
        'latency': _verdict(components['latency'], _LATENCY_VERDICTS),
        'duration': _verdict(components['duration'], _DURATION_VERDICTS),
        'efficiency': _verdict(components['efficiency'], _EFFICIENCY_VERDICTS),
        'awakenings': _verdict(components['awakenings'], _AWAKENINGS_VERDICTS),
    }

    rows = []
    if 'bedtime' in found:
        rows.append(_answered_row('bedtime', '잠자리에 든 시각', texts['bedtime'],
                                  format_clock(bedtime) and '약 %s' % format_clock(bedtime), REFERENCES['bedtime'], '-'))
    if 'wake_time' in found:
        rows.append(_answered_row('wake_time', '잠자리에서 나온 시각', texts['wake_time'],
                                  format_clock(wake_time) and '약 %s' % format_clock(wake_time), REFERENCES['wake_time'], '-'))
    if time_in_bed is not None:
        rows.append(_row('time_in_bed', '잠자리에 머문 시간', '-', '%s (추정)' % _hours_text(time_in_bed),
                         REFERENCES['time_in_bed'], '-'))
    if 'latency' in found:
        rows.append(_answered_row('latency', '잠들기까지 걸린 시간', texts['latency'],
                                  texts['latency'] if latency_minutes is not None else None,
                                  REFERENCES['latency'], verdicts['latency']))
    if 'total_sleep' in found:
        rows.append(_answered_row('total_sleep', '총 수면 시간', texts['total_sleep'],
                                  _hours_text(total_sleep) and '%s (추정)' % _hours_text(total_sleep),
                                  REFERENCES['total_sleep'], verdicts['duration']))
    if efficiency is not None:
        rows.append(_row('efficiency', '수면 효율', '-', '약 %g%% (추정)' % efficiency,
                         REFERENCES['efficiency'], verdicts['efficiency']))
    if 'awakenings' in found:
        rows.append(_answered_row('awakenings', '밤중에 깬 횟수', texts['awakenings'],
                                  ('%d회' % awakenings) if awakenings is not None else None,
                                  REFERENCES['awakenings'], verdicts['awakenings']))
    scales = {}
    for key, label in _SCALE_LABELS:
        if key in found:
            scales[key] = texts[key] or None
            rows.append(_answered_row(key, label, texts[key], texts[key] or None, REFERENCES['scale'], '-'))

    available = [c for c in (components[k] for k in SCORE_COMPONENTS) if c is not None]
    score_value = sum(available) if len(available) >= SCORE_MIN_COMPONENTS else None
    band = score_band(score_value)
    if score_value is not None:
        summary = '간이 수면 점수 %d점 / %d점 (%d개 항목 중 %d개로 계산) · %s' % (
            score_value, SCORE_MAX, len(SCORE_COMPONENTS), len(available), band)
    else:
        summary = '간이 수면 점수: 계산 불가 (%d개 항목 중 %d개만 계산됨)' % (len(SCORE_COMPONENTS), len(available))
    score = {
        'score': score_value, 'max': SCORE_MAX, 'available': len(available), 'total': len(SCORE_COMPONENTS),
        'band': band, 'summary': summary,
    } if found else None

    payload = {
        'note': (
            '설문 응답 구간의 중앙값과 자기 보고로 추정한 값이다(실제 측정한 수면이 아니다). '
            '구성 점수는 PSQI 방식(0 좋음 ~ 3 나쁨)이며, 밤중에 깬 횟수는 부스용으로 단순화한 기준이다. '
            'None 은 문항이 없거나 응답을 읽지 못한 것이다.'
        ),
        'answers': {key: (texts.get(key) or None) for key in found},
        'bedtime': format_clock(bedtime),
        'wake_time': format_clock(wake_time),
        'time_in_bed_hours': time_in_bed,
        'total_sleep_hours': total_sleep,
        'sleep_efficiency_percent': efficiency,
        'sleep_latency_minutes_max': latency_minutes,
        'awakenings': awakenings,
        'self_ratings': scales,
        'components': components,
        'verdicts': verdicts,
        'simple_score': {
            'score': score_value, 'max': SCORE_MAX,
            'components_available': len(available), 'components_total': len(SCORE_COMPONENTS),
            'band': band,
            'bands': '0~3 양호, 4~6 보통, 7~12 개선 여지 (계산된 항목이 %d개 미만이면 계산 불가)' % SCORE_MIN_COMPONENTS,
        },
        'references': {
            'latency': '15분 이내 0(양호), 16~30분 1(보통), 31~60분 2(긴 편), 60분 초과 3(매우 긴 편)',
            'duration': '7시간 초과 0(양호), 6~7시간 1(조금 짧은 편), 5~6시간 2(짧은 편), 5시간 미만 3(매우 짧은 편)',
            'efficiency': '85% 이상 0(양호), 75~84% 1(보통), 65~74% 2(낮은 편), 65% 미만 3(매우 낮은 편)',
            'awakenings': '0회 0(양호), 1회 1(보통), 2회 2(잦은 편), 3회 이상 3(매우 잦은 편) - 부스 간이 기준',
            'self_ratings': '5단계 자기평가. 응답 문구 그대로 읽는다.',
        },
    }
    return SurveyMetrics(rows, score, payload)
