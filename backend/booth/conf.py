# -*- coding:utf-8 -*-
"""booth 앱 설정값.

모든 값은 '호출 시점'에 os.environ 에서 읽는다. 모듈 로드 시점에 상수로 굳혀두면
테스트에서 mock.patch.dict(os.environ) 로 바꿔도 반영되지 않는다. 운영에서는 backend/.env 에
넣는다. settings.py 가 django-environ 으로 .env 를 os.environ 에 올리므로 uWSGI, manage.py,
cron 이 모두 같은 값을 본다. 바꾼 뒤에는 uWSGI 를 재시작한다.

잘못된 숫자 값은 예외 대신 기본값으로 떨어뜨린다. 부스 현장에서 env 오타 하나로
페이지 전체가 500 이 나는 것보다, 기본값으로 동작하며 경고 로그를 남기는 편이 낫다.
"""
import logging
import os

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 코드 상수 (env 로 바꾸지 않는 값)
# ---------------------------------------------------------------------------
BOOTH_DB = 'booth'                      # settings.DATABASES 의 booth 전용 SQLite 별칭

# __Secure- 접두어: 브라우저는 이 이름의 쿠키를 https 응답이 Secure 속성과 함께 심을 때만 받는다.
# 평문 http 응답(포트 8000, 부스 Wi-Fi 의 중간자 등)이 남의 토큰을 심어 다른 사람의 페이지로
# 보내는 일을 브라우저 단계에서 막는다.
TOKEN_COOKIE_NAME = '__Secure-booth_token'
TOKEN_COOKIE_MAX_AGE = 12 * 60 * 60     # 12시간: 부스 운영 하루를 넘기지 않는다

# 리포트 생성 시간 예산. 살아 있는 워커의 최장 수명은
#   마감(BOOTH_REPORT_DEADLINE_SEC, 최대 200) + 읽기 타임아웃 60 + 연결 10 = 270초 안쪽이다
#   (report.call_claude 가 스트림을 여는 재시도까지 마감 안에서만 한다).
# 멈춤 판정은 그보다 길어야 살아 있는 워커의 작업을 다른 워커가 가져가 Claude 를 두 번 부르지 않고,
# 운영 uWSGI harakiri(480초)보다 짧아야 워커가 죽었을 때 회수가 된다.
REPORT_DEADLINE_MAX_SEC = 200
STALE_GENERATING_SEC = 300
MAX_REPORT_ATTEMPTS = 5                 # 같은 설문·측정으로 시도할 수 있는 횟수. 넘기면 failed 로 멈춘다
# 참가자 한 명이 평생 부를 수 있는 생성 횟수. 설문 재제출·측정 덮어쓰기로 초기화되지 않는다.
# MAX_REPORT_ATTEMPTS 는 재제출마다 0 이 되므로 비용 상한 역할을 못 한다(공개 폼을 반복 제출하면 무한 호출).
MAX_REPORT_GENERATIONS = 8
REPORT_ERROR_MAX_LEN = 300              # Participant.report_error max_length 와 같다

# 공용 키오스크: 설문 접수 후 첫 화면 복귀까지, 방치 시 자동 복귀까지의 시간.
# 키오스크 토큰은 이 시간(+여유)이 지나면 더 이상 열리지 않는다(views_pages, services 공용).
KIOSK_RETURN_DELAY_SEC = 8
KIOSK_ABANDON_SEC = 10 * 60
KIOSK_TOKEN_GRACE_SEC = 120

KST_TZ_NAME = 'Asia/Seoul'              # 화면 표기용. settings.TIME_ZONE(UTC)은 건드리지 않는다


