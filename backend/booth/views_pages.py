# -*- coding:utf-8 -*-
"""체험자용 HTML 페이지와, 그 페이지가 부르는 JSON(상태 조회·리포트 생성 요청).

로그인이 없는 공개 부스 서비스다. 개인 페이지 URL 의 토큰(192비트)이 곧 '이름·리포트 열람
권한'이므로 이 모듈의 원칙은 토큰 URL 과 그 내용이 새지 않게 하는 것이다.
  - https 로 들어온 요청만 처리한다(booth_page). 운영 nginx 의 8000 포트는 평문 http 로 모든
    경로를 Django 에 넘기므로, 앱이 거부하지 않으면 이름·건강 정보·토큰이 암호화 없이 오간다.
    443(Let's Encrypt)·8443 은 uwsgi_params 가 HTTPS 를 넘겨 request.is_secure() 가 True 다.
  - 모든 응답에 Referrer-Policy: no-referrer. 개인 페이지는 구글 폼으로 나가는 링크가 있고,
    키오스크 페이지는 구글 폼을 iframe 으로 띄운다. Referer 로 토큰 URL 이 구글에 넘어가면 안 된다.
  - Cache-Control: no-store. 공용 키오스크 태블릿·공유 휴대폰에서 뒤로가기/캐시로 이전 사람의
    화면이 다시 보이지 않게 한다.
  - X-Robots-Tag: noindex. 토큰 URL 이 어떤 경로로든 크롤러에 닿아도 색인되지 않게 한다.
  - 운영이 DEBUG=True 라, 처리하지 못한 예외가 Django 까지 가면 지역 변수(이름·토큰)와 설정이 담긴
    디버그 페이지가 나간다. booth_page 가 모든 예외를 받아 고정 문구의 500 화면으로 끝낸다.
  - 공용 키오스크 화면은 개인 페이지 토큰을 쓰지 않고 kiosk_token 만 쓴다.
  - AI 가 만든 문장은 템플릿 자동 이스케이프로만 출력한다(mark_safe 금지).

상태 전이는 전부 booth.services 가, 리포트 생성은 booth.report 가 맡는다. 이 모듈은
참가자를 조회해 어떤 화면을 보여줄지 고르고, 표시용 값을 정리하는 일만 한다.
"""
import logging
import math
from datetime import timedelta
from functools import wraps
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.db import DatabaseError
from django.http import (Http404, HttpResponse, HttpResponseNotAllowed, HttpResponsePermanentRedirect,
                         HttpResponseRedirect, JsonResponse)
from django.shortcuts import render
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt

from . import conf, services
from . import report as booth_report     # generate_report 를 호출 시점에 찾아야 테스트 mock 이 먹는다
from .models import Participant
from .report_schema import INDEX_GLOSSES, INDEX_LABELS, ReportInvalid, validate_report

logger = logging.getLogger(__name__)

Status = Participant.ReportStatus

# 개인 페이지가 상태를 확인하는 간격. 리포트 생성(1~2분)을 기다리는 동안 워커를 과하게
# 두드리지 않으면서, 측정이 끝난 뒤 화면이 늦지 않게 바뀌는 정도.
PERSONAL_POLL_MS = 4000
# 화면이 보이는 동안 이 시간이 지나면 폴링을 멈춘다. 휴대폰에 열어둔 채 잊힌 탭이 하루 종일
# 서버를 부르지 않게 한다. 탭이 다시 보이면(visibilitychange) 처음부터 다시 센다.
PERSONAL_MAX_POLL_SEC = 30 * 60
# 생성 요청이 '대기(pending)'로 끝났을 때(동시 생성·시간당 상한, 생성 중 자료 변경) 페이지가 다시
# 요청하기까지의 시간. 이 대기는 페이지의 생성 요청 횟수 제한에 넣지 않는다.
GENERATE_RETRY_AFTER_SEC = 20

# 키오스크: 설문 접수 확인 간격, 접수 후 첫 화면 복귀까지의 시간, 방치 시 자동 복귀 시간.
# 자동 복귀는 작성하다 자리를 뜬 사람의 이름이 다음 사람에게 보이지 않게 하려는 것이다.
# 교차 출처 iframe 안의 입력은 부모 페이지가 감지할 수 없어 '마지막 입력 후'가 아닌
# '페이지를 연 뒤' 기준으로 잰다. 설문 작성에 보통 3~5분이면 충분하다.
KIOSK_POLL_MS = 3000
KIOSK_RETURN_DELAY_SEC = conf.KIOSK_RETURN_DELAY_SEC
KIOSK_ABANDON_SEC = conf.KIOSK_ABANDON_SEC

