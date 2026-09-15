# -*- coding:utf-8 -*-
"""부스 체험자 한 명 = Participant 한 행.

설문(구글 폼), 측정(체험 태블릿), AI 리포트가 서로 다른 시각·다른 기기에서 들어오므로
세 가지를 참가자 번호 하나로 묶어 한 행에 모은다. 이 모델은 라우터(booth.routers)에 의해
운영 MySQL 이 아닌 booth 전용 SQLite 에만 저장되고, 보유기간이 지나면 행째로 삭제된다.

상태 전이 규칙은 모델이 아니라 booth.services 에 있다. 모델 필드를 직접 바꾸지 말고
services 의 apply_survey / apply_measurement / clear_measurement 를 거쳐야
report_status 가 일관되게 유지된다.
"""
import secrets

from django.db import models


def new_token():
    """개인 페이지 URL 에 들어갈 추측 불가능한 토큰.

    로그인이 없는 서비스라 이 토큰이 곧 '개인 페이지(이름·리포트) 열람 권한'이다.
    24바이트(192비트)라 대입이 불가능하고, urlsafe 라 URL 경로에 그대로 들어간다(32자).
    """
    return secrets.token_urlsafe(24)


class Participant(models.Model):
    class Source(models.TextChoices):
        PHONE = 'phone', '개인 휴대폰'
        KIOSK = 'kiosk', '공용 키오스크'

    class ReportStatus(models.TextChoices):
        WAITING = 'waiting', '설문·측정 대기'
        PENDING = 'pending', '생성 대기'
        GENERATING = 'generating', '생성 중'
        DONE = 'done', '완료'
        FAILED = 'failed', '실패'
        NO_CONSENT = 'no_consent', '동의 없음'

    # 화면에는 'SF-042' 로 보이는 순번. 여러 uWSGI 워커가 동시에 발급하므로 unique 로
    # 충돌을 DB 가 막고, services.issue_participant 가 재시도한다.
    number = models.PositiveIntegerField(unique=True)
    token = models.CharField(max_length=64, unique=True, default=new_token, editable=False)
    # 공용 키오스크 화면 전용 토큰. 개인 페이지 토큰과 따로 둔다. 키오스크 주소는 공용 태블릿의
    # 방문 기록·주소창 추천에 남으므로, 그 주소로는 번호와 설문 접수 여부만 볼 수 있어야 한다.
    # 휴대폰으로 시작한 참가자는 NULL(unique 제약은 NULL 끼리 충돌하지 않는다).
    kiosk_token = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)
    source = models.CharField(max_length=10, choices=Source.choices)

    # 보유기간 삭제·대기 목록 조회가 모두 created_at 범위 조건이라 인덱스를 둔다.
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    # --- 설문 ---
    name = models.CharField(max_length=100, blank=True, default='')
    consent = models.BooleanField(default=False)
    survey_answers = models.JSONField(null=True, blank=True)
    survey_received_at = models.DateTimeField(null=True, blank=True)
    survey_revision = models.PositiveIntegerField(default=0)   # 재제출마다 +1

    # --- 측정 ---
    measurement = models.JSONField(null=True, blank=True)
    measurement_received_at = models.DateTimeField(null=True, blank=True)

    # --- AI 리포트 ---
    report = models.JSONField(null=True, blank=True)
    report_status = models.CharField(
        max_length=20, choices=ReportStatus.choices, default=ReportStatus.WAITING,
    )
    # 화면·로그에 그대로 노출될 수 있으므로 이름 등 개인정보를 절대 넣지 않는다.
    report_error = models.CharField(max_length=300, blank=True, default='')
    report_started_at = models.DateTimeField(null=True, blank=True)
    report_done_at = models.DateTimeField(null=True, blank=True)
    report_attempts = models.PositiveIntegerField(default=0)       # 지금 자료로 시도한 횟수(재제출 시 0)
    # 평생 생성 호출 수. 재제출·측정 덮어쓰기에도 초기화하지 않는 비용 상한(conf.MAX_REPORT_GENERATIONS).
    # 스태프의 booth_reset_report 만 0 으로 되돌린다.
    report_generations = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = 'booth'
        verbose_name = '부스 체험자'
        verbose_name_plural = '부스 체험자'

    def __str__(self):
        # 로그·관리 화면에 이름이 새지 않도록 번호만 쓴다.
        return self.label

    # 설문·측정 '도착 여부'의 기준은 received_at 이다. JSONField 의 NULL 판정은 DB 마다
    # 미묘하게 달라서(SQL NULL vs JSON null), DB 조건(claim 등)도 received_at 으로 맞춘다.
    @property
    def has_survey(self):
        return self.survey_received_at is not None

    @property
    def has_measurement(self):
        return self.measurement_received_at is not None

    @property
    def label(self):
        from .services import format_label   # services 가 models 를 import 하므로 지연 import
        return format_label(self.number) if self.number is not None else ''

    @property
    def masked_name(self):
        from .services import mask_name
        return mask_name(self.name)
