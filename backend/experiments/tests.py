# -*- coding:utf-8 -*-
"""측정 업로드 → 조회 왕복 테스트 (phase 모드별).

반드시 --settings=backend.settings_booth_test 로 실행한다(모든 DB 가 로컬 SQLite).
    .venv/Scripts/python.exe manage.py test experiments --settings=backend.settings_booth_test

payload 는 클라이언트(OneClick_Client_New 의 main.py · gui.py)가 보내는 모양 그대로 만든다.
그림은 1x1 PNG 인데 phase·슬롯마다 색을 달리한다. 키가 엉뚱한 테이블로 들어가면
바이트 비교에서 걸린다.

업로드가 쓰는 파일은 테스트마다 따로 만든 임시 MEDIA_ROOT 로 가고 끝나면 지운다
(저장소의 media 폴더에는 아무것도 남지 않는다). AI 리포트 생성(Claude 호출)은 여기서
일어나면 안 되므로, 호출되면 바로 실패하도록 막아 둔다. 리포트 쪽은 저장된 값을 돌려주는
GET 과 조립 함수만 확인한다.
"""
import base64
import importlib
import json
import logging
import os
import re
import shutil
import struct
import tempfile
import warnings
import zlib
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db import migrations
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework import serializers
from rest_framework.test import APIClient

from ecg.models import HRV, HRVRecovery3, HRVRecovery4
from ecg.serializer import HRVSerializer
from eeg.models import (EEG, EEGFAA, EEGDiff6, EEGDiff7, EEGDiff8,
                        EEGRecovery3, EEGRecovery4)
from eeg.serializer import EEGSerializer
from experiments import views
from experiments.models import Experiments


# ── 계약 (클라이언트·백엔드·프론트 공통) ─────────────────────────────────
# 합집합: 모든 모드의 phase 키를 시간 순서로 늘어놓은 것. 세션에 없는 키를 걸러내면
# 어느 모드든 남은 순서가 그 세션의 시간 순서가 된다. 코드에서 가져오지 않고 여기
# 따로 적어 둔다 — 코드 쪽 목록이 틀어지면 이 테스트가 잡아야 하기 때문이다.
UNION = ('baseline', 'stimulation1', 'recovery1', 'stimulation2', 'recovery2',
         'stimulation3', 'recovery3', 'stimulation4', 'recovery4', 'recovery')
LABELS_KO = {
    'baseline':     '기준',
    'stimulation1': '1차 자극',
    'recovery1':    '1차 회복',
    'stimulation2': '2차 자극',
    'recovery2':    '2차 회복',
    'stimulation3': '3차 자극',
    'recovery3':    '3차 회복',
    'stimulation4': '4차 자극',
    'recovery4':    '4차 회복',
    'recovery':     '회복',
}
NINE_PHASE = UNION[:9]
SIX_PHASE = ('baseline', 'stimulation1', 'stimulation2',
             'stimulation3', 'stimulation4', 'recovery')
MAX_DIFFS = 8       # diff1~diff8. diffN = 그 세션의 N번째 인접 쌍 (위치 슬롯)

# 9-phase 에서 새로 생긴 테이블. 그 밖의 모드에서는 행이 생기면 안 된다.
NEW_PHASE_MODELS = (HRVRecovery3, HRVRecovery4, EEGRecovery3, EEGRecovery4)
NEW_DIFF_MODELS = (EEGDiff6, EEGDiff7, EEGDiff8)


def phases_for(n):
    """phase 개수 → 그 세션의 phase 키 목록.

    6 만 6-phase 구성이고, 그 밖의 개수는 9-phase 순서의 앞 n개다
    (1·3·5·9 는 정식 모드, 2·4·7·8 은 중간에 끝난 세션).
    """
    return list(SIX_PHASE) if n == 6 else list(NINE_PHASE[:min(n, 9)])


# ── 그림 ─────────────────────────────────────────────────────────────
def png(r, g=0, b=0):
    """1x1 RGB PNG 바이트. 색으로 어느 키의 그림인지 구분한다."""
    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes((0, r, g, b))))
            + chunk(b'IEND', b''))


def b64(raw):
    return base64.b64encode(raw).decode('ascii')


def read(field):
    """ImageField 에 저장된 파일의 바이트. 핸들을 꼭 닫는다(안 닫으면 임시 폴더가 안 지워진다)."""
    field.open('rb')
    try:
        return field.read()
    finally:
        field.close()


BANDS = ('delta', 'theta', 'alpha', 'beta', 'gamma', 'sigma')
STAGES = ('wake', 'n1', 'n2', 'n3', 'rem')
EEG_KINDS = ('topography', 'connectivity', 'connectivity2')
# 클라이언트가 스펙트로그램을 그리는 채널 (O1·O2 는 뺀다). 서버가 소문자 컬럼으로 옮긴다.
SPECTROGRAM_CHANNELS = ('Fp1', 'F7', 'F3', 'T3', 'C3', 'Cz', 'P3',
                        'Fp2', 'F4', 'F8', 'C4', 'T4', 'P4')
# diff 슬롯 하나에 실리는 78키: 대역별 3종 + 수면단계별(지형도·연결성).
DIFF_FIELDS = tuple(
    ['%s_%s' % (kind, band) for band in BANDS for kind in EEG_KINDS]
    + ['%s_%s_%s' % (kind, band, stage)
       for band in BANDS for stage in STAGES for kind in ('topography', 'connectivity')]
)


def eeg_png(phase, kind, band):
    return png(10 + UNION.index(phase), EEG_KINDS.index(kind), BANDS.index(band))