HTTPS_REQUIRED_MESSAGE = 'HTTPS 로만 사용할 수 있습니다.'
NOT_FOUND_JSON_MESSAGE = '요청한 대상을 찾을 수 없습니다.'
SERVER_ERROR_JSON_MESSAGE = '서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.'

SECURITY_HEADERS = {
    'Cache-Control': 'no-store',
    'Referrer-Policy': 'no-referrer',
    'X-Robots-Tag': 'noindex',
}

# 템플릿조차 렌더링하지 못할 때(FTP 부분 업로드로 템플릿 누락 등) 쓰는 마지막 응답.
_FALLBACK_HTML = (
    '<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<meta name="referrer" content="no-referrer"><meta name="robots" content="noindex, nofollow">'
    '<title>%(title)s</title></head><body><h1>%(title)s</h1><p>%(body)s</p></body></html>'
)


# ---------------------------------------------------------------------------
# 공통 도우미
# ---------------------------------------------------------------------------
def _json(data, status=200):
    return JsonResponse(data, status=status, json_dumps_params={'ensure_ascii': False})


def _message_page(request, title, lines, status, action_url='', action_label=''):
    try:
        return render(request, 'booth/message.html', {
            'title': title,
            'lines': lines,
            'action_url': action_url,
            'action_label': action_label,
        }, status=status)
    except Exception as exc:  # noqa: BLE001 - 안내 화면마저 실패해도 디버그 페이지는 내보내지 않는다
        services.log_exception_safely(logger, '[booth] 안내 화면 렌더링 실패', exc)
        body = _FALLBACK_HTML % {'title': escape(title), 'body': escape(' '.join(lines))}
        return HttpResponse(body, status=status, content_type='text/html; charset=utf-8')


def _kiosk_action(kiosk):
    return (reverse('booth:kiosk_home'), '처음으로') if kiosk else ('', '')


def _not_found_page(request, kiosk=False):
    # 없는 토큰과 보유기간이 지난 토큰을 구분하지 않는다. 구분해 주면 토큰 존재 여부를 알려주는 셈이다.
    lines = ['주소가 올바르지 않거나, 보유기간이 지나 정보가 삭제되었습니다.']
    return _message_page(request, '페이지를 찾을 수 없습니다', lines, 404, *_kiosk_action(kiosk))


def _service_unavailable_page(request, lines, kiosk=False):
    return _message_page(request, '잠시 후 다시 시도해 주세요', lines, 503, *_kiosk_action(kiosk))


def _server_error_response(request, as_json, kiosk=False):
    if as_json:
        return _json({'error': SERVER_ERROR_JSON_MESSAGE}, status=500)
    lines = ['요청을 처리하는 중 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.',
             '계속되면 부스 스태프에게 알려 주세요.']
    return _message_page(request, '일시적인 오류가 발생했습니다', lines, 500, *_kiosk_action(kiosk))


def _https_required_response(request, as_json):
    if as_json:
        return _json({'error': HTTPS_REQUIRED_MESSAGE}, status=403)
    lines = ['이 페이지는 https:// 로 시작하는 보안 주소로만 열 수 있습니다.',
             '부스에 있는 QR 코드를 다시 스캔해 주세요.']
    return _message_page(request, '보안 연결(HTTPS)이 필요합니다', lines, 403)


def _finalize(response):
    """보안 헤더를 붙이고, Django 요청 로그(django.request)가 이 응답을 기록하지 않게 표시한다.

    booth 경로에는 개인 페이지 토큰이 들어가는데, DEBUG=True 인 운영에서 django.request 는 4xx·5xx 응답마다
    경로를 로그에 쓴다('Not Found: /booth/p/<토큰>/'). 로그를 볼 수 있는 사람이 14일 동안 남의 이름·리포트를
    열 수 있게 되므로 끈다. booth 는 필요한 사건을 번호·뷰 이름으로 따로 남긴다.
    _has_been_logged 는 Django 가 같은 응답을 두 번 기록하지 않으려고 확인하는 내부 표시다. 이 표시를
    보지 않는 버전이라면 효과만 없고 동작은 같다(uWSGI·nginx 요청 로그는 README 2 참고).
    """
    for header, value in SECURITY_HEADERS.items():
        response[header] = value
    response._has_been_logged = True
    return response


