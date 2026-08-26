# -*- coding:utf-8 -*-
from django.urls import path
from . import views


urlpatterns = [
    path(
        '',
        views.ExperimentView.as_view(),
        name='add experiments'
    ),
    path(
        '<int:pk>/',
        views.ExperimentDeleteView.as_view(),
        name='delete experiments'
    ),
    path(
        'list/',
        views.ExperimentListView().as_view(),
        name='list experiments'
    ),
    path(
        '<int:pk>/ai-report/',
        views.AIReportView.as_view(),
        name='ai report'
    ),
    path(
        '<int:pk>/ai-report/generate/',
        views.AIReportGenerateView.as_view(),
        name='ai report generate'
    ),
    # 원격 분석 중계 — remote_submit.py 가 붙는 주소라 슬래시 없이 정확히
    # 맞춘다 (클라이언트가 '<서버>/jobs' 형태로 호출한다).
    path(
        'analysis/jobs',
        views.analysis_relay_submit,
        name='analysis relay submit'
    ),
    path(
        'analysis/jobs/<int:job_id>',
        views.analysis_relay_status,
        name='analysis relay status'
    ),
]
