# -*- coding:utf-8 -*-
"""체험 태블릿 앱·구글 Apps Script 용 JSON API.

  POST   api/survey/                              구글 폼 제출(Apps Script) -> 설문 저장     (폼 키)
  GET    api/pending/                             설문은 냈지만 측정이 없는 사람 목록(가린 이름) (태블릿 키)
  POST   api/measurement/                         측정값 저장 -> 개인 페이지 URL(태블릿이 QR 로 표시)
  GET    api/participants/<number>/               한 사람의 진행 상태
  DELETE api/participants/<number>/measurement/   다른 사람에게 잘못 보낸 측정값 취소

공통 규칙
  - https 로 들어온 요청만 받는다(BoothAPIView.initial). 키 확인·본문 해석보다 먼저 거부해
    X-Booth-Key 와 본문(이름·건강 정보)이 평문 포트(8000)로 처리되지 않게 한다.
  - 인증은 X-Booth-Key(booth.permissions). 프로젝트 기본값이 JWT + IsAuthenticated 라
    authentication_classes = [] 와 권한 클래스를 반드시 직접 지정한다.
  - 오류 본문은 항상 {"error": "한국어 메시지"}. 받는 쪽이 사람이 아니라 앱·스크립트라
    DRF 기본 형식({"detail": ...}, 영어)이나 DEBUG 오류 페이지 대신 한 형식으로 고정한다.
    /booth/api/ 아래의 없는 경로도 views_pages.not_found 가 JSON 404 로 끝낸다.
  - 상태 전이와 동시 쓰기는 services 의 apply_* 가 쓰기 잠금 안에서 처리한다. 뷰는 본문 검증,
    참가자 조회, 예외를 HTTP 상태로 옮기는 일만 한다.
  - 응답에는 실명·토큰을 넣지 않는다(태블릿 화면은 다른 관람객에게도 보인다). 예외는
    report_url 로, 체험자가 휴대폰으로 찍어 가야 하는 개인 페이지 주소라 토큰이 들어간다.
"""
import logging
from datetime import timedelta

from django.db import OperationalError
from django.http import Http404
from django.utils import timezone
from rest_framework import exceptions, status
from rest_framework.negotiation import BaseContentNegotiation, DefaultContentNegotiation
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler as drf_exception_handler

from . import conf, services
from .models import Participant
from .permissions import (API_KEY_INVALID_MESSAGE, SCOPE_FORM, SCOPE_TABLET, BoothApiUnavailable,
                          HasBoothApiKey)

logger = logging.getLogger(__name__)

HTTPS_REQUIRED_MESSAGE = 'HTTPS 로만 사용할 수 있습니다.'

# 상태 조회 API 가 개인 페이지 주소를 알려주는 시간(측정 수신 후). 태블릿이 측정 응답을 놓쳤을 때
# 되찾는 용도로 충분하고, 키를 가진 누구나 지난 참가자 전원의 주소를 모을 수는 없게 한다.
REPORT_URL_WINDOW_SEC = 30 * 60


# ---------------------------------------------------------------------------
# 오류 형식
# ---------------------------------------------------------------------------
class BoothApiError(exceptions.APIException):
    """뷰에서 바로 {"error": message} 로 돌려줄 오류. message 는 사용자에게 보여도 되는 한국어."""

    def __init__(self, message, status_code=status.HTTP_400_BAD_REQUEST):
        super().__init__(detail=message)
        self.status_code = status_code


# DRF 가 스스로 내는 예외의 한국어 메시지. 원문(영어)에는 파서 내부 오류 문구가 섞여 있다.
_DRF_MESSAGES = (
    (exceptions.ParseError, '요청 본문이 올바른 JSON 이 아닙니다.'),
    (exceptions.UnsupportedMediaType, 'Content-Type 은 application/json 이어야 합니다.'),
    (exceptions.MethodNotAllowed, '허용되지 않은 HTTP 메서드입니다.'),
    (exceptions.NotFound, '요청한 대상을 찾을 수 없습니다.'),
    (exceptions.NotAuthenticated, API_KEY_INVALID_MESSAGE),
    (exceptions.AuthenticationFailed, API_KEY_INVALID_MESSAGE),
    (exceptions.PermissionDenied, API_KEY_INVALID_MESSAGE),
    (exceptions.Throttled, '요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.'),
)


