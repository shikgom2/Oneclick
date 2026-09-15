# -*- coding:utf-8 -*-
"""booth 도메인 로직.

페이지 뷰, 기기용 API, 리포트 생성, 관리 명령이 모두 이 모듈을 거쳐 Participant 를 읽고
바꾼다. 설문(구글 폼)·측정(태블릿)·리포트 생성이 서로 다른 uWSGI 워커에서 거의 동시에
들어오기 때문에, 상태 전이 규칙과 동시 쓰기 처리를 한곳에 모아 두어야 어긋나지 않는다.

SQLite 동시 쓰기에 대해:
  Django 의 transaction.atomic 은 SQLite 에서 'BEGIN'(deferred)으로 시작한다. 이 상태에서
  먼저 SELECT 로 읽고 나중에 쓰려고 하면, 그 사이 다른 워커가 커밋한 경우 SQLite 는 잠금을
  기다리지 않고 즉시 'database is locked' 를 낸다(WAL 스냅샷 충돌). timeout 20초 설정도
  이 경우에는 소용이 없다. 그래서 트랜잭션의 첫 문장을 '쓰기'(_acquire_write_lock)로 시작해
  쓰기 잠금을 먼저 잡고(이때는 timeout 만큼 기다려 준다), 그다음에 읽고 쓴다.
  Django 5.1 의 transaction_mode='IMMEDIATE' 옵션과 같은 효과를 4.2 에서도 내는 방법이다.
"""
import base64
import hmac
import logging
import math
import random
import re
import time
import traceback
from collections import namedtuple
from datetime import datetime, timedelta, timezone as dt_timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 에서는 Django 4.2 가 backports.zoneinfo 를 함께 설치한다
    from backports.zoneinfo import ZoneInfo

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.dateparse import parse_datetime

from . import conf
from .models import Participant, new_token

logger = logging.getLogger(__name__)

BOOTH_DB = conf.BOOTH_DB
KST = ZoneInfo(conf.KST_TZ_NAME)
Status = Participant.ReportStatus

MAX_PARTICIPANT_NUMBER = 2147483647     # PositiveIntegerField 상한 (DB 공통)
WRITE_RETRIES = 5

# 데이터가 바뀌면 함께 초기화되는 리포트 필드. report_generations 는 일부러 넣지 않는다
# (재제출로 초기화되면 비용 상한이 무의미해진다).
REPORT_FIELDS = (
    'report', 'report_status', 'report_error',
    'report_started_at', 'report_done_at', 'report_attempts',
)

GENERATIONS_EXHAUSTED_ERROR = '참가자 한 명당 리포트 생성 한도(%d회)를 모두 사용했습니다.' % conf.MAX_REPORT_GENERATIONS


class BoothError(Exception):
    pass


class AlreadyMeasured(BoothError):
    """이미 측정값이 있는 참가자에게 overwrite 없이 측정값을 보냈을 때 (API 409)."""


class MeasurementInvalid(BoothError):
    """측정 본문이 계약과 맞지 않을 때 (API 400). 메시지는 그대로 응답에 쓰는 한국어."""


class SurveyOverwriteRefused(BoothError):
    """확인 코드 없는 제출이 이미 접수된 설문을 덮어쓰려 할 때 (API 409)."""


class IssuanceLimited(BoothError):
    """최근 1시간 발급 수가 BOOTH_MAX_ISSUE_PER_HOUR 에 닿았을 때 (페이지 503)."""


def log_exception_safely(log, message, exc):
    """예외를 '종류 + 코드 위치(파일·줄·코드)'만 ERROR 로 남긴다. 예외 메시지와 지역 변수는 쓰지 않는다.

    운영 로그는 여러 사람이 보고 오래 남는다. str(exc) 에는 설문 응답·이름·토큰이 섞일 수 있고,
    logger.exception 의 마지막 줄에도 같은 메시지가 찍히므로 traceback.format_tb 결과만 남긴다.
    """
    frames = ''.join(traceback.format_tb(exc.__traceback__)) if exc.__traceback__ is not None else ''
    log.error('%s (%s)\n%s', message, type(exc).__name__, frames.rstrip())


# ---------------------------------------------------------------------------
# 번호·이름 표기
# ---------------------------------------------------------------------------
def format_label(number):
    """42 -> 'SF-042'. 세 자리 미만만 0 으로 채운다(1000 번 이후는 'SF-1000')."""
    return '%s-%03d' % (conf.number_prefix(), int(number))


_DASHES = '-‐‑‒–—−'   # 폼에서 직접 고쳐 쓴 경우의 각종 대시


def parse_number(value):
    """참가자 번호를 int 로. 해석할 수 없으면 None.

    42, '42', 'SF-042', 'sf042', ' SF-42 ' 를 모두 받는다. 구글 폼의 번호 문항은 미리
    채워지지만 사용자가 지우거나 고쳐 쓸 수 있어서 대소문자·공백·대시 변형을 허용한다.
    다른 접두어('AB-042')는 다른 행사 번호일 수 있으므로 거부한다.
    """
    if value is None or isinstance(value, bool):       # bool 은 int 의 하위형이라 먼저 걸러낸다
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        number = int(value)
    elif isinstance(value, str):
        pattern = r'^(?:%s)?\s*[%s]?\s*(\d{1,10})$' % (re.escape(conf.number_prefix()), re.escape(_DASHES))
        match = re.match(pattern, value.strip(), re.IGNORECASE)
        if not match:
            return None
        number = int(match.group(1))
    else:
        return None
    if number < 1 or number > MAX_PARTICIPANT_NUMBER:
        return None
    return number