def diff_png(slot, name):
    return png(100 + slot, 0, DIFF_FIELDS.index(name))


def faa_png(phase):
    return png(200 + UNION.index(phase))


# ── payload (클라이언트가 보내는 모양) ───────────────────────────────────
def hrv_phase_payload(phase):
    """ECGFeatureExtractor 가 phase 하나에 싣는 값. 수치로도 phase 를 구분할 수 있게 한다."""
    i = UNION.index(phase)
    return {
        'sdnn': 40.0 + i, 'rmssd': 30.0 + i, 'sdsd': 29.5 + i,
        'nn50': 12 + i, 'pnn50': 8.5 + i, 'tri_index': 9.25 + i,
        'vlf_rel_power': 20.0, 'lf_rel_power': 45.0, 'hf_rel_power': 35.0,
        'lh_ratio': 1.25 + i, 'norm_lf': 56.0, 'norm_hf': 44.0,
        'psd': {'frequency': [0.0, 0.1, 0.2, 0.3], 'power': [4.0, 2.0, 1.0, 0.5]},
        'heart_rate': b64(png(150 + i, 1)),
        'comparison': b64(png(150 + i, 2)),
    }


def build_hrv(phases):
    """hrv payload. 5-phase 계약 키 다섯 개는 항상 싣고(없는 phase 는 빈 dict),
    그 세션의 phase 를 채운다 — 클라이언트 extract() 와 같다."""
    hrv = {'nni': [812.0, 798.5, 805.25], 'rmssd': [31.5, 30.25, 33.0]}
    for key in NINE_PHASE[:5]:
        hrv[key] = {}
    for phase in phases:
        hrv[phase] = hrv_phase_payload(phase)
    return hrv


def eeg_phase_payload(phase):
    """main.py 의 eeg_content_bulk() 가 phase 하나에 싣는 18키."""
    return {'%s_%s' % (kind, band): b64(eeg_png(phase, kind, band))
            for kind in EEG_KINDS for band in BANDS}


def diff_payload(slot):
    return {name: b64(diff_png(slot, name)) for name in DIFF_FIELDS}


def coupling_payload(i):
    """spindle_coupling 의 phase 하나 (수면단계별 지표. 해당 단계가 없으면 null)."""
    return {
        'n1': None,
        'n2': {'coupled_ratio': 0.25 + i / 100, 'MRL': 0.5, 'mean_phase_deg': 12.5,
               'n_SO': 10 + i, 'n_spindle': 8, 'n_coupled': 2, 'duration_min': 5.0,
               'n_SO_per_min': 2.0, 'n_spindle_per_min': 1.6, 'n_coupled_per_min': 0.4,
               'stage_used': 'N2'},
        'n3': None,
        'rem': None,
    }


def build_eeg(phases, n_diff_keys=MAX_DIFFS):
    """eeg payload. phase 키는 그 세션에 있는 것만 싣고, diff 키는 클라이언트의
    DIFF_KEYS 전부를 싣되 남는 슬롯은 빈 dict 다.

    n_diff_keys: 클라이언트가 아는 diff 슬롯 수. 9-phase 판은 8, 그 전 판은 5.
    """
    n_pairs = len(phases) - 1
    eeg = {
        'psd': {
            'raw_psd': {'mean': [3.0, 2.0, 1.0], 'std': [0.3, 0.2, 0.1], 'freqs': [0.5, 1.0, 1.5]},
            'related_psd': [0.4, 0.2, 0.15, 0.2, 0.05, 0.08],
            'region_psd': {'right': [0.4, 0.2, 0.15, 0.2, 0.05, 0.08],
                           'left': [0.41, 0.19, 0.15, 0.2, 0.05, 0.08]},
        },
        'sleep_staging': {
            'sleep_stage': [0, 1, 2, 2, 3, 4],
            'sleep_stage_prob': [[0.9, 0.05, 0.03, 0.01, 0.01]] * 6,
        },
        'frontal_limbic': {band: b64(png(240, i)) for i, band in enumerate(BANDS)},
    }
    for phase in phases:
        eeg[phase] = eeg_phase_payload(phase)
    for slot in range(1, n_diff_keys + 1):
        eeg['diff%d' % slot] = diff_payload(slot) if slot <= n_pairs else {}
    eeg['faa'] = {'faa_' + phase: b64(faa_png(phase)) for phase in phases}
    eeg['psd_spectrogram'] = {ch: b64(png(250, i)) for i, ch in enumerate(SPECTROGRAM_CHANNELS)}
    eeg['spindle_coupling'] = {phase: coupling_payload(i) for i, phase in enumerate(phases)}
    return eeg


REPORT = {
    'tib': 45.0, 'tst': 30.0, 'twt': 15.0, 'waso': 5.0,
    'sleep_latency': 10.0, 'rem_latency': 0, 'sleep_eff': 66.7,
    'sleep_n1_tst': 16.7, 'sleep_n2_tst': 33.3, 'sleep_n3_tst': 16.7,
    'sleep_nrem_tst': 66.7, 'sleep_rem_tst': 16.7,
    'sleep_n1_min': 7.5, 'sleep_n2_min': 15.0, 'sleep_n3_min': 7.5,
    'sleep_nrem_min': 30.0, 'sleep_rem_min': 7.5,
}


def build_trigger(n_phases):
    """경계값 목록: phase 시작 분 n개 + 녹화 끝. 서버는 해석하지 않고 그대로 저장한다."""
    return [5 * i for i in range(n_phases + 1)]