def booth_api_exception_handler(exc, context):
    """모든 오류를 {"error": "..."} 로 바꾼다.

    처리하지 못한 예외도 여기서 JSON 500 으로 끝낸다. 운영 서버가 DEBUG=True 라 예외가
    Django 까지 올라가면 설정·경로가 담긴 디버그 페이지가 외부로 나간다. 원인은 서버 로그에
    예외 종류와 코드 위치로 남긴다(메시지에는 설문 응답이 섞일 수 있어 남기지 않는다).
    """
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()

    if isinstance(exc, exceptions.APIException):
        # 헤더(Retry-After 등)와 rollback 처리는 DRF 기본 핸들러에 맡기고 본문만 바꾼다.
        response = drf_exception_handler(exc, context)
        if isinstance(exc, (BoothApiError, BoothApiUnavailable)):
            message = str(exc.detail)
        else:
            message = next((text for cls, text in _DRF_MESSAGES if isinstance(exc, cls)),
                           '요청을 처리할 수 없습니다.')
        response.data = {'error': message}
        return response

    if isinstance(exc, OperationalError) and 'locked' in str(exc).lower():
        # services 가 5회 재시도한 뒤에도 잠겨 있던 경우. 태블릿·Apps Script 가 다시 보내면 된다.
        logger.warning('[booth] API DB 잠금으로 처리 실패 (%s)', type(exc).__name__)
        return Response({'error': '서버가 바빠 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)

    view = context.get('view')
    services.log_exception_safely(logger, '[booth] API 처리 중 예외 %s' % (type(view).__name__ if view else '-'), exc)
    return Response({'error': '서버 오류가 발생했습니다.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class JSONOnlyNegotiation(BaseContentNegotiation):
    """Accept 헤더와 무관하게 항상 JSON 으로 응답한다.

    기본 협상은 Accept 에 JSON 이 없으면 406 을 내거나, 브라우저로 열면 Browsable API(HTML)를
    그린다. 기기 전용 API 라 둘 다 필요 없다.
    """

    def select_parser(self, request, parsers):
        return DefaultContentNegotiation().select_parser(request, parsers)

    def select_renderer(self, request, renderers, format_suffix=None):
        renderer = renderers[0]
        return renderer, renderer.media_type


class BoothAPIView(APIView):
    authentication_classes = []                 # JWT·세션 인증 끔: 키 하나로만 판단
    permission_classes = [HasBoothApiKey]
    booth_key_scope = SCOPE_TABLET              # 어떤 키를 받을지 (booth.permissions)
    throttle_classes = []
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]
    content_negotiation_class = JSONOnlyNegotiation
    metadata_class = None                       # OPTIONS 로 뷰 설명이 노출되지 않게 405 처리

    def initial(self, request, *args, **kwargs):
        # 권한(키) 확인보다 먼저. 평문으로 온 키를 서버가 '맞다/틀리다'로 답해 주지도 않는다.
        if not (request.is_secure() or conf.allow_insecure()):
            logger.warning('[booth] https 가 아닌 API 요청 거부 %s %s', request.method, request.path)
            raise BoothApiError(HTTPS_REQUIRED_MESSAGE, status.HTTP_403_FORBIDDEN)
        super().initial(request, *args, **kwargs)

    def get_exception_handler(self):
        return booth_api_exception_handler


# ---------------------------------------------------------------------------
# 본문·조회 도우미
# ---------------------------------------------------------------------------
def _json_object(request):
    """요청 본문이 JSON 객체인지 확인하고 dict 로 돌려준다(배열·문자열 본문은 400).

    Content-Type 이 JSON 이 아니면 DRF 가 415 를, 깨진 JSON 이면 400 을 먼저 낸다.
    """
    data = request.data
    if not isinstance(data, dict):
        raise BoothApiError('요청 본문은 JSON 객체여야 합니다.')
    return data


def _required_number(body):
    raw = body.get('number')
    if raw is None:
        raise BoothApiError('number(참가자 번호)가 필요합니다.')
    number = services.parse_number(raw)
    if number is None:
        raise BoothApiError('참가자 번호 형식이 올바르지 않습니다. 예: %s 또는 42' % services.format_label(42))
    return number


def _not_found(number):
    # 보유기간이 지나 조회되지 않는 경우도 같은 404 다(지워질 데이터의 존재 여부를 따로 알릴 이유가 없다).
    parsed = services.parse_number(number)
    label = services.format_label(parsed) if parsed is not None else str(number)
    return BoothApiError('참가자 %s 를 찾을 수 없습니다.' % label, status.HTTP_404_NOT_FOUND)


def _participant_or_404(number):
    participant = services.get_participant_by_number(number)
    if participant is None:
        raise _not_found(number)
    return participant


def _run_write(number, write):
    """services 쓰기 실행. 조회 직후 purge 로 행이 사라졌으면 404."""
    try:
        return write()
    except Participant.DoesNotExist:
        raise _not_found(number)


# ---------------------------------------------------------------------------
# 뷰
# ---------------------------------------------------------------------------
class SurveyIntakeView(BoothAPIView):
    """구글 폼 onFormSubmit(Apps Script) -> 설문 저장.

    본문: {"number": "SF-042" | 42, "answers": {"문항 제목": "값" | ["값", ...]}, "submitted_at": ISO(선택)}
    이름·동의·확인 코드 해석은 services 가 BOOTH_SURVEY_*_TITLE 문항 제목으로 한다.

    확인 코드(BOOTH_SURVEY_CODE_TITLE 문항, 서버가 미리 채움)
      - 틀리면 400. 공개 폼으로 남의 번호를 적어 이름·응답을 바꾸는 일(또는 번호 오타)을 막는다.
      - 없는데 BOOTH_FORM_CODE_ENTRY 가 설정돼 있으면 400(폼이 코드를 채우게 설정된 운영 상태).
      - 없고 코드 설정도 없으면(코드 문항 도입 전) 첫 설문만 받고, 이미 설문이 있으면 409.
      - 맞으면 재제출로 덮어쓴다(survey_revision += 1).
    """

    booth_key_scope = SCOPE_FORM

    def post(self, request):
        body = _json_object(request)
        number = _required_number(body)
        answers = body.get('answers')
        if not isinstance(answers, dict):
            raise BoothApiError('answers 는 {"문항 제목": 응답} 형태의 JSON 객체여야 합니다.')

        participant = _participant_or_404(number)

        code = services.check_survey_code(participant, answers)
        if code == services.CODE_MISMATCH:
            logger.warning('[booth] 설문 확인 코드 불일치로 거부 %s', participant.label)
            raise BoothApiError('%s 의 확인 코드가 맞지 않습니다. 번호·확인 코드를 고치지 말고 개인 페이지의 '
                                '설문하기로 다시 작성해 주세요.' % participant.label)
        if code == services.CODE_MISSING and conf.form_code_entry():
            logger.warning('[booth] 설문 확인 코드 없음으로 거부 %s', participant.label)
            raise BoothApiError('확인 코드가 없습니다. 개인 페이지의 설문하기로 다시 작성해 주세요.')

        # submitted_at 이 없거나 해석할 수 없으면 서버 수신 시각을 쓴다(services 규칙). 시각 형식
        # 하나 때문에 체험자의 설문 제출 자체를 버리는 것보다 낫다.
        # report_status 재계산은 apply_survey 가 쓰기 잠금 안에서 함께 한다. 뷰에서 다시
        # refresh_report_status(save=True) 를 부르면 그 사이 생성 워커가 잡은 generating 을
        # 잠금 밖의 오래된 값으로 덮어쓸 수 있어 부르지 않는다.
        try:
            _run_write(number, lambda: services.apply_survey(
                participant, answers, submitted_at=body.get('submitted_at'),
                allow_overwrite=(code == services.CODE_OK)))
        except services.SurveyOverwriteRefused:
            logger.warning('[booth] 확인 코드 없는 설문 덮어쓰기 거부 %s', participant.label)
            raise BoothApiError('%s 는 이미 설문이 접수되어 있어 확인 코드 없이 덮어쓸 수 없습니다.'
                                % participant.label, status.HTTP_409_CONFLICT)

        return Response({'ok': True, 'number': participant.number, 'label': participant.label})


class PendingListView(BoothAPIView):
    """체험 태블릿의 '누구를 측정했나요?' 목록.

    최근 BOOTH_PENDING_WINDOW_HOURS 안에 발급됐고 아직 측정값이 없는 참가자.
      - 설문을 낸 사람을 먼저, 방금 설문을 마친 사람이 맨 위(설문 수신 최신순). 스태프가 고르는
        대상은 대부분 태블릿 앞에 막 도착한 사람이다. 최대 SURVEYED_LIMIT 명.
      - 설문 전인 사람(측정을 먼저 하는 예외)은 뒤에, 최근 발급 순으로 UNSURVEYED_LIMIT 명까지만.
        번호 발급은 누구나 할 수 있어(QR·키오스크) 빈 번호가 쌓여도 목록·응답이 커지지 않게 한다.
        잘린 수는 unsurveyed_omitted 로 알린다. 오래된 사람은 번호로 직접 고를 수 있다.
    이름은 가린 형태만 보낸다(태블릿 화면이 공개돼 있다). 동의하지 않은 사람도 체험(측정)은 할 수
    있으므로 목록에 넣고 consent 로 구분한다(이름은 저장하지 않아 masked_name 이 비어 있다).
    """

    _FIELDS = ('number', 'name', 'consent', 'survey_received_at')
    SURVEYED_LIMIT = 200
    UNSURVEYED_LIMIT = 20

    def get(self, request):
        now = timezone.now()
        # 창(시간)을 길게 잡아도 보유기간이 지난 사람은 보이지 않게 둘 중 늦은 시각을 쓴다.
        cutoff = max(now - timedelta(hours=conf.pending_window_hours()),
                     now - timedelta(days=conf.retention_days()))
        base = (Participant.objects.using(conf.BOOTH_DB)
                .filter(created_at__gte=cutoff, measurement_received_at__isnull=True))

        # 설문 응답(JSON)은 필요 없으므로 필요한 열만 읽는다.
        surveyed = list(base.filter(survey_received_at__isnull=False)
                        .order_by('-survey_received_at', '-number')
                        .values(*self._FIELDS)[:self.SURVEYED_LIMIT])
        unsurveyed_qs = base.filter(survey_received_at__isnull=True)
        unsurveyed = list(unsurveyed_qs.order_by('-created_at', '-number')
                          .values(*self._FIELDS)[:self.UNSURVEYED_LIMIT])
        omitted = 0
        if len(unsurveyed) == self.UNSURVEYED_LIMIT:
            omitted = max(unsurveyed_qs.count() - self.UNSURVEYED_LIMIT, 0)

        items = [self._item(row, True) for row in surveyed]
        items += [self._item(row, False) for row in unsurveyed]
        return Response({'participants': items, 'unsurveyed_omitted': omitted})

    @staticmethod
    def _item(row, survey_received):
        return {
            'number': row['number'],
            'label': services.format_label(row['number']),
            'masked_name': services.mask_name(row['name']) if survey_received else '',
            'survey_received': survey_received,
            'survey_received_at': services.kst_isoformat(row['survey_received_at']),
            'consent': bool(row['consent']) if survey_received else False,
        }


class MeasurementIntakeView(BoothAPIView):
    """측정 완료 후 태블릿이 보내는 측정값 저장.

    본문: {"number": 42, "overwrite": false, "measurement": {...}}  (형식은 services.validate_measurement)
    이미 측정값이 있으면 409. 스태프가 확인하고 overwrite=true 로 다시 보내야 바뀐다(다른 태블릿이
    먼저 보냈거나 사람을 잘못 고른 경우를 조용히 덮어쓰지 않기 위해).
    설문에서 동의하지 않은 사람은 값을 저장하지 않고 '측정함'만 기록한다(응답은 200, no_consent).
    응답의 report_url 은 태블릿이 QR 로 띄워 체험자가 휴대폰으로 찍는다.
    """

    def post(self, request):
        body = _json_object(request)
        number = _required_number(body)

        overwrite = body.get('overwrite', False)
        if overwrite is None:
            overwrite = False
        if not isinstance(overwrite, bool):
            # "false" 같은 문자열을 참으로 해석해 기존 측정을 덮어쓰는 사고를 막는다.
            raise BoothApiError('overwrite 는 true 또는 false 여야 합니다.')

        # 본문 검증을 참가자 조회보다 먼저 한다. 번호가 틀린 경우보다 앱이 형식을 잘못 보내는
        # 버그를 먼저 드러내는 편이 현장에서 원인을 찾기 쉽다.
        try:
            measurement = services.validate_measurement(body.get('measurement'))
        except services.MeasurementInvalid as exc:
            raise BoothApiError(str(exc))

        participant = _participant_or_404(number)
        try:
            # report_status 재계산은 apply_measurement 가 잠금 안에서 한다(SurveyIntakeView 주석 참고).
            _run_write(number, lambda: services.apply_measurement(participant, measurement, overwrite=overwrite))
        except services.AlreadyMeasured:
            raise BoothApiError(
                '%s 는 이미 측정값이 있습니다. 덮어쓰려면 overwrite 를 true 로 보내세요.' % participant.label,
                status.HTTP_409_CONFLICT,
            )

        return Response({
            'ok': True,
            'number': participant.number,
            'label': participant.label,
            'masked_name': participant.masked_name,
            'report_status': str(participant.report_status),
            'report_url': services.build_report_url(participant, request),
        })


class ParticipantDetailView(BoothAPIView):
    """태블릿이 QR 을 띄운 뒤 진행 상태(리포트 생성 여부 등)를 확인할 때 쓴다.

    report_url 은 측정 수신 후 REPORT_URL_WINDOW_SEC(30분) 안에만 준다(그 밖에는 null). 측정 응답을
    놓친 태블릿이 되찾기에는 충분하고, 키만으로 순번을 돌며 모든 참가자의 개인 페이지 주소를 모을
    수는 없게 한다.
    """

    def get(self, request, number):
        participant = _participant_or_404(number)
        received = participant.measurement_received_at
        recent = received is not None and received >= timezone.now() - timedelta(seconds=REPORT_URL_WINDOW_SEC)
        return Response({
            'number': participant.number,
            'label': participant.label,
            'masked_name': participant.masked_name,
            'survey_received': participant.has_survey,
            'measurement_received': participant.has_measurement,
            'report_status': str(participant.report_status),
            'report_url': services.build_report_url(participant, request) if recent else None,
        })


class ParticipantMeasurementView(BoothAPIView):
    """다른 사람에게 잘못 보낸 측정값을 지운다. 그 측정값으로 만든 리포트도 함께 지워진다.

    측정값이 없어도 200 이다. 태블릿이 응답을 못 받고 다시 보내도 결과가 같게(멱등) 한다.
    """

    def delete(self, request, number):
        participant = _participant_or_404(number)
        _run_write(number, lambda: services.clear_measurement(participant))
        return Response({'ok': True})
