# -*- coding:utf-8 -*-
from django.urls import path, re_path

from . import views_api, views_pages

app_name = 'booth'

urlpatterns = [
    # --- 체험자 페이지 (HTML) ---
    path('start/', views_pages.start, name='start'),
    path('kiosk/', views_pages.kiosk_home, name='kiosk_home'),
    path('kiosk/new/', views_pages.kiosk_new, name='kiosk_new'),
    # 키오스크 경로의 토큰은 개인 페이지 토큰이 아니라 kiosk_token 이다(공용 태블릿 방문 기록 대비).
    path('kiosk/p/<str:token>/', views_pages.kiosk_participant, name='kiosk_participant'),
    path('kiosk/p/<str:token>/status/', views_pages.kiosk_status, name='kiosk_status'),
    path('p/<str:token>/', views_pages.personal, name='personal'),
    path('p/<str:token>/survey/', views_pages.survey_redirect, name='survey_redirect'),
    path('p/<str:token>/status/', views_pages.status, name='status'),
    path('p/<str:token>/generate/', views_pages.generate, name='generate'),

    # --- 기기용 JSON API (X-Booth-Key) ---
    path('api/survey/', views_api.SurveyIntakeView.as_view(), name='api_survey'),
    path('api/pending/', views_api.PendingListView.as_view(), name='api_pending'),
    path('api/measurement/', views_api.MeasurementIntakeView.as_view(), name='api_measurement'),
    path(
        'api/participants/<int:number>/',
        views_api.ParticipantDetailView.as_view(),
        name='api_participant_detail',
    ),
    path(
        'api/participants/<int:number>/measurement/',
        views_api.ParticipantMeasurementView.as_view(),
        name='api_participant_measurement',
    ),

    # --- 반드시 마지막: /booth/ 아래의 나머지 모든 경로 ---
    # 운영이 DEBUG=True 라, 맞는 경로가 없으면 Django 가 프로젝트 전체 URL 목록을 보여주는 404 를,
    # 끝의 '/' 가 빠진 POST 에는 설정값이 담긴 디버그 500 을 낸다. booth 아래 경로는 항상 여기서
    # 끝나므로 그런 화면이 나가지 않는다(API 경로는 JSON 404).
    re_path(r'^.*$', views_pages.not_found, name='not_found'),
]