def build_body(phases, n_diff_keys=MAX_DIFFS):
    """POST /api/v1/exp/ 본문. hrv·eeg·report 는 JSON 문자열로 한 번 더 감싸서 보낸다."""
    return {
        'name': 'TEST9',
        'measurement_date': '2026-11-19 10:00',
        'age': 30,
        'birth': '1996-01-01',
        'sex': 0,
        'hrv': json.dumps(build_hrv(phases)),
        'eeg': json.dumps(build_eeg(phases, n_diff_keys)),
        'report': json.dumps(REPORT),
        'trigger': build_trigger(len(phases)),
        'questionnaire': {'IRLS': 6, 'PSQI': 10},
        'stimulus_info': 'test stimulus',
    }


def expected_file_count(phases):
    """업로드 한 건이 MEDIA_ROOT 에 쓰는 파일 수.

    phase 마다 EEG 18 + HRV 2, diff 슬롯마다 78, frontal_limbic 6, 스펙트로그램 13.
    FAA 는 처음 5컬럼이 NOT NULL 이라 항상 쓰이고(값이 없으면 0바이트 파일),
    나머지 컬럼은 값이 있을 때만 쓰인다.
    """
    n = len(phases)
    faa = 5 + len([p for p in phases if p not in NINE_PHASE[:5]])
    return n * (18 + 2) + (n - 1) * 78 + 6 + 13 + faa


class PhaseContractTests(SimpleTestCase):
    """DB 없이 보는 것: 합집합 순서·라벨과, 모델·serializer·프롬프트·마이그레이션이 한 벌인지."""

    def test_union_order_and_labels(self):
        self.assertEqual(views.CONNECTIVITY_PHASES,
                         tuple((key, LABELS_KO[key]) for key in UNION))

    def test_filtered_union_is_time_order_in_every_mode(self):
        union = [key for key, _ in views.CONNECTIVITY_PHASES]
        for n in range(1, 10):
            phases = phases_for(n)
            with self.subTest(n_phases=n):
                self.assertEqual([key for key in union if key in phases], phases)

    def test_models_have_a_nullable_slot_for_every_key(self):
        # FK 가 NULL 인지로만 '그 세션에 그 phase 가 있는가' 를 안다. 그래서 전부 NULL 허용이어야 한다.
        for key in UNION:
            self.assertTrue(EEG._meta.get_field(key).null, key)
            self.assertTrue(HRV._meta.get_field(key).null, key)
            EEGFAA._meta.get_field('faa_' + key)            # 없으면 FieldDoesNotExist
        for slot in range(1, MAX_DIFFS + 1):
            self.assertTrue(EEG._meta.get_field('diff%d' % slot).null, slot)

        # 9-phase 에서 새로 생긴 컬럼·테이블 이름.
        columns = {
            (EEG, 'recovery3'): 'EEG_RECOVERY_3_ID',
            (EEG, 'recovery4'): 'EEG_RECOVERY_4_ID',
            (EEG, 'diff6'): 'EEG_DIFF_6_ID',
            (EEG, 'diff7'): 'EEG_DIFF_7_ID',
            (EEG, 'diff8'): 'EEG_DIFF_8_ID',
            (HRV, 'recovery3'): 'HRV_RECOVERY_3_ID',
            (HRV, 'recovery4'): 'HRV_RECOVERY_4_ID',
            (EEGFAA, 'faa_recovery3'): 'EEG_FRONTAL_ASYMMETRY_RECOVERY3',
            (EEGFAA, 'faa_recovery4'): 'EEG_FRONTAL_ASYMMETRY_RECOVERY4',
        }
        for (model, name), column in columns.items():
            field = model._meta.get_field(name)
            self.assertEqual(field.column, column)
            self.assertTrue(field.null, name)
        tables = {
            EEGRecovery3: 'OC_EEG_RECOVERY_3', EEGRecovery4: 'OC_EEG_RECOVERY_4',
            EEGDiff6: 'OC_EEG_DIFF_6', EEGDiff7: 'OC_EEG_DIFF_7', EEGDiff8: 'OC_EEG_DIFF_8',
            HRVRecovery3: 'OC_HRV_RECOVERY_3', HRVRecovery4: 'OC_HRV_RECOVERY_4',
        }
        for model, table in tables.items():
            self.assertEqual(model._meta.db_table, table)

    def test_serializers_nest_every_phase_and_diff(self):
        # 중첩 선언과 Meta.fields 는 한 벌이다. 선언만 있으면 조회가 전부 500 이고,
        # Meta.fields 에만 있으면 중첩 객체 대신 PK 정수가 나간다.
        eeg_fields = EEGSerializer().fields
        hrv_fields = HRVSerializer().fields
        for key in UNION:
            self.assertIsInstance(eeg_fields[key], serializers.ModelSerializer, key)
            self.assertIsInstance(hrv_fields[key], serializers.ModelSerializer, key)
        for slot in range(1, MAX_DIFFS + 1):
            self.assertIsInstance(eeg_fields['diff%d' % slot], serializers.ModelSerializer, slot)
        # 응답 안에서도 phase 키는 합집합(시간) 순서로 나간다.
        self.assertEqual([key for key in eeg_fields if key in UNION], list(UNION))
        self.assertEqual([key for key in hrv_fields if key in UNION], list(UNION))
        for key in UNION:
            self.assertIn('faa_' + key, eeg_fields['faa'].fields)

    def test_response_keys_only_grew(self):
        # 기존 세션의 조회 응답에서 달라져도 되는 것은 null 인 새 키가 늘어나는 것뿐이다.
        # 9-phase 를 넣기 전의 응답 키(순서 포함)를 여기 적어 둔다. 새 키를 빼면 그대로여야 한다
        # (phase 가 아닌 키 — psd, note, trigger 등 — 가 Meta.fields 에서 빠져도 여기서 걸린다).
        eeg_before = ['pk', 'psd', 'sleep_staging', 'frontal_limbic',
                      'baseline', 'stimulation1', 'recovery1', 'stimulation2', 'recovery2',
                      'stimulation3', 'stimulation4', 'recovery',
                      'diff1', 'diff2', 'diff3', 'diff4', 'diff5',
                      'faa', 'psd_spectrogram', 'spindle_coupling', 'note', 'trigger']
        hrv_before = ['pk', 'nni', 'rmssd',
                      'baseline', 'stimulation1', 'recovery1', 'stimulation2', 'recovery2',
                      'stimulation3', 'stimulation4', 'recovery', 'note']
        faa_before = ['id', 'int_dt', 'upt_dt',
                      'faa_baseline', 'faa_stimulation1', 'faa_recovery1', 'faa_stimulation2',
                      'faa_recovery2', 'faa_stimulation3', 'faa_stimulation4', 'faa_recovery']
        eeg_fields = EEGSerializer().fields
        cases = (
            ('eeg', list(eeg_fields), eeg_before,
             {'recovery3', 'recovery4', 'diff6', 'diff7', 'diff8'}),
            ('hrv', list(HRVSerializer().fields), hrv_before, {'recovery3', 'recovery4'}),
            ('faa', list(eeg_fields['faa'].fields), faa_before, {'faa_recovery3', 'faa_recovery4'}),
        )
        for label, now, before, added in cases:
            with self.subTest(response=label):
                self.assertEqual([key for key in now if key not in added], before)
                self.assertEqual(set(now) - set(before), added)

    def test_prompt_phase_vocabulary_is_the_union(self):
        # 프롬프트는 phase_notes 에 쓸 수 있는 영문 키를 문장으로 나열한다(손으로 적은 목록).
        # 프론트가 이 키로 표에 연결하므로 합집합과 어긋나면 그 구간의 설명이 사라진다.
        prompt = views._build_ai_prompt_json({
            'patient': {'name': 'TEST9', 'age': 30, 'sex': 'N/A', 'measurement_date': 'N/A'},
            'hrv_phases': [], 'sleep': {}, 'eeg': {},
        })
        match = re.search(r'영문 키를\s*\(([^)]+)\)', prompt)
        self.assertIsNotNone(match)
        self.assertEqual(re.split(r'[\s,]+', match.group(1).strip()), list(UNION))

    def test_nine_phase_migrations_only_add(self):
        # 기존 행을 건드리지 않으려면 새 테이블(CreateModel)과 NULL 허용 컬럼(AddField)뿐이어야 한다.
        # 6-phase 마이그레이션 바로 뒤에 붙고, 다른 앱에는 의존하지 않는다.
        parents = {
            'eeg': '0014_eegdiff5_eegrecovery_eegstimulation3_eegstimulation4_and_more',
            'ecg': '0003_hrvrecovery_hrvstimulation3_hrvstimulation4_and_more',
        }
        for app, name in (('eeg', '0015_nine_phase'), ('ecg', '0004_nine_phase')):
            migration = importlib.import_module('%s.migrations.%s' % (app, name)).Migration
            self.assertEqual(migration.dependencies, [(app, parents[app])])
            operations = migration.operations
            for op in operations:
                with self.subTest(app=app, operation=op.describe()):
                    self.assertIsInstance(op, (migrations.CreateModel, migrations.AddField))
                    if isinstance(op, migrations.AddField):
                        self.assertTrue(op.field.null)