def is_secure_enough(request):
    """https 요청이거나, 로컬 개발·테스트용 BOOTH_ALLOW_INSECURE=1 인지."""
    return request.is_secure() or conf.allow_insecure()


SAFE_METHODS = ('GET', 'HEAD')


def booth_page(json_errors=False, kiosk=False, methods=None):
    """booth 페이지·페이지용 JSON 뷰의 공통 바깥 껍질.

    1) https 가 아니면 뷰를 실행하지 않고 403(짧은 한국어 안내). BOOTH_ALLOW_INSECURE=1 이면 통과.
       https 로 리다이렉트하지 않는다. 리다이렉트로는 이미 평문으로 보낸 토큰 URL·본문을 되돌릴 수
       없고, 사용자가 평문 주소를 계속 쓰게 된다.
    2) Http404 는 booth 404, 그 밖의 모든 예외는 고정 문구 500 으로 끝낸다(DEBUG 디버그 페이지 차단).
       로그에는 뷰 이름, 예외 종류, 코드 위치만 남긴다(services.log_exception_safely).
    3) 결과 응답(HTML·JSON·리다이렉트·405)에 보안 헤더를 붙이고 django.request 로그에서 뺀다(_finalize).
       리다이렉트에도 헤더를 붙이는 이유:
       브라우저는 3xx 응답의 Referrer-Policy 로 다음 요청의 정책을 갱신한다. /survey/ 가 구글 폼으로
       보내는 302 에 no-referrer 가 있어야 토큰 URL 이 구글로 넘어가지 않는다. SecurityMiddleware 는
       Referrer-Policy 를 setdefault 로 넣으므로 여기서 넣은 값이 그대로 유지된다.

    json_errors: 오류를 JSON 으로 낼지. bool 또는 request 를 받아 bool 을 돌려주는 함수.
    kiosk: 오류 화면에 키오스크 '처음으로' 버튼을 둘지.
    methods: 허용할 HTTP 메서드(None 이면 모두). Django 의 require_POST·require_safe 는 쓰지 않는다.
      그 데코레이터는 405 를 요청 경로(개인 페이지 토큰 포함)와 함께 django.request 로그에 직접 쓰기 때문이다.
    """
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            as_json = json_errors(request) if callable(json_errors) else json_errors
            if not is_secure_enough(request):
                # 경로에는 토큰이 있을 수 있어 뷰 이름만 남긴다.
                logger.warning('[booth] https 가 아닌 요청 거부 (%s)', view.__name__)
                return _finalize(_https_required_response(request, as_json))
            try:
                if methods is not None and request.method not in methods:
                    response = HttpResponseNotAllowed(methods)
                else:
                    response = view(request, *args, **kwargs)
            except Http404:
                response = (_json({'error': NOT_FOUND_JSON_MESSAGE}, status=404) if as_json
                            else _not_found_page(request, kiosk))
            except Exception as exc:  # noqa: BLE001 - 디버그 페이지 대신 고정 문구로 끝낸다
                services.log_exception_safely(logger, '[booth] 페이지 처리 오류 %s' % view.__name__, exc)
                response = _server_error_response(request, as_json, kiosk)
            return _finalize(response)
        return wrapper
    return decorator


def _redirect_see_other(url):
    # POST 뒤에는 303 으로 보내 새로고침이 POST(=새 번호 발급)를 반복하지 않게 한다.
    response = HttpResponseRedirect(url)
    response.status_code = 303
    return response


def _booth_root_path():
    """booth URL 접두어('/booth/'). 쿠키 path 와 미정의 경로 판별에 쓴다."""
    start_path = reverse('booth:start')
    return start_path[:-len('start/')] if start_path.endswith('start/') else '/'


def _reload(participant):
    return Participant.objects.using(services.BOOTH_DB).filter(pk=participant.pk).first()


# ---------------------------------------------------------------------------
# 구글 폼 미리 채우기 주소
# ---------------------------------------------------------------------------
def _entry_name(entry):
    """'entry.1234567' 형태로. 숫자만 넣은 설정도 받아준다."""
    if entry.isdigit():
        return 'entry.' + entry
    return entry


