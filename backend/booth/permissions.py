# -*- coding:utf-8 -*-
"""booth 기기용 API 의 X-Booth-Key 권한.

부스 API 를 부르는 쪽은 사람이 아니라 체험 태블릿 앱과 구글 Apps Script 다. 둘 다 JWT
로그인을 할 수 없으므로 프로젝트 기본값(JWT + IsAuthenticated) 대신 공유 키로 막는다.
대기 목록에는 (가린) 이름이, 측정 응답에는 개인 페이지 URL 이 담기므로 키 없이 열리면 안 된다.

키는 쓰는 곳에 따라 둘로 나눈다(최소 권한).
  - 태블릿 키 BOOTH_API_KEY: 대기 목록, 측정 저장, 상태 조회, 측정 취소
  - 폼 키 BOOTH_FORM_API_KEY: 설문 수신만. Apps Script 속성은 폼 편집자 모두에게 보이므로,
    그 키로 참가자별 개인 페이지 주소(이름·리포트 열람 권한)를 읽을 수 없게 한다.
    폼 키가 비어 있으면 설문 수신도 태블릿 키로 받는다(예전 설정 호환, 경고 로그).

  - 서버에 필요한 키가 없으면 503. 키 설정을 빠뜨린 배포에서 API 가 '열린 채로'
    동작하는 일을 막고, 기기 쪽에서는 403(키 틀림)과 구분해 서버 설정 문제임을 알 수 있다.
  - 헤더가 없거나 다르면 403. 인증기(authentication_classes)가 비어 있으면 DRF 는 401 대신
    403 을 낸다.
"""
import hmac
import logging

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission

from . import conf

logger = logging.getLogger(__name__)

API_KEY_HEADER = 'X-Booth-Key'
API_KEY_META = 'HTTP_X_BOOTH_KEY'      # WSGI 가 헤더 이름을 바꾼 형태

SCOPE_TABLET = 'tablet'
SCOPE_FORM = 'form'

API_KEY_MISSING_MESSAGE = '서버에 BOOTH_API_KEY 가 설정되지 않아 API 를 사용할 수 없습니다.'
API_KEY_INVALID_MESSAGE = '인증 키(X-Booth-Key)가 없거나 올바르지 않습니다.'

# 폼 키 미설정 경고는 워커마다 한 번만 남긴다(설문마다 남기면 로그가 뒤덮인다).
_form_key_fallback_warned = False


class BoothApiUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = API_KEY_MISSING_MESSAGE
    default_code = 'booth_api_key_not_configured'


def _to_bytes(value):
    # hmac.compare_digest 는 str 끼리면 ASCII 만 받는다(아니면 TypeError). bytes 로 맞춘다.
    # 리눅스의 os.environ 은 해석 못 한 바이트를 surrogateescape 로 담으므로 같은 방식으로 되돌린다.
    return value.encode('utf-8', 'surrogateescape')


def api_key_matches(provided, expected):
    """헤더 값과 서버 키를 일정 시간 비교. 둘 중 하나라도 비었으면 False."""
    if not provided or not expected or not isinstance(provided, str):
        return False
    try:
        return hmac.compare_digest(_to_bytes(provided), _to_bytes(expected))
    except UnicodeError:
        return False


def expected_key(view):
    """뷰의 booth_key_scope 에 맞는 서버 키. 설정이 없으면 ''."""
    global _form_key_fallback_warned
    if getattr(view, 'booth_key_scope', SCOPE_TABLET) == SCOPE_FORM:
        form_key = conf.form_api_key()
        if form_key:
            return form_key
        if not _form_key_fallback_warned:
            _form_key_fallback_warned = True
            logger.warning('[booth] BOOTH_FORM_API_KEY 가 없어 설문 수신에 태블릿 키(BOOTH_API_KEY)를 씁니다. '
                           '폼 편집자가 태블릿 권한까지 갖게 되니 키를 나누세요.')
    return conf.api_key()


class HasBoothApiKey(BasePermission):
    """X-Booth-Key 헤더가 뷰 범위(태블릿·폼)의 서버 키와 같을 때만 허용."""

    message = API_KEY_INVALID_MESSAGE

    def has_permission(self, request, view):
        # 키는 호출 시점에 읽는다(conf 규칙). uWSGI 재시작 없이 바뀌진 않지만 테스트가 patch 할 수 있다.
        expected = expected_key(view)
        if not expected:
            logger.error('[booth] BOOTH_API_KEY 미설정으로 API 요청 거부 %s %s', request.method, request.path)
            raise BoothApiUnavailable()

        if api_key_matches(request.META.get(API_KEY_META), expected):
            return True
        # 보낸 키 값은 로그에 남기지 않는다(오타 난 진짜 키일 수 있다).
        logger.warning('[booth] API 키 불일치로 거부 %s %s', request.method, request.path)
        return False
