# -*- coding:utf-8 -*-
"""booth 설정 누락을 manage.py check / migrate 단계에서 잡는 시스템 체크.

라우터가 빠지면 booth 모델이 조용히 default(운영 MySQL)로 가려고 한다. 개인정보가
엉뚱한 DB 에 쌓이는 사고는 런타임에 눈에 띄지 않으므로, 설정 단계에서 에러로 막는다.
운영 배포에서 흔한 실수(FTP 로 동기화되는 폴더 안의 DB 파일, http 기준 주소, 키 하나로
모든 권한)는 경고로 알린다. 경고는 시작을 막지 않는다.
"""
import os

from django.conf import settings
from django.core.checks import Error, Warning, register

from . import conf
from .conf import BOOTH_DB

ROUTER_PATH = 'booth.routers.BoothRouter'


def _router_configured():
    for router in getattr(settings, 'DATABASE_ROUTERS', []):
        if isinstance(router, str):
            if router == ROUTER_PATH:
                return True
        else:
            cls = router if isinstance(router, type) else type(router)
            if '%s.%s' % (cls.__module__, cls.__name__) == ROUTER_PATH:
                return True
    return False


@register()
def booth_database_check(app_configs, **kwargs):
    errors = []
    if BOOTH_DB not in settings.DATABASES:
        errors.append(Error(
            "settings.DATABASES 에 'booth' 항목이 없습니다.",
            hint='settings.py 끝의 booth 블록을 확인하세요.',
            id='booth.E001',
        ))
    if not _router_configured():
        errors.append(Error(
            'settings.DATABASE_ROUTERS 에 %s 가 없습니다.' % ROUTER_PATH,
            hint='라우터가 없으면 booth 모델이 default(운영) DB 로 갑니다.',
            id='booth.E002',
        ))
    return errors


def _booth_db_name():
    return str((settings.DATABASES.get(BOOTH_DB) or {}).get('NAME') or '')


def path_inside(path, base_dir):
    """path 가 base_dir 안(같은 폴더 포함)에 있는지. 메모리 DB 이름은 False."""
    if not path or not base_dir or path.startswith('file:') or ':memory:' in path:
        return False
    real_path = os.path.normcase(os.path.realpath(path))
    real_base = os.path.normcase(os.path.realpath(str(base_dir)))
    return real_path == real_base or real_path.startswith(real_base.rstrip(os.sep) + os.sep)


@register()
def booth_deploy_check(app_configs, **kwargs):
    warnings = []
    if path_inside(_booth_db_name(), getattr(settings, 'BASE_DIR', None)):
        warnings.append(Warning(
            'booth SQLite 파일이 backend 폴더 안에 있습니다(BOOTH_DB_PATH 미설정).',
            hint='FTP 로 backend 를 내려받거나 올릴 때 실명·설문 응답이 복사되거나 운영 DB 를 덮어쓸 수 있습니다. '
                 'backend/.env 에 BOOTH_DB_PATH=/var/lib/oneclick-booth/booth.sqlite3 처럼 폴더 밖 경로를 넣으세요.',
            id='booth.W001',
        ))
    base_url = conf.public_base_url()
    if base_url and not base_url.lower().startswith('https://'):
        warnings.append(Warning(
            'BOOTH_PUBLIC_BASE_URL 이 https:// 로 시작하지 않아 쓰이지 않습니다.',
            hint='예: BOOTH_PUBLIC_BASE_URL=https://180.83.245.145',
            id='booth.W002',
        ))
    if conf.api_key() and not conf.form_api_key():
        warnings.append(Warning(
            'BOOTH_FORM_API_KEY 가 없어 Apps Script 가 태블릿 키(BOOTH_API_KEY)를 씁니다.',
            hint='폼 편집자가 대기 목록·개인 페이지 주소까지 읽을 수 있게 됩니다. 키를 나누세요.',
            id='booth.W003',
        ))
    if conf.allow_insecure():
        warnings.append(Warning(
            'BOOTH_ALLOW_INSECURE=1 이라 booth 페이지·API 가 평문 http 요청도 받습니다.',
            hint='로컬 개발 전용입니다. 운영 backend/.env 에서는 지우세요.',
            id='booth.W004',
        ))
    if conf.form_url() and not conf.form_number_entry():
        # 인쇄 QR 이 폼을 바로 열어 휴대폰 체험자는 번호를 볼 화면이 없다. 번호 미리 채우기가 빠지면 조용히
        # 개인 페이지로 돌아가므로(views_pages._start_destination) 설정 단계에서 알린다.
        warnings.append(Warning(
            'BOOTH_FORM_URL 은 있는데 BOOTH_FORM_NUMBER_ENTRY 가 없어 폼에 참가자 번호를 미리 채우지 못합니다.',
            hint='인쇄 QR 이 폼 대신 개인 페이지를 엽니다(번호 없는 설문은 접수되지 않음). '
                 '구글 폼 미리 채운 링크의 entry.<숫자> 를 넣으세요.',
            id='booth.W005',
        ))
    return warnings