def build_form_url(participant, embedded=False):
    """참가자 번호·확인 코드를 미리 채운 구글 폼 주소. BOOTH_FORM_URL 이 없거나 http(s)가 아니면 ''.

    BOOTH_FORM_URL 에 이미 붙어 있는 usp·embedded·번호·코드 entry 파라미터는 지우고 다시 붙인다.
    구글 폼 '미리 채운 링크'를 통째로 복사해 넣는 실수를 해도 값이 중복되지 않게 하기 위해서다.
    값은 urlencode 로 인코딩한다(접두어에 공백·& 가 있어도 쿼리가 깨지지 않는다).
    """
    base = conf.form_url()
    if not base:
        return ''
    parts = urlsplit(base)
    # iframe src·Location 에 들어가는 값이라 javascript: 같은 스킴은 설정 오류로 본다.
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        logger.warning('[booth] BOOTH_FORM_URL 이 http(s) 주소가 아니라 무시합니다.')
        return ''

    number_entry = _entry_name(conf.form_number_entry())
    code_entry = _entry_name(conf.form_code_entry())
    replaced = {'usp', 'embedded'} | {entry for entry in (number_entry, code_entry) if entry}
    params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in replaced]
    params.append(('usp', 'pp_url'))
    if number_entry:
        params.append((number_entry, participant.label))
    else:
        # 번호가 미리 채워지지 않아도 체험자가 화면의 번호를 직접 적으면 매칭은 된다. 막지 않고 알린다.
        logger.warning('[booth] BOOTH_FORM_NUMBER_ENTRY 가 비어 있어 참가자 번호를 미리 채우지 못합니다.')
    if code_entry:
        params.append((code_entry, services.survey_code(participant)))
    if embedded:
        params.append(('embedded', 'true'))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))


# ---------------------------------------------------------------------------
# 개인 페이지 표시 상태
# ---------------------------------------------------------------------------
def _page_state(participant):
    """개인 페이지에 보여줄 화면.

    before_survey  설문 전: 번호와 설문하기 버튼
    no_consent     설문은 왔지만 동의 없음. 측정 전이라도 바로 알려 체험자가 다시 설문할 수 있게 한다
    after_survey   설문 접수, 측정 대기: 체험 태블릿으로 안내
    generating     설문·측정·동의가 모두 있고 리포트 대기/생성 중: JS 가 생성을 요청하고 폴링
    failed         생성 실패(재시도 가능)
    failed_final   시도 횟수 또는 평생 생성 한도 도달. claim 이 거부하므로 버튼을 보여주지 않는다
    done           리포트 완료 (저장된 리포트가 깨져 있으면 personal() 이 broken 으로 바꾼다)
    """
    p = participant
    if not p.has_survey:
        return 'before_survey'
    if not p.consent:
        return 'no_consent'
    if not p.has_measurement:
        return 'after_survey'
    if p.report_status == Status.DONE:
        return 'done'
    if p.report_status == Status.FAILED:
        return 'failed_final' if services.report_retries_exhausted(p) else 'failed'
    return 'generating'


def _status_payload(participant):
    """개인 페이지 폴링용 상태 JSON. 이름·토큰·번호는 넣지 않는다.

    state 는 화면 종류다. 측정 전에 동의 여부만 바뀐 재제출은 세 값(설문·측정·리포트 상태)이 그대로라
    state 없이는 페이지가 바뀐 줄 모르고 옛 화면('설문 다시 작성하기')에 머문다.
    """
    return {
        'survey_received': participant.has_survey,
        'measurement_received': participant.has_measurement,
        'report_status': str(participant.report_status),
        'state': _page_state(participant),
    }


def _can_generate(participant):
    """generate 요청 시 report 모듈을 부를 가치가 있는지. 최종 판정은 report 의 DB claim 이 한다.

    generating 도 포함한다. 생성하던 워커가 죽어 오래된 generating 은 claim 이 다시 가져갈 수
    있고, 정상 진행 중이면 claim 이 즉시 거절해 비용이 거의 없다.
    """
    p = participant
    if not (p.has_survey and p.has_measurement and p.consent):
        return False
    if p.report_status in (Status.PENDING, Status.GENERATING):
        return True
    return p.report_status == Status.FAILED and not services.report_retries_exhausted(p)


def format_value(value):
    """비교표 숫자 표기. 정수는 그대로, 실수는 소수 첫째 자리까지(.0 은 뗀다), 없으면 '-'."""
    if value is None or isinstance(value, bool):
        return '-'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        rounded = round(value, 1)
        return str(int(rounded)) if rounded.is_integer() else '%.1f' % rounded
    return '-'


