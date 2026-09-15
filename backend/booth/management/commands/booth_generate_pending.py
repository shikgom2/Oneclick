# -*- coding:utf-8 -*-
"""생성 대기 중이거나 멈춘 리포트를 한 명씩 생성하는 수동 대체 수단.

평소에는 개인 페이지의 JavaScript 가 generate 를 호출해 리포트가 만들어진다. 체험자가
페이지를 닫았거나, 워커가 harakiri 로 죽어 generating 에 멈췄거나, API 장애로 failed 가
쌓였을 때 운영자가 서버에서 직접 돌린다.

    python manage.py booth_generate_pending                # pending + 멈춘 generating
    python manage.py booth_generate_pending --include-failed
    python manage.py booth_generate_pending --dry-run

출력에는 참가자 번호와 상태만 쓰고 이름은 쓰지 않는다. 한 명당 최대
BOOTH_REPORT_DEADLINE_SEC 초가 걸릴 수 있으므로 --limit 으로 한 번에 처리할 인원을 제한한다.
이 명령도 동시 생성 상한(BOOTH_MAX_CONCURRENT_REPORTS)을 따른다. 웹 워커가 상한만큼 생성 중이면
그 사람은 pending 으로 남고 '기타'로 센다(나중에 다시 실행). 시도 한도를 다 쓴 사람은 대상이
아니다. 스태프 확인 뒤 booth_reset_report 로 초기화한다.
"""
from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from booth import conf, report
from booth.models import Participant

Status = Participant.ReportStatus


def pending_queryset(include_failed=False):
    """생성 대상. claim 조건과 같은 기준으로 고르고, 최종 판단은 generate_report 의 claim 이 한다."""
    now = timezone.now()
    stale_before = now - timedelta(seconds=conf.STALE_GENERATING_SEC)
    status_q = Q(report_status=Status.PENDING) | (
        Q(report_status=Status.GENERATING)
        & (Q(report_started_at__lt=stale_before) | Q(report_started_at__isnull=True))
    )
    if include_failed:
        status_q |= Q(report_status=Status.FAILED, report_attempts__lt=conf.MAX_REPORT_ATTEMPTS)
    return (Participant.objects.using(conf.BOOTH_DB)
            .filter(created_at__gte=now - timedelta(days=conf.retention_days()),
                    consent=True,
                    survey_received_at__isnull=False,
                    measurement_received_at__isnull=False,
                    report_generations__lt=conf.MAX_REPORT_GENERATIONS)
            .filter(status_q)
            .order_by('number'))


class Command(BaseCommand):
    help = ('pending 이거나 %d초 넘게 멈춘 generating 리포트를 순서대로 생성한다(이름은 출력하지 않음).'
            % conf.STALE_GENERATING_SEC)

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50, help='한 번에 처리할 최대 인원 (기본 50)')
        parser.add_argument('--include-failed', action='store_true',
                            help='시도 횟수가 남은 failed 도 다시 생성한다')
        parser.add_argument('--dry-run', action='store_true', help='대상 인원만 출력하고 생성하지 않는다')

    def handle(self, *args, **options):
        limit = max(options['limit'], 0)
        ids = list(pending_queryset(options['include_failed']).values_list('pk', flat=True)[:limit])

        if options['dry_run']:
            self.stdout.write('생성 대상 %d명 (dry-run, 생성하지 않음)' % len(ids))
            return

        counts = Counter()
        for pk in ids:
            # 목록을 뽑은 뒤 긴 생성이 이어지므로 한 명씩 다시 읽는다(그 사이 삭제·변경 대비).
            participant = Participant.objects.using(conf.BOOTH_DB).filter(pk=pk).first()
            if participant is None:
                counts['missing'] += 1
                continue
            status = report.generate_report(participant)
            counts[status] += 1
            self.stdout.write('%s -> %s' % (participant.label, status))

        self.stdout.write('생성 대상 %d명: 완료 %d, 실패 %d, 기타 %d' % (
            len(ids), counts[Status.DONE], counts[Status.FAILED],
            len(ids) - counts[Status.DONE] - counts[Status.FAILED],
        ))