_HANGUL_ONLY = re.compile(r'^[가-힣]+$')


def mask_name(name):
    """대기 목록용 이름 가림. 홍길동 -> 홍○동, 이수 -> 이○, John -> J***.

    태블릿 화면은 다른 관람객에게도 보이므로 실명 전체를 노출하지 않으면서, 스태프가
    본인 확인을 할 수 있을 만큼만 남긴다. 한 글자 이름은 남기면 전체가 드러나므로 전부 가린다.
    """
    if not name:
        return ''
    compact = re.sub(r'\s+', '', str(name))
    if not compact:
        return ''
    if _HANGUL_ONLY.match(compact):
        if len(compact) == 1:
            return '○'
        if len(compact) == 2:
            return compact[0] + '○'
        return compact[0] + '○' * (len(compact) - 2) + compact[-1]
    if len(compact) == 1:
        return '*'
    return compact[0] + '*' * (len(compact) - 1)


# ---------------------------------------------------------------------------
# 설문 해석
# ---------------------------------------------------------------------------
# 명세의 부정 표현('동의하지', '미동의', '거부')에 흔한 변형을 더했다. 판정이 애매하면
# '동의 안 함' 쪽으로 기울어야 개인정보가 동의 없이 외부(AI)로 나가지 않는다.
_CONSENT_NEGATIVES = ('동의하지', '미동의', '비동의', '거부', '동의안', '않')

_MAX_ANSWERS = 200
_MAX_TITLE_LEN = 500
_MAX_TEXT_LEN = 5000
_MAX_LIST_ITEMS = 100
_MAX_ITEM_LEN = 1000

# Apps Script 는 제목이 같은 문항에 ' (2)', ' (3)' 을 붙여 보낸다(collectAnswers_). 정규화한 제목 끝의
# '(숫자)' 를 떼면 원래 제목이 된다.
_DUPLICATE_SUFFIX = re.compile(r'\(\d+\)$')


def parse_consent(value):
    """동의 문항 응답(문자열 또는 목록)이 '동의'인지.

    '동의'를 포함하고 부정 표현을 포함하지 않을 때만 True. 공백을 지운 뒤 비교해
    '동의 하지 않습니다' 같은 띄어쓰기 변형도 부정으로 잡는다.
    """
    if value is None:
        return False
    if isinstance(value, (list, tuple)):
        text = ''.join(str(v) for v in value if v is not None)
    else:
        text = str(value)
    text = re.sub(r'\s+', '', text)
    if '동의' not in text:
        return False
    return not any(neg in text for neg in _CONSENT_NEGATIVES)


def _normalize_title(title):
    # 구글 폼 문항 제목은 앞뒤 공백·필수 표시(*)가 섞여 들어올 수 있다.
    return re.sub(r'\s+', '', str(title)).strip('*').lower()


def _find_answer_key(answers, title):
    """answers 에서 title 에 해당하는 실제 키. 정확히 일치를 먼저, 없으면 공백·대소문자 무시."""
    if not isinstance(answers, dict) or not title:
        return None
    if title in answers:
        return title
    target = _normalize_title(title)
    for key in answers:
        if _normalize_title(key) == target:
            return key
    return None


def _title_in(key, normalized_titles):
    """key 가 normalized_titles 중 하나이거나, 그 제목에 Apps Script 중복 번호만 붙은 것인지."""
    normalized = _normalize_title(key)
    if normalized in normalized_titles:
        return True
    base = _DUPLICATE_SUFFIX.sub('', normalized)
    return base != normalized and base.rstrip('*') in normalized_titles


def normalize_answers(answers):
    """설문 응답을 {문항 제목(str): str 또는 [str, ...]} 로 정리한 새 dict.

    Apps Script 가 보내는 형태를 그대로 저장하되, 비정상적으로 큰 본문이 DB·프롬프트를
    채우지 않게 개수와 길이를 자른다. dict 가 아니면 ValueError.
    """
    if not isinstance(answers, dict):
        raise ValueError('answers 는 객체여야 합니다.')
    cleaned = {}
    for key, value in list(answers.items())[:_MAX_ANSWERS]:
        title = str(key).strip()[:_MAX_TITLE_LEN]
        if not title:
            continue
        if value is None:
            cleaned[title] = ''
        elif isinstance(value, (list, tuple)):
            cleaned[title] = [str(v)[:_MAX_ITEM_LEN] for v in value[:_MAX_LIST_ITEMS] if v is not None]
        else:
            cleaned[title] = str(value)[:_MAX_TEXT_LEN]
    return cleaned


def _answer_text(value):
    if isinstance(value, (list, tuple)):
        return ' '.join(str(v) for v in value if v is not None)
    return '' if value is None else str(value)