def _report_context(participant):
    """완료된 리포트 화면용 값. 저장된 리포트가 구조와 맞지 않으면 None.

    전/후 비교표는 AI 출력이 아니라 서버가 측정값으로 직접 만든다(AI 에는 숫자를 보내지도 않는다).
    캡션 규칙(표시값/측정값)은 services.display_sides 한 곳에서 정한다. 변화량 열은 일부러 두지 않는다.
    화면 표시값에는 시연용 보정이 들어 있어 차이를 개인의 효과처럼 읽게 만들면 안 된다.
    화면에서는 validate_report 를 느슨하게(strict=False) 써서, 저장 뒤에 규칙이 엄격해져도 이미
    만든 리포트가 '표시할 수 없음'이 되지 않게 한다.
    """
    p = participant
    try:
        report = validate_report(p.report)
    except ReportInvalid as exc:
        logger.warning('[booth] 저장된 리포트 형식 오류 %s (%s)', p.label, exc)
        return None

    sides = services.display_sides(p.measurement)
    rows = [
        {
            'label': label,
            'gloss': INDEX_GLOSSES.get(key, ''),
            'before': format_value(sides.before.get(key)),
            'after': format_value(sides.after.get(key)),
        }
        for key, label in services.COMPARISON_ROWS
    ]
    indices = [dict(item, label=INDEX_LABELS.get(item['key'], item['key']), gloss=INDEX_GLOSSES.get(item['key'], ''))
               for item in report['indices']]
    experienced_at = services.kst(p.measurement_received_at or p.created_at)
    return {
        'report': report,
        'indices': indices,
        'caption': sides.caption,
        'is_displayed': sides.is_displayed,
        'rows': rows,
        'report_date': experienced_at.strftime('%Y-%m-%d'),
    }


# ---------------------------------------------------------------------------
# 휴대폰: QR 시작 -> 개인 페이지
# ---------------------------------------------------------------------------
def _participant_from_cookie(request):
    """12시간 안에 이 휴대폰에서 발급한 참가자. QR 을 다시 찍어도 같은 번호로 돌아오게 한다."""
    participant = services.get_participant_by_token(request.COOKIES.get(conf.TOKEN_COOKIE_NAME))
    if participant is None:
        return None
    # 쿠키 만료는 브라우저가 지키지만, 시계가 틀린 기기도 있어 서버에서도 발급 시각으로 한 번 더 자른다.
    if participant.created_at < timezone.now() - timedelta(seconds=conf.TOKEN_COOKIE_MAX_AGE):
        return None
    return participant


_ISSUE_LIMITED_LINE = '지금은 참가자 번호 발급이 많아 잠시 멈췄습니다. 잠시 후 다시 시도해 주세요.'


@booth_page(methods=SAFE_METHODS)
def start(request):
    """GET /booth/start/ : 인쇄된 QR 의 목적지. 번호를 발급(또는 재사용)하고 개인 페이지로 보낸다.

    ?new=1 은 기존 쿠키를 무시하고 새 번호를 발급한다(가족이 휴대폰 한 대를 같이 쓰는 경우).
    쿠키는 새로 발급할 때만 심는다. 재사용 때 다시 심으면 만료가 계속 연장되어 12시간 제한이 무의미해진다.
    """
    force_new = request.GET.get('new') == '1'
    participant = None if force_new else _participant_from_cookie(request)
    if participant is not None:
        return HttpResponseRedirect(reverse('booth:personal', kwargs={'token': participant.token}))

    try:
        participant = services.issue_participant(Participant.Source.PHONE)
    except services.IssuanceLimited:
        return _service_unavailable_page(request, [_ISSUE_LIMITED_LINE])
    except DatabaseError as exc:
        logger.error('[booth] 휴대폰 번호 발급 실패 (%s)', type(exc).__name__)
        return _service_unavailable_page(request, ['참가자 번호를 발급하지 못했습니다. QR 코드를 다시 스캔해 주세요.'])

    response = HttpResponseRedirect(reverse('booth:personal', kwargs={'token': participant.token}))
    response.set_cookie(
        conf.TOKEN_COOKIE_NAME,
        participant.token,
        max_age=conf.TOKEN_COOKIE_MAX_AGE,
        path=_booth_root_path(),
        httponly=True,
        samesite='Lax',
        # __Secure- 접두어 쿠키는 Secure 속성이 있어야 브라우저가 받는다. booth 는 https 요청만
        # 처리하므로 항상 켠다(443 에서는 uwsgi_params 의 HTTPS 로 is_secure() 도 True 다).
        secure=True,
    )
    return response


