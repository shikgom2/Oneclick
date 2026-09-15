# -*- coding:utf-8 -*-
"""booth 앱 전용 DB 라우터.

체험자 이름·설문 같은 단기 개인정보를 운영 MySQL(default)에 섞지 않으려고 booth 앱
모델만 'booth'(SQLite)로 보낸다. 다른 앱에 대해서는 모든 메서드가 None 을 돌려준다.
None 은 '이 라우터는 의견 없음'이라 Django 기본 동작(default DB)이 그대로 유지된다.
"""
from .conf import BOOTH_DB

BOOTH_APP_LABEL = 'booth'


def _is_booth(obj_or_model):
    return obj_or_model._meta.app_label == BOOTH_APP_LABEL


class BoothRouter:

    def db_for_read(self, model, **hints):
        if _is_booth(model):
            return BOOTH_DB
        return None

    def db_for_write(self, model, **hints):
        if _is_booth(model):
            return BOOTH_DB
        return None

    def allow_relation(self, obj1, obj2, **hints):
        # 서로 다른 DB 사이에는 FK 가 성립하지 않는다. booth 끼리만 허용하고,
        # booth 와 다른 앱을 잇는 관계는 명시적으로 막는다.
        booth1, booth2 = _is_booth(obj1), _is_booth(obj2)
        if booth1 and booth2:
            return True
        if booth1 or booth2:
            return False
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # booth 테이블은 booth DB 에만, booth DB 에는 booth 테이블만.
        # (auth/contenttypes 등이 booth SQLite 에 생기지 않고, 운영 MySQL 에 booth 테이블이 생기지 않는다.)
        if app_label == BOOTH_APP_LABEL:
            return db == BOOTH_DB
        if db == BOOTH_DB:
            return False
        return None