def find_name_key(answers):
    """이름 문항의 실제 키. 이름 문항을 끈 설정('-')이거나 찾지 못하면 None."""
    title = conf.survey_name_title()
    return _find_answer_key(answers, title) if title else None


def name_question_missing(answers):
    """이름 문항 제목이 설정돼 있는데 응답에서 찾지 못했는지(설정 오류).

    제목이 조금만 달라도('성함', '이름(필수)') 이름을 찾지 못하고, 그러면 실명이 '다른 문항'으로
    AI 에 그대로 넘어간다. report 는 이 경우 Claude 를 부르지 않는다(실패 쪽으로 닫힘).
    BOOTH_SURVEY_NAME_TITLE='-' 로 '이름 문항 없음'을 명시한 폼은 False.
    """
    return bool(conf.survey_name_title()) and find_name_key(answers) is None


def all_consents_given(answers):
    """설정한 동의 문항(conf.survey_consent_titles)이 모두 있고 모두 동의인지.

    문항이 하나라도 없거나 거부면 False. 폼 설정이 어긋나도 동의 없이 외부로 나가지 않는다.
    """
    titles = conf.survey_consent_titles()
    if not titles or not isinstance(answers, dict):
        return False
    for title in titles:
        key = _find_answer_key(answers, title)
        if key is None or not parse_consent(answers[key]):
            return False
    return True


def extract_survey_fields(answers):
    """설문 응답에서 (이름, 동의 여부)를 꺼낸다. 문항 제목은 conf 의 BOOTH_SURVEY_* 설정."""
    name_key = find_name_key(answers)
    name = ''
    if name_key is not None:
        name = re.sub(r'\s+', ' ', _answer_text(answers[name_key])).strip()[:100]
    return name, all_consents_given(answers)


def identifying_titles():
    """AI 로 보내지 않는 문항(이름·동의·참가자 번호·확인 코드)의 정규화한 제목 집합."""
    titles = [conf.survey_name_title(), conf.survey_number_title(), conf.survey_code_title()]
    titles += conf.survey_consent_titles()
    return {_normalize_title(title) for title in titles if title}


def survey_answers_for_ai(answers):
    """AI 에 보낼 설문 응답. 이름·동의·참가자 번호·확인 코드 문항을 뺀 새 dict.

    실명과 번호는 리포트 생성에 필요 없고, 국외(Anthropic) 전송 범위를 동의 문구대로
    '이름을 제외한 설문 응답'으로 한정하기 위해 여기서 확실히 제거한다. 같은 제목이 두 번 있어
    Apps Script 가 '이름 (2)' 로 보낸 문항도 함께 뺀다.
    """
    if not isinstance(answers, dict):
        return {}
    excluded = identifying_titles()
    return {k: v for k, v in answers.items() if not _title_in(k, excluded)}


# ---------------------------------------------------------------------------
# 설문 확인 코드
# ---------------------------------------------------------------------------
CODE_OK = 'ok'
CODE_MISSING = 'missing'
CODE_MISMATCH = 'mismatch'
SURVEY_CODE_LENGTH = 8


def survey_code(participant):
    """구글 폼에 미리 채우는 확인 코드(대문자·숫자 8자).

    참가자 번호는 순번이고 화면에 공개돼 있다. 번호만으로 설문을 받으면 공개된 폼으로 남의 번호를
    적어 이름·동의·응답을 덮어쓰거나(오타도 마찬가지) 남의 리포트에 지시문을 심을 수 있다.
    개인 페이지 토큰에서 SECRET_KEY 로 만든 HMAC 이라 번호를 알아도 코드는 알 수 없다.
    DB 에 저장하지 않고 필요할 때 다시 계산한다.
    """
    digest = salted_hmac('booth.survey_code', participant.token, algorithm='sha256').digest()
    return base64.b32encode(digest).decode('ascii')[:SURVEY_CODE_LENGTH]


def _normalize_code(value):
    return re.sub(r'[\s\-_]+', '', _answer_text(value)).upper()


def check_survey_code(participant, answers):
    """설문 응답의 확인 코드 판정: CODE_OK / CODE_MISSING(문항 없음·빈 값) / CODE_MISMATCH."""
    key = _find_answer_key(answers, conf.survey_code_title())
    provided = _normalize_code(answers[key]) if key is not None else ''
    if not provided:
        return CODE_MISSING
    expected = survey_code(participant)
    if hmac.compare_digest(provided.encode('utf-8'), expected.encode('ascii')):
        return CODE_OK
    return CODE_MISMATCH


# ---------------------------------------------------------------------------
# 시간
# ---------------------------------------------------------------------------
# 기기 시계 오류로 들어오는 극단적인 연도(0001-01-01 등)는 시간대 변환에서 OverflowError 가 난다.
_MIN_YEAR = 2000
_MAX_YEAR = 2100


def kst(dt):
    """aware datetime 을 한국 시간으로. naive 는 Django 저장 관례대로 UTC 로 본다. None -> None."""
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt.astimezone(KST)