@booth_page(methods=SAFE_METHODS)
def personal(request, token):
    """GET /booth/p/<token>/ : 체험자 개인 페이지. 쿠키는 읽지도 심지도 않는다(토큰 URL 만으로 동작).

    태블릿 QR 로 열린 휴대폰, 키오스크로 설문한 사람의 휴대폰에서도 같은 페이지가 열려야 하므로
    쿠키에 의존하지 않는다.
    """
    participant = services.get_participant_by_token(token)
    if participant is None:
        return _not_found_page(request)

    p = participant
    state = _page_state(p)
    kwargs = {'token': p.token}
    context = {
        'state': state,
        'label': p.label,
        # 이름이 비어 있으면(이름 문항이 없는 폼 등) 일반 호칭으로 대신한다.
        'name_display': p.name or '체험자',
        'survey_url': reverse('booth:survey_redirect', kwargs=kwargs),
        'status_url': reverse('booth:status', kwargs=kwargs),
        'generate_url': reverse('booth:generate', kwargs=kwargs),
        'new_url': reverse('booth:start') + '?new=1',
        'deletion_date': services.deletion_date(p).isoformat(),
        # JS 는 렌더링 당시의 이 값들과 상태 JSON 을 비교해 달라졌을 때만 새로고침한다.
        'survey_received': p.has_survey,
        'measurement_received': p.has_measurement,
        'report_status': str(p.report_status),
        'poll_ms': PERSONAL_POLL_MS,
        'max_poll_sec': PERSONAL_MAX_POLL_SEC,
        # 멈춘 generating 을 다시 요청하기까지의 시간. 서버의 멈춤 판정보다 길어야, 아직 살아 있는
        # 워커의 작업을 다른 워커가 가져가 Claude 를 두 번 부르지 않는다.
        'regenerate_after_sec': conf.STALE_GENERATING_SEC + 10,
    }
    if state == 'done':
        report_context = _report_context(p)
        if report_context is None:
            context['state'] = 'broken'
        else:
            context.update(report_context)
    return render(request, 'booth/personal.html', context)


@booth_page(methods=SAFE_METHODS)
def survey_redirect(request, token):
    """GET /booth/p/<token>/survey/ : 번호·확인 코드가 미리 채워진 구글 폼으로 보낸다.

    개인 페이지에 폼 주소를 직접 박지 않고 한 단계 거치는 이유: 폼 주소·entry 설정을 바꿔도
    이미 열려 있는 페이지가 새 설정을 따르고, 302 응답의 no-referrer 로 토큰 URL 을 한 번 더 막는다.
    """
    participant = services.get_participant_by_token(token)
    if participant is None:
        return _not_found_page(request)
    url = build_form_url(participant)
    if not url:
        return _service_unavailable_page(request, ['설문 주소가 아직 설정되지 않았습니다. 부스 스태프에게 알려 주세요.'])
    return HttpResponseRedirect(url)


@booth_page(json_errors=True, methods=SAFE_METHODS)
def status(request, token):
    """GET /booth/p/<token>/status/ : 개인 페이지 폴링용 상태 JSON (이름 없음)."""
    participant = services.get_participant_by_token(token)
    if participant is None:
        return _json({'error': '참가자를 찾을 수 없습니다.'}, status=404)
    return _json(_status_payload(participant))


