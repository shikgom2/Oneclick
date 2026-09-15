# -*- coding:utf-8 -*-
"""booth 앱 테스트·로컬 관리 명령 전용 설정.

기본 settings 의 DATABASES['default'] 는 원격 운영 MySQL 이다. manage.py test 는
테스트 DB 를 '만들고 지우는' 명령이라 실수로 운영 설정으로 돌리면 치명적이다.
그래서 booth 작업(test, check, makemigrations)은 항상 이 모듈을 --settings 로 지정하고,
여기서 모든 DB 를 로컬 SQLite 로 덮어쓴다. 아래 가드가 SQLite 이외 엔진을 발견하면
설정 로드 단계에서 바로 멈추므로, 누가 DB 항목을 추가해도 MySQL 로 새지 않는다.

사용 예:
    .venv/Scripts/python.exe manage.py test booth --settings=backend.settings_booth_test
"""
import os
import tempfile

from .settings import *  # noqa: F401,F403


# 테스트 DB 자체는 Django 가 메모리에 따로 만든다. 이 파일 경로는 makemigrations/check
# 처럼 테스트가 아닌 명령이 연결을 열 때만 쓰인다. 저장소 안에 파일이 생기지 않도록
# OS 임시 폴더에 둔다(SQLite 는 상위 폴더를 만들어주지 않으므로 미리 만든다).
_BOOTH_TEST_DIR = os.path.join(tempfile.gettempdir(), 'oneclick_booth_test')
os.makedirs(_BOOTH_TEST_DIR, exist_ok=True)

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(_BOOTH_TEST_DIR, 'default.sqlite3'),
    },
    'booth': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(_BOOTH_TEST_DIR, 'booth.sqlite3'),
        'OPTIONS': {'timeout': 20},
    },
}

for _alias, _db in DATABASES.items():
    if _db.get('ENGINE') != 'django.db.backends.sqlite3':
        raise RuntimeError('settings_booth_test: %s DB 가 SQLite 가 아닙니다. 운영 DB 보호를 위해 중단합니다.' % _alias)

DATABASE_ROUTERS = ['booth.routers.BoothRouter']
ROOT_URLCONF = 'backend.urls'

# 기본 설정의 파일 캐시는 저장소 안(backend/cache)에 쓴다. 테스트가 흔적을 남기지 않게 메모리로.
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

# 운영 설정은 booth 로거를 INFO 로 올린다. 테스트 출력이 발급·수신 로그로 뒤덮이지 않게
# WARNING 이상만 남긴다(assertLogs 는 수준을 스스로 낮추므로 로그 검증 테스트에는 영향 없음).
LOGGING['loggers']['booth']['level'] = 'WARNING'

# 네트워크 안전망. .env 에서 읽힌 실제 ANTHROPIC_API_KEY 를 가짜로 바꾸고, SDK 가 기본
# 엔드포인트 대신 닫힌 로컬 포트(127.0.0.1:9)로 가게 한다. 테스트가 Claude 호출 mock 을
# 빠뜨려도 요청이 외부로 나가지 않고 즉시 연결 거부로 끝난다.
os.environ['ANTHROPIC_API_KEY'] = 'sk-ant-test-not-a-real-key'
os.environ['ANTHROPIC_BASE_URL'] = 'http://127.0.0.1:9'