class UploadRoundTripTests(TestCase):
    """POST /api/v1/exp/ 로 올린 세션을 DB, 조회 API, AI 리포트 조립에서 다시 읽는다."""

    client_class = APIClient

    def setUp(self):
        super().setUp()
        # 업로드가 쓰는 그림 파일을 저장소의 media 폴더가 아니라 임시 폴더로 보낸다.
        self.media_dir = tempfile.mkdtemp(prefix='oneclick_test_media_')
        self.addCleanup(shutil.rmtree, self.media_dir)
        media = override_settings(MEDIA_ROOT=self.media_dir)
        media.enable()
        self.addCleanup(media.disable)

        # 업로드는 리포트를 만들지 않는다(클라이언트가 따로 요청한다). 누가 생성 경로를
        # 건드려도 Claude 로 나가지 않고 바로 실패하도록 막아 둔다.
        self.ai_guards = []
        for target in ('experiments.views.anthropic.Anthropic',
                       'experiments.views._stream_ai_message'):
            patcher = mock.patch(
                target, side_effect=AssertionError('Claude 호출이 일어났다: ' + target))
            self.ai_guards.append((target, patcher.start()))
            self.addCleanup(patcher.stop)

        # 설문 저장·figure 합성의 INFO 로그가 테스트 출력을 덮지 않게 한다.
        # (로그를 확인하는 테스트는 assertLogs 를 쓴다 — 수준을 스스로 낮춘다.)
        log = logging.getLogger('experiments')
        self.addCleanup(log.setLevel, log.level)
        log.setLevel(logging.WARNING)

        # 조회 API 는 로그인이 필요하고, 측정 장비는 토큰 없이 올린다(업로드만 AllowAny).
        self.client.force_authenticate(
            user=get_user_model().objects.create_user(username='phase-tester'))
        self.device = APIClient()

    def tearDown(self):
        for target, guard in self.ai_guards:
            self.assertFalse(guard.called, target)
        super().tearDown()

    # --- 요청 도우미 ---
    def upload(self, body):
        """본문을 올리고 (저장된 Experiments, 이 업로드가 쓴 파일 수) 를 돌려준다."""
        # 파일이 임시 폴더로 가는지 먼저 확인한다. 어긋나면 저장소의 media 폴더가 더럽혀진다.
        self.assertEqual(os.path.normcase(os.path.abspath(default_storage.location)),
                         os.path.normcase(os.path.abspath(self.media_dir)))
        before = len(os.listdir(self.media_dir))
        with warnings.catch_warnings():
            # 클라이언트는 시간대 없는 'YYYY-MM-DD HH:MM' 을 보낸다. USE_TZ 라 Django 가
            # naive datetime 경고를 내는데, 기존 동작이고 여기서 보려는 것이 아니다.
            warnings.filterwarnings('ignore', message='.*received a naive datetime',
                                    category=RuntimeWarning)
            response = self.device.post('/api/v1/exp/', data=json.dumps(body),
                                        content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()
        self.assertEqual(result['result'], 'Success!!')
        exp = Experiments.objects.get(pk=result['pk'])
        return exp, len(os.listdir(self.media_dir)) - before

    def get_json(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    # --- 확인 도우미 ---
    def assertSession(self, exp, phases):
        """세션에 있는 phase·diff 만 저장되고, 저장된 것이 제 키의 것인지."""
        hrv, eeg = exp.hrv, exp.eeg
        n_pairs = len(phases) - 1

        for key in UNION:
            present = key in phases
            self.assertEqual(getattr(hrv, key + '_id') is not None, present, 'HRV.' + key)
            self.assertEqual(getattr(eeg, key + '_id') is not None, present, 'EEG.' + key)
        for slot in range(1, MAX_DIFFS + 1):
            self.assertEqual(getattr(eeg, 'diff%d_id' % slot) is not None, slot <= n_pairs,
                             'EEG.diff%d' % slot)

        # AI 리포트는 합집합을 FK 유무로 걸러 쓴다. 걸러낸 순서가 이 세션의 시간 순서여야 한다.
        union = [key for key, _ in views.CONNECTIVITY_PHASES]
        self.assertEqual([key for key in union if getattr(hrv, key) is not None], phases)
        self.assertEqual([key for key in union if getattr(eeg, key) is not None], phases)

        # 값과 그림이 제 phase·슬롯의 것인지 (키가 다른 테이블로 새지 않았는지).
        for key in phases:
            i = UNION.index(key)
            hrv_phase, eeg_phase = getattr(hrv, key), getattr(eeg, key)
            self.assertEqual(hrv_phase.sdnn, 40.0 + i, key)
            self.assertEqual(read(hrv_phase.heart_rate), png(150 + i, 1), key)
            self.assertEqual(read(hrv_phase.comparison), png(150 + i, 2), key)
            self.assertEqual(read(eeg_phase.topography_delta),
                             eeg_png(key, 'topography', 'delta'), key)
            self.assertEqual(read(eeg_phase.connectivity2_sigma),
                             eeg_png(key, 'connectivity2', 'sigma'), key)
        for slot in range(1, n_pairs + 1):
            diff = getattr(eeg, 'diff%d' % slot)
            for name in ('topography_delta', 'connectivity2_sigma', 'connectivity_gamma_rem'):
                self.assertEqual(read(getattr(diff, name)), diff_png(slot, name),
                                 'diff%d.%s' % (slot, name))

        # FAA: 세션당 한 행에 phase 별 그림 컬럼.
        faa_row = EEGFAA.objects.values().get(pk=eeg.faa_id)
        for key in UNION:
            name = 'faa_' + key
            if key in phases:
                self.assertEqual(read(getattr(eeg.faa, name)), faa_png(key), name)
            elif key in NINE_PHASE[:5]:
                # 처음 5컬럼은 NOT NULL 이라 값이 없으면 0바이트 파일이 들어간다 (기존 동작).
                self.assertEqual(getattr(eeg.faa, name).size, 0, name)
            else:
                # 나중에 추가된 컬럼은 값이 없으면 비워 둔다. Django 는 값이 없는 FileField 를
                # NULL 이 아니라 빈 문자열로 저장한다(컬럼을 추가하기 전에 쌓인 행만 NULL 이다).
                # 어느 쪽이든 파일은 생기지 않고 조회 응답은 null 이다.
                self.assertIn(faa_row[name], (None, ''), name)
                self.assertFalse(getattr(eeg.faa, name), name)

    def assertResponses(self, exp, phases):
        """조회 API 응답: 합집합 키가 전부 나오고, 세션에 있는 것은 객체, 없는 것은 null."""
        hrv = self.get_json('/api/v1/ecg/hrv/%d/' % exp.hrv_id)
        eeg = self.get_json('/api/v1/eeg/analysis/%d/' % exp.eeg_id)
        n_pairs = len(phases) - 1

        self.assertEqual([key for key in hrv if key in UNION], list(UNION))
        self.assertEqual([key for key in eeg if key in UNION], list(UNION))
        for key in UNION:
            if key in phases:
                self.assertEqual(hrv[key]['sdnn'], 40.0 + UNION.index(key), key)
                self.assertTrue(hrv[key]['heart_rate'].startswith('/media/'), key)
                self.assertTrue(eeg[key]['topography_delta'].startswith('/media/'), key)
            else:
                self.assertIsNone(hrv[key], key)
                self.assertIsNone(eeg[key], key)
        for slot in range(1, MAX_DIFFS + 1):
            key = 'diff%d' % slot
            self.assertIn(key, eeg)
            if slot <= n_pairs:
                self.assertTrue(eeg[key]['connectivity_gamma_rem'].startswith('/media/'), key)
            else:
                self.assertIsNone(eeg[key], key)
        for key in UNION:
            name = 'faa_' + key
            self.assertIn(name, eeg['faa'])
            if key in phases:
                self.assertTrue(eeg['faa'][name].startswith('/media/'), name)
            elif key not in NINE_PHASE[:5]:
                self.assertIsNone(eeg['faa'][name], name)
        # trigger 는 받은 그대로 저장되고 EEG 응답에 실려 나간다 (서버는 해석하지 않는다).
        self.assertEqual(eeg['trigger'], exp.trigger)

    def assertRowCounts(self, models, count):
        for model in models:
            self.assertEqual(model.objects.count(), count, model.__name__)

    # --- 모드별 왕복 ---
    def test_5_phase_session(self):
        phases = phases_for(5)
        # 9-phase 판 클라이언트는 빈 diff5~8 을 붙여 보내고, 그 전 판은 diff1~5 만 보낸다.
        # 어느 쪽이든 저장 결과가 같아야 한다.
        for n_diff_keys in (MAX_DIFFS, 5):
            with self.subTest(n_diff_keys=n_diff_keys):
                exp, n_files = self.upload(build_body(phases, n_diff_keys=n_diff_keys))
                self.assertSession(exp, phases)
                self.assertResponses(exp, phases)
                self.assertEqual(exp.trigger, [0, 5, 10, 15, 20, 25])
                self.assertEqual(n_files, 436)
                self.assertEqual(n_files, expected_file_count(phases))

                for key in ('stimulation3', 'recovery3', 'stimulation4', 'recovery4', 'recovery'):
                    self.assertIsNone(getattr(exp.hrv, key), key)
                    self.assertIsNone(getattr(exp.eeg, key), key)
                for slot in (5, 6, 7, 8):
                    self.assertIsNone(getattr(exp.eeg, 'diff%d' % slot), slot)
                # 빈 슬롯과 없는 phase 는 행 자체가 생기지 않는다.
                self.assertRowCounts(NEW_PHASE_MODELS + NEW_DIFF_MODELS, 0)

    def test_6_phase_session(self):
        phases = phases_for(6)
        exp, n_files = self.upload(build_body(phases))
        self.assertSession(exp, phases)
        self.assertResponses(exp, phases)
        self.assertEqual(len(exp.trigger), 7)
        self.assertEqual(n_files, 537)
        self.assertEqual(n_files, expected_file_count(phases))

        # 6-phase 의 마지막 구간은 recovery 다. 9-phase 의 recovery3·recovery4 와는 별개 키다.
        self.assertIsNotNone(exp.hrv.recovery)
        self.assertIsNotNone(exp.eeg.recovery)
        for key in ('recovery1', 'recovery2', 'recovery3', 'recovery4'):
            self.assertIsNone(getattr(exp.hrv, key), key)
            self.assertIsNone(getattr(exp.eeg, key), key)
        self.assertIsNotNone(exp.eeg.diff5)             # 인접 쌍 5개
        for slot in (6, 7, 8):
            self.assertIsNone(getattr(exp.eeg, 'diff%d' % slot), slot)
        self.assertRowCounts(NEW_PHASE_MODELS + NEW_DIFF_MODELS, 0)

    def test_9_phase_session(self):
        phases = phases_for(9)
        exp, n_files = self.upload(build_body(phases))
        self.assertSession(exp, phases)
        self.assertResponses(exp, phases)
        self.assertEqual(exp.trigger, [0, 5, 10, 15, 20, 25, 30, 35, 40, 45])   # 경계값 10개
        self.assertEqual(n_files, 832)
        self.assertEqual(n_files, expected_file_count(phases))

        for key in ('recovery3', 'recovery4'):
            self.assertIsNotNone(getattr(exp.hrv, key), key)
            self.assertIsNotNone(getattr(exp.eeg, key), key)
            self.assertTrue(getattr(exp.eeg.faa, 'faa_' + key), key)
        # 9-phase 의 마지막 구간은 recovery4 다. 6-phase 의 recovery 는 비어 있어야 한다.
        self.assertIsNone(exp.hrv.recovery)
        self.assertIsNone(exp.eeg.recovery)
        self.assertFalse(exp.eeg.faa.faa_recovery)
        for slot in (6, 7, 8):                          # 인접 쌍 8개
            self.assertIsNotNone(getattr(exp.eeg, 'diff%d' % slot), slot)
        self.assertRowCounts(NEW_PHASE_MODELS + NEW_DIFF_MODELS, 1)

        # 조회 응답에서 새 키는 PK 정수가 아니라 중첩 객체로 나간다.
        hrv = self.get_json('/api/v1/ecg/hrv/%d/' % exp.hrv_id)
        eeg = self.get_json('/api/v1/eeg/analysis/%d/' % exp.eeg_id)
        for key in ('recovery3', 'recovery4'):
            self.assertEqual(hrv[key]['id'], getattr(exp.hrv, key + '_id'), key)
            self.assertEqual(hrv[key]['psd'], hrv_phase_payload(key)['psd'], key)
            self.assertEqual(eeg[key]['id'], getattr(exp.eeg, key + '_id'), key)
        for slot in (6, 7, 8):
            self.assertEqual(eeg['diff%d' % slot]['id'], getattr(exp.eeg, 'diff%d_id' % slot))
        # spindle_coupling 은 JSON 한 덩어리라 스키마 변경 없이 새 phase 키가 그대로 들어간다.
        self.assertEqual(sorted(eeg['spindle_coupling']), sorted(phases))

    def test_7_phase_session(self):
        # 9-phase 측정이 3차 회복에서 끝난 세션. 9-phase 순서의 앞 7개로 저장된다.
        phases = phases_for(7)
        self.assertEqual(phases, ['baseline', 'stimulation1', 'recovery1', 'stimulation2',
                                  'recovery2', 'stimulation3', 'recovery3'])
        exp, n_files = self.upload(build_body(phases))
        self.assertSession(exp, phases)
        self.assertResponses(exp, phases)
        self.assertEqual(len(exp.trigger), 8)
        self.assertEqual(n_files, 634)
        self.assertEqual(n_files, expected_file_count(phases))

        self.assertIsNotNone(exp.hrv.recovery3)
        self.assertIsNotNone(exp.eeg.recovery3)
        self.assertTrue(exp.eeg.faa.faa_recovery3)
        for key in ('stimulation4', 'recovery4', 'recovery'):
            self.assertIsNone(getattr(exp.hrv, key), key)
            self.assertIsNone(getattr(exp.eeg, key), key)
            self.assertFalse(getattr(exp.eeg.faa, 'faa_' + key), key)
        self.assertIsNotNone(exp.eeg.diff6)             # 인접 쌍 6개
        self.assertIsNone(exp.eeg.diff7)
        self.assertIsNone(exp.eeg.diff8)
        self.assertRowCounts((HRVRecovery3, EEGRecovery3, EEGDiff6), 1)
        self.assertRowCounts((HRVRecovery4, EEGRecovery4, EEGDiff7, EEGDiff8), 0)

    def test_1_and_3_phase_sessions(self):
        for n in (1, 3):
            with self.subTest(n_phases=n):
                phases = phases_for(n)
                exp, n_files = self.upload(build_body(phases))
                self.assertSession(exp, phases)
                self.assertResponses(exp, phases)
                self.assertEqual(n_files, expected_file_count(phases))
        self.assertRowCounts(NEW_PHASE_MODELS + NEW_DIFF_MODELS, 0)

    def test_unknown_keys_are_dropped_without_error(self):
        # 서버는 아는 키만 골라 읽는다. 모르는 phase·diff 키가 섞여 와도 200 이고 그 키만 버려진다.
        # (옛 클라이언트는 지원하지 않는 트리거 개수에서 'phase5' 같은 이름을 만들어 보냈다.)
        phases = phases_for(5)
        body = build_body(phases)
        hrv, eeg = json.loads(body['hrv']), json.loads(body['eeg'])
        hrv['phase5'] = hrv_phase_payload('recovery2')
        eeg['phase5'] = eeg_phase_payload('recovery2')
        eeg['phase6'] = eeg_phase_payload('recovery2')
        eeg['diff9'] = diff_payload(9)
        eeg['faa']['faa_phase5'] = b64(png(1))
        eeg['spindle_coupling']['phase5'] = coupling_payload(5)
        trigger = build_trigger(7)
        body.update(hrv=json.dumps(hrv), eeg=json.dumps(eeg), trigger=trigger)

        exp, n_files = self.upload(body)
        # 아는 키는 5-phase 세션 그대로 저장되고, 버려진 키는 행으로도 파일로도 남지 않는다.
        self.assertSession(exp, phases)
        self.assertResponses(exp, phases)
        self.assertEqual(n_files, expected_file_count(phases))
        self.assertRowCounts(NEW_PHASE_MODELS + NEW_DIFF_MODELS, 0)

        hrv_json = self.get_json('/api/v1/ecg/hrv/%d/' % exp.hrv_id)
        eeg_json = self.get_json('/api/v1/eeg/analysis/%d/' % exp.eeg_id)
        self.assertNotIn('phase5', hrv_json)
        for key in ('phase5', 'phase6', 'diff9'):
            self.assertNotIn(key, eeg_json)
        self.assertNotIn('faa_phase5', eeg_json['faa'])
        # trigger 와 spindle_coupling 은 JSON 그대로 저장된다 (서버는 해석하지 않는다).
        self.assertEqual(exp.trigger, trigger)
        self.assertEqual(eeg_json['trigger'], trigger)
        self.assertIn('phase5', eeg_json['spindle_coupling'])

    # --- AI 리포트 조립 (Claude 는 부르지 않는다) ---
    def test_report_data_follows_session_order(self):
        for n in (5, 6, 7, 9):
            with self.subTest(n_phases=n):
                phases = phases_for(n)
                exp, _ = self.upload(build_body(phases))
                data = views._collect_report_data(views._report_queryset().get(pk=exp.pk))

                names = [key[0].upper() + key[1:] for key in phases]
                self.assertEqual([p['name'] for p in data['hrv_phases']], names)
                self.assertEqual([p['name_ko'] for p in data['hrv_phases']],
                                 [LABELS_KO[key] for key in phases])
                self.assertEqual([p['sdnn'] for p in data['hrv_phases']],
                                 [40.0 + UNION.index(key) for key in phases])
                self.assertEqual([p['name'] for p in data['hrv_images']], names)

                figures = data['figures']
                # FAA 는 합집합 키가 전부 나오고 세션에 없는 것은 None 이다.
                self.assertEqual(list(figures['faa']), list(UNION))
                for key in UNION:
                    self.assertEqual(figures['faa'][key] is not None, key in phases, key)
                # 지형도·연결성은 세션에 있는 phase 만, 시간 순서로.
                self.assertEqual(list(figures['topography']), phases)
                self.assertEqual(list(figures['connectivity']), phases)

                # 순서만 맞고 그림이 엇갈리면 안 된다. URL 이 제 phase 의 파일을 가리키는지 본다.
                for item, key in zip(data['hrv_images'], phases):
                    hrv_phase = getattr(exp.hrv, key)
                    self.assertEqual(item['heart_rate'], hrv_phase.heart_rate.url, key)
                    self.assertEqual(item['comparison'], hrv_phase.comparison.url, key)
                for key in phases:
                    eeg_phase = getattr(exp.eeg, key)
                    self.assertEqual(figures['faa'][key],
                                     getattr(exp.eeg.faa, 'faa_' + key).url, key)
                    for band in views.CONNECTIVITY_BANDS:
                        self.assertEqual(figures['topography'][key][band],
                                         getattr(eeg_phase, 'topography_' + band).url, key)
                        self.assertEqual(figures['connectivity'][key][band],
                                         getattr(eeg_phase, 'connectivity_' + band).url, key)

    def test_saved_report_get_for_9_phase(self):
        # 저장된 리포트가 있으면 GET 은 Claude 를 부르지 않고 수치·그림 URL 만 다시 조립한다.
        phases = phases_for(9)
        exp, _ = self.upload(build_body(phases))
        saved = {'headline': {'one_liner': 'test'}}
        Experiments.objects.filter(pk=exp.pk).update(ai_report=json.dumps(saved))

        data = self.get_json('/api/v1/exp/%d/ai-report/' % exp.pk)
        self.assertEqual(data['ai'], saved)
        self.assertEqual([p['name'] for p in data['hrv_phases']],
                         ['Baseline', 'Stimulation1', 'Recovery1', 'Stimulation2', 'Recovery2',
                          'Stimulation3', 'Recovery3', 'Stimulation4', 'Recovery4'])
        self.assertEqual(data['hrv_phases'][6]['name_ko'], '3차 회복')
        self.assertEqual(data['hrv_phases'][8]['name_ko'], '4차 회복')
        self.assertEqual(list(data['figures']['topography']), phases)
        self.assertTrue(data['figures']['faa']['recovery3'].startswith('/media/'))
        self.assertTrue(data['figures']['faa']['recovery4'].startswith('/media/'))
        self.assertIsNone(data['figures']['faa']['recovery'])
        self.assertEqual(sorted(data['coupling']), sorted(phases))

    def test_figure_grid_and_prompt_for_9_phase(self):
        phases = phases_for(9)
        labels_ko = [LABELS_KO[key] for key in phases]
        exp, _ = self.upload(build_body(phases))
        exp = views._report_queryset().get(pk=exp.pk)

        with self.assertLogs('experiments.views', level='INFO') as logs, \
                mock.patch.object(views, '_compose_grid', wraps=views._compose_grid) as compose:
            blocks, labels = views._collect_connectivity_blocks(exp)
        # 대역마다 '지표 2행 x phase 9열' 격자 한 장으로 합쳐진다 (낱장으로 새지 않는다).
        images = [block for block in blocks if block['type'] == 'image']
        self.assertEqual(len(images), len(views.CONNECTIVITY_BANDS))
        self.assertLessEqual(len(images), views.AI_REPORT_MAX_IMAGES)
        self.assertEqual(sum('격자 2x9 합성' in line for line in logs.output),
                         len(views.CONNECTIVITY_BANDS))
        # 격자의 칸마다 제 phase 의 그림이 들어갔는지: 행은 지표, 열은 시간 순서의 phase 다.
        # (그림 앞 설명 글의 열 이름만 맞고 그림이 엇갈리면 모델이 엉뚱한 구간을 해석한다.)
        self.assertEqual(compose.call_count, len(views.CONNECTIVITY_BANDS))
        for band, call in zip(views.CONNECTIVITY_BANDS, compose.call_args_list):
            self.assertEqual(call.args[0],
                             [[eeg_png(key, prefix, band) for key in phases]
                              for prefix, _metric in views.BAND_FIGURE_ROWS], band)
        texts = [block['text'] for block in blocks if block['type'] == 'text']
        self.assertEqual(len(texts), len(views.CONNECTIVITY_BANDS))
        for text in texts:
            self.assertIn('2행 9열 격자', text)
            self.assertIn('열 순서(왼쪽→오른쪽): ' + ', '.join(labels_ko), text)
        self.assertEqual([label['band'] for label in labels], list(views.CONNECTIVITY_BANDS))
        for label in labels:
            for _prefix, metric in views.BAND_FIGURE_ROWS:
                self.assertEqual([item['phase'] for item in label['items']
                                  if item['metric'] == metric], phases, label['band'])

        prompt = views._build_ai_prompt_json(views._collect_report_data(exp), labels)
        # [심박변이도 — 구간별] 의 줄이 시간 순서이고, 영문 키와 한글 라벨이 짝이 맞는지.
        self.assertEqual(re.findall(r'^  (\w+) \(([^)]+)\): 이완지표', prompt, flags=re.M),
                         [(key, LABELS_KO[key]) for key in phases])
        self.assertIn('지형도(파워 분포)(3차 회복)', prompt)
        self.assertIn('연결성(4차 회복)', prompt)