# CSRF 예외 사유(kiosk_new, generate, not_found 공통):
#   - 쿠키 기반 인증이 없다. 브라우저가 자동으로 실어 보내는 권한이 없으니 CSRF 로 훔칠 권한도 없다.
#     generate 의 권한은 URL 경로의 토큰이고, 토큰을 모르는 제3 사이트는 요청 주소를 만들 수 없다.
#   - 페이지의 fetch·폼은 CSRF 토큰을 싣지 않는다(로그인·세션이 없는 공개 페이지).
#   - 두 동작 모두 무해하거나 멈등이다. kiosk_new 는 빈 번호 하나를 만들 뿐이고(시간당 상한과
#     Sec-Fetch-Site 검사가 있다), generate 는 DB claim 으로 한 번만 생성되며 이미 끝났거나 조건이
#     안 되면 아무것도 하지 않는다.
@csrf_exempt
@booth_page(json_errors=True, methods=('POST',))
def generate(request, token):
    """POST /booth/p/<token>/generate/ : 리포트를 동기로 생성하고 상태 JSON 을 돌려준다.

    생성은 최대 BOOTH_REPORT_DEADLINE_SEC(+읽기 타임아웃) 동안 이 요청(워커)을 붙잡는다. 연결이 먼저
    끊겨도 워커는 생성을 끝까지 마치고 저장하므로, 페이지는 상태 폴링으로 완료를 확인한다.
    결과가 pending 이면(동시 생성·시간당 상한으로 미뤄짐 등) retry_after_sec 초 뒤 다시 요청하라고 알린다.
    """
    participant = services.get_participant_by_token(token)
    if participant is None:
        return _json({'error': '참가자를 찾을 수 없습니다.'}, status=404)

    if not _can_generate(participant):
        return _json(_status_payload(participant))

    try:
        booth_report.generate_report(participant)
    except Exception as exc:  # noqa: BLE001 - 어떤 오류든 페이지에는 짧은 JSON 으로만 알린다
        # report 모듈이 오류를 failed 로 저장하지만, claim 단계의 DB 오류 등은 여기까지 올라온다.
        # 예외 메시지에 설문 내용이 섞일 수 있어 종류와 코드 위치만 남긴다.
        services.log_exception_safely(logger, '[booth] 리포트 생성 요청 처리 실패 %s' % participant.label, exc)
        fresh = _reload(participant) or participant
        payload = _status_payload(fresh)
        payload['error'] = '리포트 생성 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.'
        return _json(payload, status=503)

    # report 모듈이 넘겨받은 인스턴스를 갱신하는지에 기대지 않고 DB 의 최종 상태를 읽는다.
    participant = _reload(participant)
    if participant is None:
        return _json({'error': '참가자를 찾을 수 없습니다.'}, status=404)
    payload = _status_payload(participant)
    if participant.report_status == Status.PENDING:
        payload['retry_after_sec'] = GENERATE_RETRY_AFTER_SEC
    return _json(payload)


# ---------------------------------------------------------------------------
# 공용 키오스크 태블릿
# ---------------------------------------------------------------------------
@booth_page(kiosk=True, methods=SAFE_METHODS)
def kiosk_home(request):
    """GET /booth/kiosk/ : '새 체험자 시작' 버튼만 있는 첫 화면. 이전 사람의 정보는 전혀 없다."""
    return render(request, 'booth/kiosk_home.html', {'new_url': reverse('booth:kiosk_new')})


@csrf_exempt     # 사유는 generate 위 주석 참고
@booth_page(kiosk=True)
def kiosk_new(request):
    """POST /booth/kiosk/new/ : 누를 때마다 새 참가자를 발급한다.

    공용 태블릿이므로 booth_token 쿠키를 읽지도 심지도 않는다. 쿠키로 재사용하면 다음 사람이
    앞사람의 번호(와 설문 이름)를 이어받게 된다. 이동할 주소에는 개인 페이지 토큰이 아니라
    키오스크 토큰을 쓴다. 공용 태블릿의 방문 기록에서 앞사람의 개인 페이지를 열 수 없게 하기 위해서다.
    """
    if request.method != 'POST':
        # 주소창에서 직접 열거나 새로고침한 경우. 번호를 만들지 않고 첫 화면으로 돌려보낸다.
        return HttpResponseRedirect(reverse('booth:kiosk_home'))
    # 다른 사이트의 숨은 폼이 방문자 브라우저로 번호를 대량 발급하지 못하게 한다. Sec-Fetch-Site 는
    # 브라우저가 붙이고 페이지 스크립트가 바꿀 수 없다. 헤더가 없는 오래된 브라우저는 통과시킨다.
    fetch_site = request.META.get('HTTP_SEC_FETCH_SITE', '')
    if fetch_site and fetch_site not in ('same-origin', 'none'):
        logger.warning('[booth] 다른 사이트에서 온 키오스크 발급 요청 거부 (%s)', fetch_site[:20])
        return _message_page(request, '요청을 처리할 수 없습니다', ['키오스크 첫 화면에서 다시 시작해 주세요.'],
                             403, *_kiosk_action(True))
    try:
        participant = services.issue_participant(Participant.Source.KIOSK)
    except services.IssuanceLimited:
        return _service_unavailable_page(request, [_ISSUE_LIMITED_LINE], kiosk=True)
    except DatabaseError as exc:
        logger.error('[booth] 키오스크 번호 발급 실패 (%s)', type(exc).__name__)
        return _service_unavailable_page(request, ['참가자 번호를 발급하지 못했습니다.'], kiosk=True)
    return _redirect_see_other(reverse('booth:kiosk_participant', kwargs={'token': participant.kiosk_token}))