def _str(name, default=''):
    """문자열 env. 비어 있거나 공백뿐이면 기본값."""
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _int(name, default, minimum=None, maximum=None):
    """정수 env. 해석 불가·범위 밖이면 경고 후 기본값."""
    raw = (os.environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning('[booth] %s 값을 정수로 읽을 수 없어 기본값 %s 를 씁니다.', name, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning('[booth] %s 값이 최솟값 %s 미만이라 기본값 %s 를 씁니다.', name, minimum, default)
        return default
    if maximum is not None and value > maximum:
        logger.warning('[booth] %s 값이 최댓값 %s 를 넘어 기본값 %s 를 씁니다.', name, maximum, default)
        return default
    return value


# ---------------------------------------------------------------------------
# 전송 보안
# ---------------------------------------------------------------------------
def allow_insecure():
    """BOOTH_ALLOW_INSECURE=1 일 때만 http 요청을 받는다(로컬 개발·테스트 전용).

    운영 서버는 nginx 8000 포트가 평문 http 로 모든 경로를 Django 에 넘긴다. 건강 정보가 담긴
    booth 페이지·API 가 그 경로로 열리지 않도록 기본은 https 가 아니면 거부한다.
    """
    return _str('BOOTH_ALLOW_INSECURE') == '1'


# ---------------------------------------------------------------------------
# 외부 연동 (API 키, 구글 폼)
# ---------------------------------------------------------------------------
def api_key():
    """체험 태블릿 앱이 X-Booth-Key 로 보내야 하는 키. 비어 있으면 API 는 503."""
    return _str('BOOTH_API_KEY')


def form_api_key():
    """구글 Apps Script 전용 키. 설문 수신 API 만 받는다.

    Apps Script 속성은 폼 편집자 모두에게 보인다. 태블릿 키와 나눠 두면 그 키로는 대기 목록·
    개인 페이지 주소(report_url)를 읽을 수 없다. 비어 있으면 설문 수신도 BOOTH_API_KEY 로 받는다.
    """
    return _str('BOOTH_FORM_API_KEY')


def form_url():
    """구글 폼 응답 URL (예: https://docs.google.com/forms/d/e/.../viewform)."""
    return _str('BOOTH_FORM_URL')


def form_number_entry():
    """참가자 번호 문항의 미리 채우기 파라미터 이름 (예: entry.1234567)."""
    return _str('BOOTH_FORM_NUMBER_ENTRY')


def form_code_entry():
    """확인 코드 문항의 미리 채우기 파라미터 이름. 설정하면 코드 없는 설문 제출을 거부한다."""
    return _str('BOOTH_FORM_CODE_ENTRY')


def number_prefix():
    return _str('BOOTH_NUMBER_PREFIX', 'SF')


def survey_name_title():
    """이름 문항 제목. '-' 는 '이름 문항이 없는 폼'이라는 명시적 설정이라 '' 를 돌려준다."""
    value = _str('BOOTH_SURVEY_NAME_TITLE', '이름')
    return '' if value == '-' else value


def survey_consent_title():
    return _str('BOOTH_SURVEY_CONSENT_TITLE', '개인정보 수집·이용 동의')


def survey_consent_titles():
    """반드시 모두 '동의'여야 하는 동의 문항 제목 목록.

    개인정보보호법은 수집·이용, 민감정보, 국외 이전 동의를 따로 받게 한다. 폼에 동의 문항이
    여러 개면 BOOTH_SURVEY_CONSENT_TITLES 에 '|' 로 이어 적는다. 없으면 BOOTH_SURVEY_CONSENT_TITLE
    하나를 쓴다(이전 설정과 호환).
    """
    raw = _str('BOOTH_SURVEY_CONSENT_TITLES')
    if raw:
        titles = [title.strip() for title in raw.split('|') if title.strip()]
        if titles:
            return titles
    return [survey_consent_title()]


def survey_number_title():
    return _str('BOOTH_SURVEY_NUMBER_TITLE', '참가자 번호')


def survey_code_title():
    return _str('BOOTH_SURVEY_CODE_TITLE', '확인 코드')


# ---------------------------------------------------------------------------
# 운영 정책
# ---------------------------------------------------------------------------
def retention_days():
    """생성일로부터 이 일수가 지나면 조회 불가 + booth_purge 삭제 대상."""
    return _int('BOOTH_RETENTION_DAYS', 14, minimum=1)


def pending_window_hours():
    """태블릿 대기 목록에 보일 참가자의 생성 시각 범위(최근 N시간)."""
    return _int('BOOTH_PENDING_WINDOW_HOURS', 12, minimum=1)


def max_issue_per_hour():
    """최근 1시간 동안 발급할 수 있는 참가자 번호 수. 넘으면 503(익명 대량 발급 방지)."""
    return _int('BOOTH_MAX_ISSUE_PER_HOUR', 300, minimum=1)


def public_base_url():
    """report_url 의 절대 주소 기준 (https:// 로 시작해야 쓰인다, services.build_report_url).

    QR 주소가 요청이 들어온 포트(8443 등)나 Host 헤더에 따라 달라지지 않게 고정해 둔다.
    끝의 '/' 는 떼어 경로와 이어붙일 때 '//' 가 생기지 않게 한다.
    """
    return _str('BOOTH_PUBLIC_BASE_URL').rstrip('/')


# ---------------------------------------------------------------------------
# AI 리포트 (Claude)
# ---------------------------------------------------------------------------
def report_model():
    return _str('BOOTH_REPORT_MODEL', 'claude-opus-5')


def report_effort():
    return _str('BOOTH_REPORT_EFFORT', 'medium')


def report_max_tokens():
    return _int('BOOTH_REPORT_MAX_TOKENS', 16000, minimum=1)


def report_deadline_sec():
    """스트림을 끊는 마감(초, 10~200). 위 STALE_GENERATING_SEC 계산의 전제라 200 을 넘기지 않는다."""
    return _int('BOOTH_REPORT_DEADLINE_SEC', 150, minimum=10, maximum=REPORT_DEADLINE_MAX_SEC)


def max_concurrent_reports():
    """동시에 생성 중일 수 있는 리포트 수.

    생성은 요청을 받은 uWSGI 워커를 붙잡는다. 운영 워커 5개는 OneClickRemote 중계·다른 앱과
    함께 쓰므로, 부스 리포트가 워커를 모두 차지하지 않게 기본 2 로 막는다.
    """
    return _int('BOOTH_MAX_CONCURRENT_REPORTS', 2, minimum=1)


def report_max_per_hour():
    """최근 1시간 동안 시작할 수 있는 리포트 생성 수(전체 비용 상한)."""
    return _int('BOOTH_REPORT_MAX_PER_HOUR', 60, minimum=1)


def anthropic_api_key():
    return _str('ANTHROPIC_API_KEY')
