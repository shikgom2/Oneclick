# -*- coding:utf-8 -*-
"""스태프 복구: 한 참가자의 리포트 시도 횟수·평생 생성 수·리포트를 초기화한다.

개인 페이지가 '여러 번 시도했지만 리포트를 만들지 못했습니다'(최종 실패)나 '리포트를 표시할 수
없습니다'를 보여주고 체험자가 참가자 번호를 알려 줬을 때 쓴다. 먼저 uWSGI 로그의
'[booth] 리포트 SF-042 failed ... error=' 로 원인을 확인하고 고친 뒤(키, SDK, 폼 제목 설정 등) 실행한다.

    python manage.py booth_reset_report SF-042
    python manage.py booth_reset_report 42 --clear-measurement   # 측정값도 지워 태블릿에서 다시 측정

초기화한 뒤 체험자가 개인 페이지를 새로고침하면 생성이 다시 시작된다(또는 booth_generate_pending).
출력에는 번호와 상태만 쓴다(이름 없음). 평생 생성 한도(비용 상한)도 함께 초기화되므로 원인을
고치지 않고 반복 실행하지 않는다.
"""
from django.core.management.base import BaseCommand, CommandError

from booth import services
from booth.checks import booth_database_check


class Command(BaseCommand):
    help = '참가자 한 명의 리포트 시도 횟수·리포트를 초기화하고 상태를 다시 계산합니다(번호·상태만 출력).'

    # 다른 앱의 시스템 체크 오류와 무관하게 현장 복구가 되어야 한다(booth_purge 와 같은 이유).
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('participant', help='참가자 번호 (SF-042 또는 42)')
        parser.add_argument('--clear-measurement', action='store_true',
                            help='측정값도 지웁니다. 태블릿 대기 목록에 다시 나타나 다시 측정할 수 있습니다.')

    def handle(self, *args, **options):
        errors = booth_database_check(None)
        if errors:
            raise CommandError('booth 설정 오류: ' + ' / '.join(error.msg for error in errors))

        participant = services.get_participant_by_number(options['participant'])
        if participant is None:
            # 입력값을 되풀이하지 않는다(번호 대신 이름을 잘못 적었을 수 있다).
            raise CommandError('참가자를 찾을 수 없습니다. 번호 형식(SF-042 또는 42)과 보유기간을 확인하세요.')

        before = str(participant.report_status)
        clear = options['clear_measurement']
        services.reset_report(participant, clear_measurement=clear)
        after = str(participant.report_status)

        self.stdout.write('%s: %s -> %s%s' % (participant.label, before, after, ' (측정값 삭제)' if clear else ''))
        if after == 'pending':
            self.stdout.write('체험자가 개인 페이지를 새로고침하면 리포트 생성이 다시 시작됩니다 '
                              '(또는 booth_generate_pending).')
        elif clear or after == 'waiting':
            self.stdout.write('설문·측정이 모두 도착하면 리포트를 만들 수 있습니다.')