@booth_page(kiosk=True, methods=SAFE_METHODS)
def kiosk_participant(request, token):
    """GET /booth/kiosk/p/<kiosk_token>/ : 미리 채운 구글 폼을 iframe 으로 띄우고 설문 접수를 기다린다.

    이 화면에는 이름도 개인 페이지 링크·토큰도 두지 않는다. 설문이 접수되면 폼을 치우고 감사 문구를
    보인 뒤 첫 화면으로 돌아가, 다음 사람이 앞사람의 응답을 볼 수 없게 한다. 개인 리포트는 체험
    태블릿이 보여주는 QR 로 본인 휴대폰에서 연다(휴대폰이 없으면 스태프가 부스 기기로, README 8).
    """
    participant = services.get_participant_by_kiosk_token(token)
    if participant is None:
        return _not_found_page(request, kiosk=True)

    form_url = '' if participant.has_survey else build_form_url(participant, embedded=True)
    if not participant.has_survey and not form_url:
        return _service_unavailable_page(
            request, ['설문 주소가 아직 설정되지 않았습니다. 부스 스태프에게 알려 주세요.'], kiosk=True)

    return render(request, 'booth/kiosk_participant.html', {
        'label': participant.label,
        'form_url': form_url,
        'survey_received': participant.has_survey,
        'status_url': reverse('booth:kiosk_status', kwargs={'token': participant.kiosk_token}),
        'home_url': reverse('booth:kiosk_home'),
        'poll_ms': KIOSK_POLL_MS,
        'return_delay_sec': KIOSK_RETURN_DELAY_SEC,
        'abandon_sec': KIOSK_ABANDON_SEC,
    })


@booth_page(json_errors=True, kiosk=True, methods=SAFE_METHODS)
def kiosk_status(request, token):
    """GET /booth/kiosk/p/<kiosk_token>/status/ : 키오스크 폴링용. 설문 접수 여부만 돌려준다."""
    participant = services.get_participant_by_kiosk_token(token)
    if participant is None:
        return _json({'error': '참가자를 찾을 수 없습니다.'}, status=404)
    return _json({'survey_received': participant.has_survey})


# ---------------------------------------------------------------------------
# /booth/ 아래 미정의 경로 (urls.py 의 마지막 항목)
# ---------------------------------------------------------------------------
def _is_api_path(request):
    return request.path_info.startswith(_booth_root_path() + 'api/')


@csrf_exempt     # 모르는 경로로 온 POST 도 Django 의 CSRF 안내 화면이 아니라 booth 404 로 끝낸다
@booth_page(json_errors=_is_api_path)
def not_found(request):
    """booth 아래 어떤 경로에도 맞지 않는 요청. API 경로는 JSON 404, 나머지는 한국어 404 화면.

    끝의 '/' 만 빠진 GET·HEAD 는 올바른 주소로 301 한다(주소를 직접 친 경우의 편의). POST 는 본문이
    리다이렉트로 사라지므로 404 로 끝낸다. 이 경로가 항상 맞으므로 CommonMiddleware 의 APPEND_SLASH
    처리(DEBUG=True 에서 POST 에 설정값이 담긴 500)도 일어나지 않는다.
    """
    root = _booth_root_path()
    if request.method in ('GET', 'HEAD') and not request.path_info.endswith('/'):
        try:
            match = resolve(request.path_info + '/')
        except Resolver404:
            match = None
        if match is not None and match.namespace == 'booth' and match.url_name != 'not_found':
            query = request.META.get('QUERY_STRING', '')
            return HttpResponsePermanentRedirect(request.path + '/' + ('?' + query if query else ''))
    if _is_api_path(request):
        return _json({'error': NOT_FOUND_JSON_MESSAGE}, status=404)
    return _not_found_page(request, kiosk=request.path_info.startswith(root + 'kiosk'))