def kst_isoformat(dt):
    """API 응답용 KST ISO 문자열(초 단위). None -> None."""
    value = kst(dt)
    return value.isoformat(timespec='seconds') if value is not None else None


def parse_iso_datetime(value):
    """ISO 8601 문자열(또는 datetime)을 aware datetime 으로. 해석 불가·2000~2100년 밖이면 None.

    시간대가 없는 값은 기기(태블릿·폼)의 현지 시각, 즉 한국 시간으로 본다.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = parse_datetime(value.strip())
        except (ValueError, OverflowError):
            dt = None
    else:
        return None
    if dt is None:
        return None
    if not _MIN_YEAR <= dt.year <= _MAX_YEAR:
        return None
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=KST)
    return dt


def deletion_date(participant):
    """이 참가자 데이터가 삭제 대상이 되는 날짜(한국 날짜)."""
    return (kst(participant.created_at) + timedelta(days=conf.retention_days())).date()


def _retention_cutoff():
    return timezone.now() - timedelta(days=conf.retention_days())


# ---------------------------------------------------------------------------
# 조회·URL
# ---------------------------------------------------------------------------
def _valid_token_value(token):
    return bool(token) and isinstance(token, str) and len(token) <= 64


def get_participant_by_token(token):
    """개인 페이지 토큰으로 참가자 조회. 없거나 보유기간이 지났으면 None.

    purge 크론 사이의 공백에도 보유기간이 지난 개인 페이지가 열리지 않게 조회 단계에서 기간을
    확인한다. 키오스크 토큰(kiosk_token)으로는 절대 찾지 않는다.
    """
    if not _valid_token_value(token):
        return None
    return (Participant.objects.using(BOOTH_DB)
            .filter(token=token, created_at__gte=_retention_cutoff())
            .first())


def get_participant_by_kiosk_token(token):
    """공용 키오스크 화면 토큰으로 조회. 기한이 지났으면 None.

    키오스크 주소는 공용 태블릿의 방문 기록에 남으므로 쓸모 있는 시간을 짧게 둔다.
      - 발급 후 KIOSK_ABANDON_SEC(+여유)가 지나면 무효(화면도 그때 첫 화면으로 돌아간다)
      - 설문이 접수되면 KIOSK_RETURN_DELAY_SEC(+여유) 뒤 무효
    여유(KIOSK_TOKEN_GRACE_SEC)는 Apps Script 전송 지연과 기기 시계 차이 몫이다.
    이 토큰으로는 번호와 설문 접수 여부만 볼 수 있고, 개인 페이지 토큰으로는 찾지 않는다.
    """
    if not _valid_token_value(token):
        return None
    now = timezone.now()
    grace = conf.KIOSK_TOKEN_GRACE_SEC
    participant = (Participant.objects.using(BOOTH_DB)
                   .filter(kiosk_token=token, source=Participant.Source.KIOSK,
                           created_at__gte=now - timedelta(seconds=conf.KIOSK_ABANDON_SEC + grace))
                   .first())
    if participant is None:
        return None
    if participant.has_survey and participant.survey_received_at < now - timedelta(
            seconds=conf.KIOSK_RETURN_DELAY_SEC + grace):
        return None
    return participant


def get_participant_by_number(number):
    """번호(int 또는 'SF-042' 등)로 참가자 조회. 없거나 보유기간이 지났으면 None."""
    parsed = parse_number(number)
    if parsed is None:
        return None
    return (Participant.objects.using(BOOTH_DB)
            .filter(number=parsed, created_at__gte=_retention_cutoff())
            .first())


def build_report_url(participant, request=None):
    """개인 페이지 절대 URL. 태블릿이 QR 로 띄우는 주소라 https 주소만 만든다.

    1) BOOTH_PUBLIC_BASE_URL 이 https:// 로 시작하면 그 주소
    2) 아니면 요청이 https 로 들어왔을 때만 그 요청 기준 주소
    3) request 가 없으면(관리 명령 등) 상대 경로
    https 주소를 만들 수 없으면 오류 로그를 남기고 '' 를 돌려준다. 평문 http QR 을 찍으면 이름과
    리포트가 암호화 없이 오간다. BOOTH_ALLOW_INSECURE=1(로컬 개발·테스트)이면 http 도 허용한다.
    """
    path = reverse('booth:personal', kwargs={'token': participant.token})
    insecure_ok = conf.allow_insecure()
    base = conf.public_base_url()
    if base:
        if base.lower().startswith('https://') or insecure_ok:
            return base + path
        logger.error('[booth] BOOTH_PUBLIC_BASE_URL 이 https:// 로 시작하지 않아 쓰지 않습니다.')
    if request is None:
        return path
    if request.is_secure() or insecure_ok:
        return request.build_absolute_uri(path)
    logger.error('[booth] https 가 아닌 요청이라 개인 페이지 주소를 만들지 않았습니다.')
    return ''


# ---------------------------------------------------------------------------
# 동시 쓰기 도우미
# ---------------------------------------------------------------------------
def _is_lock_error(exc):
    return 'locked' in str(exc).lower()


def _backoff(attempt):
    # 워커들이 같은 박자로 다시 부딪히지 않게 약간의 무작위를 섞는다.
    time.sleep(random.uniform(0.02, 0.08) * (attempt + 1))


def _acquire_write_lock(pk=0):
    """트랜잭션 첫 문장으로 쓰기 잠금을 잡는다(모듈 docstring 참고).

    pk=0 은 존재하지 않는 행이라 아무것도 바뀌지 않지만, UPDATE 문 자체가 쓰기 잠금을 연다.
    """
    Participant.objects.using(BOOTH_DB).filter(pk=pk).update(updated_at=timezone.now())


def _retry_write(operation, retry_integrity=False):
    """operation 을 booth 트랜잭션 안에서 실행하고, 잠금 충돌(과 선택적으로 unique 충돌)이면 재시도."""
    last_exc = None
    for attempt in range(WRITE_RETRIES):
        try:
            with transaction.atomic(using=BOOTH_DB):
                return operation()
        except IntegrityError as exc:
            if not retry_integrity:
                raise
            last_exc = exc
        except OperationalError as exc:
            if not _is_lock_error(exc):
                raise
            last_exc = exc
        logger.warning('[booth] 쓰기 재시도 %d/%d (%s)', attempt + 1, WRITE_RETRIES, type(last_exc).__name__)
        if attempt < WRITE_RETRIES - 1:
            _backoff(attempt)
    raise last_exc


def _update_participant(participant, mutate):
    """잠금을 잡고 DB 에서 최신 행을 다시 읽어 mutate(fresh) 를 적용·저장한다.

    뷰가 들고 있는 participant 는 요청 초반에 읽은 값이라, 그 사이 다른 워커가 설문이나
    측정을 저장했을 수 있다. 오래된 인스턴스를 그대로 save() 하면 그 변경을 덮어쓴다.
    mutate 가 False 를 돌려주면 저장하지 않는다(바꿀 것이 없는 중복 전송 등).
    끝나면 넘겨받은 인스턴스에도 최신 값을 복사해 호출자가 바로 쓸 수 있게 한다.
    """
    def operation():
        _acquire_write_lock(participant.pk)
        fresh = Participant.objects.using(BOOTH_DB).get(pk=participant.pk)
        if mutate(fresh) is not False:
            fresh.save(using=BOOTH_DB)
        return fresh

    fresh = _retry_write(operation)
    for field in Participant._meta.concrete_fields:
        setattr(participant, field.attname, getattr(fresh, field.attname))
    participant._state.db = BOOTH_DB
    participant._state.adding = False
    return participant


# ---------------------------------------------------------------------------
# 발급
# ---------------------------------------------------------------------------
def _current_max_number():
    return Participant.objects.using(BOOTH_DB).aggregate(m=Max('number'))['m'] or 0


def issue_participant(source):
    """새 참가자 번호·토큰 발급. source 는 'phone' 또는 'kiosk'.

    max(number)+1 을 트랜잭션 안에서 계산한다. 여러 워커가 같은 번호를 잡으면 unique 제약이
    IntegrityError 로 막고, 최대 5회까지 다시 계산한다. 키오스크 참가자에게는 키오스크 화면 전용
    토큰을 따로 만든다.

    발급은 로그인 없이 누구나 부를 수 있어(QR, 키오스크 버튼) 최근 1시간 발급 수가
    BOOTH_MAX_ISSUE_PER_HOUR 에 닿으면 IssuanceLimited 로 거부한다. 셈은 쓰기 잠금 안에서 해
    여러 워커가 동시에 상한을 넘기지 않는다. 부스 Wi-Fi 는 여러 휴대폰이 IP 하나를 함께 쓰므로
    IP 별 제한은 두지 않는다(필요하면 nginx limit_req 로, README 4-7).
    """
    if source not in Participant.Source.values:
        raise ValueError('source 는 %s 중 하나여야 합니다.' % ', '.join(Participant.Source.values))

    def operation():
        _acquire_write_lock()
        limit = conf.max_issue_per_hour()
        recent = (Participant.objects.using(BOOTH_DB)
                  .filter(created_at__gte=timezone.now() - timedelta(hours=1)).count())
        if recent >= limit:
            raise IssuanceLimited('최근 1시간 발급 수가 상한(%d)에 닿았습니다.' % limit)
        fields = {'number': _current_max_number() + 1, 'source': source}
        if source == Participant.Source.KIOSK:
            fields['kiosk_token'] = new_token()
        return Participant.objects.using(BOOTH_DB).create(**fields)

    try:
        participant = _retry_write(operation, retry_integrity=True)
    except IssuanceLimited:
        logger.warning('[booth] 시간당 발급 상한(%d)에 닿아 번호 발급을 거부했습니다.', conf.max_issue_per_hour())
        raise
    logger.info('[booth] 참가자 발급 %s (%s)', participant.label, source)
    return participant


# ---------------------------------------------------------------------------
# 상태 전이
# ---------------------------------------------------------------------------
def _clear_report(participant):
    participant.report = None
    participant.report_error = ''
    participant.report_started_at = None
    participant.report_done_at = None
    participant.report_attempts = 0


def report_retries_exhausted(participant):
    """더 이상 생성을 시도할 수 없는지: 이번 자료의 시도 횟수 또는 평생 생성 한도를 다 썼다."""
    return (participant.report_attempts >= conf.MAX_REPORT_ATTEMPTS
            or participant.report_generations >= conf.MAX_REPORT_GENERATIONS)


def refresh_report_status(participant, replaced=False, save=False):
    """설문·측정·동의 상태로 report_status 를 다시 계산해 인스턴스에 반영하고 새 상태를 돌려준다.

    replaced: 이미 있던 설문 또는 측정값을 새 값으로 바꿨는지(재제출, 측정 덮어쓰기, 측정 삭제).
    save: True 면 리포트 관련 필드만 저장한다. 기본은 저장하지 않는다(apply_* 가 한 번에 저장).

    규칙
      - 설문이나 측정이 없으면 waiting. 근거 데이터가 없는 리포트는 남기지 않는다.
      - 둘 다 있고 동의가 없으면 no_consent. 기존 리포트도 지운다(동의 철회 재제출 대비).
      - 둘 다 있고 동의가 있으면
          * replaced 면 리포트를 지우고 pending (옛 데이터로 만든 리포트를 보여주지 않는다)
          * waiting/no_consent 면 pending
          * failed 면 pending. 단 시도 횟수·평생 생성 한도에 닿았으면 failed 유지
            (pending 으로 돌려도 claim 이 거부해 화면이 영원히 '생성 중'에 머물기 때문)
          * pending/generating/done 은 그대로(다른 워커의 생성·완료 결과를 되돌리지 않는다)
      - pending 이 되어야 하는데 평생 생성 한도를 다 썼으면 failed(사유 기록). 재제출로 시도
        횟수가 0 이 되어도 비용 상한은 그대로다.

    replaced 로 generating 을 pending 으로 되돌린 경우, 이미 돌고 있던 생성 작업의 결과는
    report.py 의 최종 저장 조건(상태가 여전히 generating 이고 report_started_at 이 같을 때만)에
    걸려 버려진다.
    """
    p = participant
    current = p.report_status

    if not (p.has_survey and p.has_measurement):
        _clear_report(p)
        new_status = Status.WAITING
    elif not p.consent:
        _clear_report(p)
        new_status = Status.NO_CONSENT
    elif replaced:
        _clear_report(p)
        new_status = Status.PENDING
    elif current in (Status.WAITING, Status.NO_CONSENT):
        new_status = Status.PENDING
    elif current == Status.FAILED:
        new_status = Status.FAILED if report_retries_exhausted(p) else Status.PENDING
    else:
        new_status = current

    if new_status == Status.PENDING and p.report_generations >= conf.MAX_REPORT_GENERATIONS:
        new_status = Status.FAILED
        p.report_error = GENERATIONS_EXHAUSTED_ERROR

    p.report_status = new_status
    if save:
        p.save(using=BOOTH_DB, update_fields=list(REPORT_FIELDS) + ['updated_at'])
    return new_status


def apply_survey(participant, answers, submitted_at=None, allow_overwrite=True):
    """설문 응답 저장. 갱신된 participant 를 돌려준다.

    - 동의하지 않았으면 이름·응답·측정값을 저장하지 않고 번호, consent=False, 수신 시각만 남긴다.
      수집 근거(동의)가 없고 리포트도 만들지 않으므로 건강 정보를 14일 동안 둘 이유가 없다.
      측정이 먼저 와 있었다면 값을 지우고 '측정함'(수신 시각)만 남긴다.
    - 거부 후 동의로 다시 제출하면 값 없는 측정은 무효로 돌려(수신 시각을 지움) 다시 측정하게 한다.
    - 이미 설문이 있으면 덮어쓰고 survey_revision += 1. allow_overwrite=False 면
      SurveyOverwriteRefused (확인 코드 없는 제출이 남의 설문을 덮어쓰지 못하게 뷰가 끈다).
    - 저장된 것과 같은 응답(같은 제출 시각)이 다시 오면 아무것도 바꾸지 않는다. Apps Script 는
      응답을 못 받으면 재전송하므로, 같은 제출이 두 번 와도 리포트를 지우고 Claude 를 다시 부르지 않게 한다.
    - submitted_at 이 저장된 설문보다 오래되었으면(늦게 도착한 옛 응답) 무시한다.

    answers 는 normalize_answers 로 정리한다. submitted_at 은 ISO 문자열·datetime·None
    (해석 불가면 서버 수신 시각).
    """
    cleaned = normalize_answers(answers)
    submitted = parse_iso_datetime(submitted_at)
    received_at = submitted or timezone.now()
    name, consent = extract_survey_fields(cleaned)
    if consent and name_question_missing(cleaned):
        logger.warning('[booth] 설문 응답에서 이름 문항(BOOTH_SURVEY_NAME_TITLE)을 찾지 못했습니다. '
                       '리포트는 만들지 않습니다.')
    stored_answers = cleaned if consent else {}
    stored_name = name if consent else ''
    outcome = {'changed': True}

    def mutate(p):
        if p.has_survey:
            if not allow_overwrite:
                raise SurveyOverwriteRefused('이미 설문이 접수되어 있습니다.')
            same = (p.survey_received_at == received_at and p.consent == consent
                    and p.name == stored_name and p.survey_answers == stored_answers)
            older = submitted is not None and received_at < p.survey_received_at
            if same or older:
                outcome['changed'] = False
                return False
        replaced = p.has_survey
        p.survey_answers = stored_answers
        p.name = stored_name
        p.consent = consent
        p.survey_received_at = received_at
        p.survey_revision += 1
        if not consent:
            p.measurement = None
        elif p.measurement is None and p.has_measurement:
            p.measurement_received_at = None
        refresh_report_status(p, replaced=replaced)
        return True

    _update_participant(participant, mutate)
    # 번호와 동의 여부를 함께 남기면 폼 응답(실명)과 이어 '누가 거부했는지'가 로그에 오래 남는다.
    # 그래서 동의·상태는 쓰지 않는다.
    if outcome['changed']:
        logger.info('[booth] 설문 수신 %s rev=%d', participant.label, participant.survey_revision)
    else:
        logger.info('[booth] 설문 중복·지난 전송이라 바꾸지 않음 %s', participant.label)
    return participant


def apply_measurement(participant, measurement, overwrite=False):
    """측정값 저장. 이미 있는데 overwrite=False 면 AlreadyMeasured.

    measurement 는 validate_measurement 를 통과한 dict 여야 한다. 확인은 잠금 안에서 최신
    행으로 하므로, 태블릿 두 대가 동시에 같은 사람에게 보내도 한쪽만 저장된다.
    설문에서 동의하지 않은 참가자는 값을 저장하지 않고 수신 시각만 남긴다. 태블릿 흐름(대기
    목록에서 빠짐, 409 판정)은 그대로 유지하면서 건강 정보는 보관하지 않기 위해서다.
    """
    def mutate(p):
        replaced = p.has_measurement
        if replaced and not overwrite:
            raise AlreadyMeasured('이미 측정값이 있습니다.')
        refused = p.has_survey and not p.consent
        p.measurement = None if refused else measurement
        p.measurement_received_at = timezone.now()
        refresh_report_status(p, replaced=replaced)

    _update_participant(participant, mutate)
    logger.info('[booth] 측정 수신 %s overwrite=%s', participant.label, overwrite)
    return participant


def clear_measurement(participant):
    """잘못된 사람에게 보낸 측정값을 지운다. 그 측정값으로 만든 리포트도 함께 지운다."""
    def mutate(p):
        had = p.has_measurement
        p.measurement = None
        p.measurement_received_at = None
        refresh_report_status(p, replaced=had)

    _update_participant(participant, mutate)
    logger.info('[booth] 측정 삭제 %s', participant.label)
    return participant


def reset_report(participant, clear_measurement=False):
    """스태프 복구(booth_reset_report): 시도 횟수·평생 생성 수·리포트를 지우고 상태를 다시 계산한다.

    최종 실패(시도 5회)나 평생 한도에 닿은 참가자를 스태프가 확인한 뒤 다시 생성할 수 있게 한다.
    clear_measurement=True 면 측정값도 지워 태블릿에서 다시 측정하게 한다(대기 목록에 다시 나타남).
    생성 중이던 워커의 결과는 report_started_at 이 지워져 report._finish 조건에 걸려 버려진다.
    이 함수는 리포트를 만들지 않는다(개인 페이지를 열거나 booth_generate_pending 으로 생성).
    """
    def mutate(p):
        _clear_report(p)
        p.report_generations = 0
        if clear_measurement:
            p.measurement = None
            p.measurement_received_at = None
        # done·generating 을 '그대로 유지'하지 않도록 기준 상태에서 다시 계산한다.
        p.report_status = Status.WAITING
        refresh_report_status(p)

    _update_participant(participant, mutate)
    logger.warning('[booth] 스태프가 리포트를 초기화함 %s clear_measurement=%s',
                   participant.label, clear_measurement)
    return participant


# ---------------------------------------------------------------------------
# 측정 본문 검증
# ---------------------------------------------------------------------------
# 한쪽(before/after) 측정 필드와 허용 범위. 범위 밖 값은 기기 오류로 보고 400 으로 돌려보낸다.
SIDE_FIELDS = (
    'heart_rate', 'rmssd_ms', 'sdnn_ms',
    'sleep_index', 'autonomic_balance', 'stress_recovery_index', 'rr_count',
)
SIDE_RANGES = {
    'heart_rate': (30, 220),
    'rmssd_ms': (0, 500),
    'sdnn_ms': (0, 500),
    'sleep_index': (0, 100),
    'autonomic_balance': (0, 100),
    'stress_recovery_index': (0, 100),
    'rr_count': (0, 1000),
}
SIDE_INT_FIELDS = ('heart_rate', 'rr_count')
_MAX_META_LEN = 100


def _clean_side(value, where):
    if not isinstance(value, dict):
        raise MeasurementInvalid('%s 는 객체여야 합니다.' % where)
    cleaned = {}
    for key in SIDE_FIELDS:                      # 모르는 키는 버린다
        raw = value.get(key)
        if raw is None:
            cleaned[key] = None
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise MeasurementInvalid('%s.%s 는 숫자 또는 null 이어야 합니다.' % (where, key))
        if isinstance(raw, float) and not math.isfinite(raw):
            raise MeasurementInvalid('%s.%s 값이 유한한 숫자가 아닙니다.' % (where, key))
        low, high = SIDE_RANGES[key]
        try:
            if key in SIDE_INT_FIELDS:
                # 앱이 평균 심박수 등을 double 로 계산해 보낼 수 있어 반올림해 받는다.
                number = int(round(raw))
            else:
                # JSON 의 아주 큰 정수(10**400 등)는 float 로 바꿀 때 OverflowError 가 난다. 500 이 아니라 400.
                number = float(raw)
        except (OverflowError, ValueError):
            raise MeasurementInvalid('%s.%s 값이 허용 범위(%s~%s)를 벗어났습니다.' % (where, key, low, high))
        if not low <= number <= high:
            raise MeasurementInvalid('%s.%s 값이 허용 범위(%s~%s)를 벗어났습니다.' % (where, key, low, high))
        cleaned[key] = number
    return cleaned


def _clean_meta(value, key):
    if value is None:
        return None
    if not isinstance(value, str):
        raise MeasurementInvalid('%s 는 문자열이어야 합니다.' % key)
    return value.strip()[:_MAX_META_LEN]


def validate_measurement(payload):
    """태블릿이 보낸 measurement 본문을 검사해 저장용 dict 로 정리한다.

    반환 형태(항상 이 키들을 가진다):
      {"measured_at": KST ISO 문자열, "device_id": str|None, "app_version": str|None,
       "before": SIDE, "after": SIDE, "displayed": {"before": SIDE, "after": SIDE} 또는 None}
    SIDE 는 SIDE_FIELDS 7개 키를 모두 가지며 값은 숫자 또는 None.
    잘못되면 MeasurementInvalid(한국어 메시지).
    """
    if not isinstance(payload, dict):
        raise MeasurementInvalid('measurement 는 객체여야 합니다.')

    measured_at = parse_iso_datetime(payload.get('measured_at')) if isinstance(payload.get('measured_at'), str) else None
    if measured_at is None:
        raise MeasurementInvalid('measured_at 은 ISO 8601 날짜시간 문자열(2000~2100년)이어야 합니다.')

    displayed = payload.get('displayed')
    if displayed is not None:
        if not isinstance(displayed, dict):
            raise MeasurementInvalid('displayed 는 객체 또는 null 이어야 합니다.')
        displayed = {
            'before': _clean_side(displayed.get('before'), 'displayed.before'),
            'after': _clean_side(displayed.get('after'), 'displayed.after'),
        }

    return {
        'measured_at': kst_isoformat(measured_at),
        'device_id': _clean_meta(payload.get('device_id'), 'device_id'),
        'app_version': _clean_meta(payload.get('app_version'), 'app_version'),
        'before': _clean_side(payload.get('before'), 'before'),
        'after': _clean_side(payload.get('after'), 'after'),
        'displayed': displayed,
    }


# ---------------------------------------------------------------------------
# 전/후 비교표용 값 선택 (페이지 표 + AI 프롬프트 공용)
# ---------------------------------------------------------------------------
DISPLAYED_CAPTION = '체험 화면 표시값 (시연용 보정 포함)'
RAW_CAPTION = '측정값'

# 리포트 비교표의 행 순서와 표기. 표는 서버가 만들고 AI 는 숫자를 만들지도 받지도 않는다.
COMPARISON_ROWS = (
    ('sleep_index', 'Sleep Index'),
    ('autonomic_balance', 'Autonomic Balance'),
    ('stress_recovery_index', 'Stress/Recovery Index'),
    ('heart_rate', '심박수 (bpm)'),
)

DisplaySides = namedtuple('DisplaySides', ['caption', 'before', 'after', 'is_displayed'])


def display_sides(measurement):
    """비교표·프롬프트에 쓸 전/후 값.

    체험 화면에 실제로 보여준 값(displayed, 시연용 보정 포함)이 있으면 그것을, 없으면
    원 측정값(before/after)을 쓴다. 체험자가 태블릿에서 본 숫자와 리포트 숫자가 달라
    혼란스러운 일을 막기 위해 displayed 가 우선이고, 캡션으로 출처를 반드시 밝힌다.
    """
    empty = {key: None for key in SIDE_FIELDS}
    if not isinstance(measurement, dict):
        return DisplaySides(RAW_CAPTION, empty, dict(empty), False)
    displayed = measurement.get('displayed')
    if isinstance(displayed, dict) and isinstance(displayed.get('before'), dict) \
            and isinstance(displayed.get('after'), dict):
        return DisplaySides(DISPLAYED_CAPTION, displayed['before'], displayed['after'], True)
    before = measurement.get('before') if isinstance(measurement.get('before'), dict) else empty
    after = measurement.get('after') if isinstance(measurement.get('after'), dict) else dict(empty)
    return DisplaySides(RAW_CAPTION, before, after, False)
