# -*- coding:utf-8 -*-
import base64
import json
import os
import anthropic
from rest_framework.views import APIView
from rest_framework import status
from rest_framework.response import Response
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from .serializer import ExperimentsViewSerializer
from .models import Experiments
from ecg.models import *
from eeg.models import *
from report.models import *
from django.db.models import Q
from uuid import uuid4
from django.core.files.base import ContentFile


class ExperimentsPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 1000


class ExperimentView(APIView):
    permission_classes = [AllowAny]

    @staticmethod
    def get_value(data, param_name):
        try:
            value = data[param_name]
        except KeyError:
            value = None
        return value

    @staticmethod
    def base64_file(data, name=None):
        return ContentFile(base64.b64decode(data) if data else b'',
                           name='{}.jpg'.format(str(uuid4())))

    def eeg_obj_save(self, obj, name, contents):
        eeg = obj.objects.create(
            topography_delta=self.base64_file(contents[name]['topography_delta']),
            topography_theta=self.base64_file(contents[name]['topography_theta']),
            topography_alpha=self.base64_file(contents[name]['topography_alpha']),
            topography_sigma=self.base64_file(contents[name]['topography_sigma']),
            topography_beta=self.base64_file(contents[name]['topography_beta']),
            topography_gamma=self.base64_file(contents[name]['topography_gamma']),
            connectivity_delta=self.base64_file(contents[name]['connectivity_delta']),
            connectivity_theta=self.base64_file(contents[name]['connectivity_theta']),
            connectivity_alpha=self.base64_file(contents[name]['connectivity_alpha']),
            connectivity_sigma=self.base64_file(contents[name]['connectivity_sigma']),
            connectivity_beta=self.base64_file(contents[name]['connectivity_beta']),
            connectivity_gamma=self.base64_file(contents[name]['connectivity_gamma']),
            connectivity2_delta=self.base64_file(contents[name]['connectivity2_delta']),
            connectivity2_theta=self.base64_file(contents[name]['connectivity2_theta']),
            connectivity2_alpha=self.base64_file(contents[name]['connectivity2_alpha']),
            connectivity2_sigma=self.base64_file(contents[name]['connectivity2_sigma']),
            connectivity2_beta=self.base64_file(contents[name]['connectivity2_beta']),
            connectivity2_gamma=self.base64_file(contents[name]['connectivity2_gamma'])
        )
        return eeg

    def eeg_diff_obj_save(self, obj, name, contents):
        from collections import defaultdict
        c = defaultdict(str, contents[name])
        eeg = obj.objects.create(
            topography_delta=self.base64_file(c['topography_delta']),
            topography_theta=self.base64_file(c['topography_theta']),
            topography_alpha=self.base64_file(c['topography_alpha']),
            topography_sigma=self.base64_file(c['topography_sigma']),
            topography_beta=self.base64_file(c['topography_beta']),
            topography_gamma=self.base64_file(c['topography_gamma']),
            connectivity_delta=self.base64_file(c['connectivity_delta']),
            connectivity_theta=self.base64_file(c['connectivity_theta']),
            connectivity_alpha=self.base64_file(c['connectivity_alpha']),
            connectivity_sigma=self.base64_file(c['connectivity_sigma']),
            connectivity_beta=self.base64_file(c['connectivity_beta']),
            connectivity_gamma=self.base64_file(c['connectivity_gamma']),
            connectivity2_delta=self.base64_file(c['connectivity2_delta']),
            connectivity2_theta=self.base64_file(c['connectivity2_theta']),
            connectivity2_alpha=self.base64_file(c['connectivity2_alpha']),
            connectivity2_sigma=self.base64_file(c['connectivity2_sigma']),
            connectivity2_beta=self.base64_file(c['connectivity2_beta']),
            connectivity2_gamma=self.base64_file(c['connectivity2_gamma']),
            # >> WAKE stage
            topography_delta_wake=self.base64_file(c['topography_delta_wake']),
            topography_theta_wake=self.base64_file(c['topography_theta_wake']),
            topography_alpha_wake=self.base64_file(c['topography_alpha_wake']),
            topography_sigma_wake=self.base64_file(c['topography_sigma_wake']),
            topography_beta_wake=self.base64_file(c['topography_beta_wake']),
            topography_gamma_wake=self.base64_file(c['topography_gamma_wake']),
            connectivity_delta_wake=self.base64_file(c['connectivity_delta_wake']),
            connectivity_theta_wake=self.base64_file(c['connectivity_theta_wake']),
            connectivity_alpha_wake=self.base64_file(c['connectivity_alpha_wake']),
            connectivity_sigma_wake=self.base64_file(c['connectivity_sigma_wake']),
            connectivity_beta_wake=self.base64_file(c['connectivity_beta_wake']),
            connectivity_gamma_wake=self.base64_file(c['connectivity_gamma_wake']),
            # >> N1 stage
            topography_delta_n1=self.base64_file(c['topography_delta_n1']),
            topography_theta_n1=self.base64_file(c['topography_theta_n1']),
            topography_alpha_n1=self.base64_file(c['topography_alpha_n1']),
            topography_sigma_n1=self.base64_file(c['topography_sigma_n1']),
            topography_beta_n1=self.base64_file(c['topography_beta_n1']),
            topography_gamma_n1=self.base64_file(c['topography_gamma_n1']),
            connectivity_delta_n1=self.base64_file(c['connectivity_delta_n1']),
            connectivity_theta_n1=self.base64_file(c['connectivity_theta_n1']),
            connectivity_alpha_n1=self.base64_file(c['connectivity_alpha_n1']),
            connectivity_sigma_n1=self.base64_file(c['connectivity_sigma_n1']),
            connectivity_beta_n1=self.base64_file(c['connectivity_beta_n1']),
            connectivity_gamma_n1=self.base64_file(c['connectivity_gamma_n1']),
            # >> N2 stage
            topography_delta_n2=self.base64_file(c['topography_delta_n2']),
            topography_theta_n2=self.base64_file(c['topography_theta_n2']),
            topography_alpha_n2=self.base64_file(c['topography_alpha_n2']),
            topography_sigma_n2=self.base64_file(c['topography_sigma_n2']),
            topography_beta_n2=self.base64_file(c['topography_beta_n2']),
            topography_gamma_n2=self.base64_file(c['topography_gamma_n2']),
            connectivity_delta_n2=self.base64_file(c['connectivity_delta_n2']),
            connectivity_theta_n2=self.base64_file(c['connectivity_theta_n2']),
            connectivity_alpha_n2=self.base64_file(c['connectivity_alpha_n2']),
            connectivity_sigma_n2=self.base64_file(c['connectivity_sigma_n2']),
            connectivity_beta_n2=self.base64_file(c['connectivity_beta_n2']),
            connectivity_gamma_n2=self.base64_file(c['connectivity_gamma_n2']),
            # >> N3 stage
            topography_delta_n3=self.base64_file(c['topography_delta_n3']),
            topography_theta_n3=self.base64_file(c['topography_theta_n3']),
            topography_alpha_n3=self.base64_file(c['topography_alpha_n3']),
            topography_sigma_n3=self.base64_file(c['topography_sigma_n3']),
            topography_beta_n3=self.base64_file(c['topography_beta_n3']),
            topography_gamma_n3=self.base64_file(c['topography_gamma_n3']),
            connectivity_delta_n3=self.base64_file(c['connectivity_delta_n3']),
            connectivity_theta_n3=self.base64_file(c['connectivity_theta_n3']),
            connectivity_alpha_n3=self.base64_file(c['connectivity_alpha_n3']),
            connectivity_sigma_n3=self.base64_file(c['connectivity_sigma_n3']),
            connectivity_beta_n3=self.base64_file(c['connectivity_beta_n3']),
            connectivity_gamma_n3=self.base64_file(c['connectivity_gamma_n3']),
            # >> REM stage
            topography_delta_rem=self.base64_file(c['topography_delta_rem']),
            topography_theta_rem=self.base64_file(c['topography_theta_rem']),
            topography_alpha_rem=self.base64_file(c['topography_alpha_rem']),
            topography_sigma_rem=self.base64_file(c['topography_sigma_rem']),
            topography_beta_rem=self.base64_file(c['topography_beta_rem']),
            topography_gamma_rem=self.base64_file(c['topography_gamma_rem']),
            connectivity_delta_rem=self.base64_file(c['connectivity_delta_rem']),
            connectivity_theta_rem=self.base64_file(c['connectivity_theta_rem']),
            connectivity_alpha_rem=self.base64_file(c['connectivity_alpha_rem']),
            connectivity_sigma_rem=self.base64_file(c['connectivity_sigma_rem']),
            connectivity_beta_rem=self.base64_file(c['connectivity_beta_rem']),
            connectivity_gamma_rem=self.base64_file(c['connectivity_gamma_rem']),
        )
        return eeg

    def ecg_obj_save(self, obj, name, contents):
        ecg = obj.objects.create(
            sdnn=contents[name]['sdnn'],
            rmssd=contents[name]['rmssd'],
            sdsd=contents[name]['sdsd'],
            nn50=contents[name]['nn50'],
            pnn50=contents[name]['pnn50'],
            tri_index=contents[name]['tri_index'],
            vlf_rel_power=contents[name]['vlf_rel_power'],
            lf_rel_power=contents[name]['lf_rel_power'],
            hf_rel_power=contents[name]['hf_rel_power'],
            lh_ratio=contents[name]['lh_ratio'],
            norm_lf=contents[name]['norm_lf'],
            norm_hf=contents[name]['norm_hf'],
            psd=contents[name]['psd'],
            heart_rate=self.base64_file(contents[name]['heart_rate']),
            comparison=self.base64_file(contents[name]['comparison']),
        )
        return ecg

    def post(self, request):
        data = request.data
        hrv = None
        eeg = None
        report = None

        try:
            if str(data['hrv']).strip() != '""':
                hrv_data = json.loads(data['hrv'])
                hrv = HRV.objects.create(
                    nni=hrv_data['nni'],
                    rmssd=hrv_data['rmssd'],
                    baseline=self.ecg_obj_save(HRVBaseline, 'baseline', hrv_data),
                    stimulation1=self.ecg_obj_save(HRVStimulation1, 'stimulation1', hrv_data) if hrv_data.get('stimulation1') else None,
                    recovery1=self.ecg_obj_save(HRVRecovery1, 'recovery1', hrv_data) if hrv_data.get('recovery1') else None,
                    stimulation2=self.ecg_obj_save(HRVStimulation2, 'stimulation2', hrv_data) if hrv_data.get('stimulation2') else None,
                    recovery2=self.ecg_obj_save(HRVRecovery2, 'recovery2', hrv_data) if hrv_data.get('recovery2') else None,
                )

            if str(data['eeg']).strip() != '""':
                eeg_data = json.loads(data['eeg'])
                eeg = EEG.objects.create(
                    psd=eeg_data['psd'],
                    sleep_staging=eeg_data['sleep_staging'],
                    frontal_limbic=EEGFrontalLimbic.objects.create(
                        delta=self.base64_file(eeg_data['frontal_limbic']['delta']),
                        theta=self.base64_file(eeg_data['frontal_limbic']['theta']),
                        alpha=self.base64_file(eeg_data['frontal_limbic']['alpha']),
                        sigma=self.base64_file(eeg_data['frontal_limbic']['sigma']),
                        beta=self.base64_file(eeg_data['frontal_limbic']['beta']),
                        gamma=self.base64_file(eeg_data['frontal_limbic']['gamma'])
                    ),
                    baseline=self.eeg_obj_save(EEGBaseline, 'baseline', eeg_data),
                    stimulation1=self.eeg_obj_save(EEGStimulation1, 'stimulation1', eeg_data) if eeg_data.get('stimulation1', {}).get('topography_delta') else None,
                    recovery1=self.eeg_obj_save(EEGRecovery1, 'recovery1', eeg_data) if eeg_data.get('recovery1', {}).get('topography_delta') else None,
                    stimulation2=self.eeg_obj_save(EEGStimulation2, 'stimulation2', eeg_data) if eeg_data.get('stimulation2', {}).get('topography_delta') else None,
                    recovery2=self.eeg_obj_save(EEGRecovery2, 'recovery2', eeg_data) if eeg_data.get('recovery2', {}).get('topography_delta') else None,
                    diff1=self.eeg_diff_obj_save(EEGDiff1, 'diff1', eeg_data) if eeg_data.get('diff1') else None,
                    diff2=self.eeg_diff_obj_save(EEGDiff2, 'diff2', eeg_data) if eeg_data.get('diff2') else None,
                    diff3=self.eeg_diff_obj_save(EEGDiff3, 'diff3', eeg_data) if eeg_data.get('diff3') else None,
                    diff4=self.eeg_diff_obj_save(EEGDiff4, 'diff4', eeg_data) if eeg_data.get('diff4') else None,
                    psd_spectrogram=EEGPSDSpectrogram.objects.create(
                        **{k: self.base64_file(v) for k, v in (lambda s: {
                            'cz':  s.get('cz',  s.get('Cz',  '')),
                            'c3':  s.get('c3',  s.get('C3',  '')),
                            'c4':  s.get('c4',  s.get('C4',  '')),
                            'fp1': s.get('fp1', s.get('Fp1', '')),
                            'fp2': s.get('fp2', s.get('Fp2', '')),
                            'f3':  s.get('f3',  s.get('F3',  '')),
                            'f4':  s.get('f4',  s.get('F4',  '')),
                            'f7':  s.get('f7',  s.get('F7',  '')),
                            'f8':  s.get('f8',  s.get('F8',  '')),
                            't3':  s.get('t3',  s.get('T3',  '')),
                            't4':  s.get('t4',  s.get('T4',  '')),
                            'p3':  s.get('p3',  s.get('P3',  '')),
                            'p4':  s.get('p4',  s.get('P4',  '')),
                        })(eeg_data.get('psd_spectrogram') or
                           eeg_data.get('brain_spectrogram') or {}).items()}
                    ),
                    faa=EEGFAA.objects.create(
                        faa_baseline=self.base64_file(eeg_data['faa'].get('faa_baseline')),
                        faa_stimulation1=self.base64_file(eeg_data['faa'].get('faa_stimulation1')),
                        faa_recovery1=self.base64_file(eeg_data['faa'].get('faa_recovery1')),
                        faa_stimulation2=self.base64_file(eeg_data['faa'].get('faa_stimulation2')),
                        faa_recovery2=self.base64_file(eeg_data['faa'].get('faa_recovery2'))
                    ),
                )

            if str(data['report']).strip() != '""':
                rpt = json.loads(data['report'])
                report = Report.objects.create(
                    tib=rpt['tib'],
                    twt=rpt['twt'],
                    tst=rpt['tst'],
                    waso=rpt['waso'],
                    sleep_latency=rpt['sleep_latency'],
                    rem_latency=rpt['rem_latency'],
                    sleep_eff=rpt['sleep_eff'],

                    sleep_n1_tst=rpt['sleep_n1_tst'],
                    sleep_n2_tst=rpt['sleep_n2_tst'],
                    sleep_n3_tst=rpt['sleep_n3_tst'],
                    sleep_nrem_tst=rpt['sleep_nrem_tst'],
                    sleep_rem_tst=rpt['sleep_rem_tst'],

                    sleep_n1_min=rpt['sleep_n1_min'],
                    sleep_n2_min=rpt['sleep_n2_min'],
                    sleep_n3_min=rpt['sleep_n3_min'],
                    sleep_nrem_min=rpt['sleep_nrem_min'],
                    sleep_rem_min=rpt['sleep_rem_min'],
                )

            age = self.get_value(data, 'age')
            birth = self.get_value(data, 'birth')
            sex = self.get_value(data, 'sex')
            measurement_date = self.get_value(data, 'measurement_date')
            trigger = self.get_value(data, 'trigger')

            exp = Experiments(name=data['name'], age=age, birth=birth, sex=sex,
                              measurement_date=measurement_date,
                              hrv=hrv,
                              eeg=eeg,
                              report=report,
                              trigger=trigger)
            exp.save()
            return Response(
                {'result': 'Success!!'},
                status=status.HTTP_200_OK
            )
        except Exception as e:
            print(e)
            return Response(
                {'result': 'Failed!! Contact Your Administrator!!'},
                status=status.HTTP_400_BAD_REQUEST
            )


class ExperimentDeleteView(APIView):
    def delete(self, request, pk):
        try:
            exp = Experiments.objects.get(pk=pk)
        except Experiments.DoesNotExist:
            return Response(
                status=status.HTTP_404_NOT_FOUND
            )
        exp.delete()
        return Response(
            status=status.HTTP_200_OK
        )


class ExperimentListView(ListAPIView):
    pagination_class = ExperimentsPagination
    serializer_class = ExperimentsViewSerializer

    def get_queryset(self):
        queryset = Experiments.objects.filter()
        queryset = self.filter_queryset(queryset)
        return queryset

    def filter_queryset(self, queryset):
        name = self.request.GET.get('name', '')
        sorting = self.request.GET.get('sorting')
        descending = self.request.GET.get('descending')

        if name:
            terms = [t for t in name.strip().split() if t]
            for term in terms:
                queryset = queryset.filter(name__icontains=term)

        if sorting:
            if descending == 'True':
                queryset = queryset.order_by(f'-{sorting}')
            else:
                queryset = queryset.order_by(f'{sorting}')
        else:
            queryset = queryset.order_by('-pk')

        return queryset


def _img_url(field):
    """ImageField → 미디어 상대 URL (/media/...). 없거나 0바이트이면 None."""
    try:
        if not field or not field.name:
            return None
        # 0바이트 파일(빈 base64로 저장된 경우) 제외
        try:
            if field.size == 0:
                return None
        except Exception:
            pass  # size 확인 불가 시 그냥 URL 반환
        return field.url  # /media/경로
    except Exception:
        pass
    return None


def _fmt(v, unit='', precision=2):
    """숫자 값을 안전하게 포맷. None이면 'N/A' 반환."""
    if v is None:
        return 'N/A'
    try:
        return f"{round(float(v), precision)}{unit}"
    except (TypeError, ValueError):
        return str(v)


def _rmssd_change_symbol(prev, curr):
    """두 RMSSD 값을 비교해 ▲/▼/─ 반환."""
    if prev is None or curr is None:
        return '─'
    try:
        diff = float(curr) - float(prev)
        if diff > 1:
            return '▲'
        elif diff < -1:
            return '▼'
        return '─'
    except (TypeError, ValueError):
        return '─'


def _hrv_phase_summary(phase_obj):
    """HRVParameter 서브클래스 인스턴스에서 수치 지표를 dict로 반환."""
    if phase_obj is None:
        return None
    return {
        'sdnn':         phase_obj.sdnn,
        'rmssd':        phase_obj.rmssd,
        'sdsd':         phase_obj.sdsd,
        'nn50':         phase_obj.nn50,
        'pnn50':        phase_obj.pnn50,
        'tri_index':    phase_obj.tri_index,
        'vlf':          phase_obj.vlf_rel_power,
        'lf':           phase_obj.lf_rel_power,
        'hf':           phase_obj.hf_rel_power,
        'lh_ratio':     phase_obj.lh_ratio,
        'norm_lf':      phase_obj.norm_lf,
        'norm_hf':      phase_obj.norm_hf,
    }


def _sleep_stage_distribution_from_staging(staging_json):
    """
    sleep_staging JSON에서 수면 단계 분포(%) 반환.
    지원 형식:
      - dict: {'sleep_stage': [0,1,2,...], ...}  (0=W,1=N1,2=N2,3=N3,4=REM)
      - list: [[W_prob,N1_prob,...], ...]  (epoch별 확률 배열)
    """
    if not staging_json:
        return None

    counts = {'W': 0, 'N1': 0, 'N2': 0, 'N3': 0, 'REM': 0}
    int_to_label = {0: 'W', 1: 'N1', 2: 'N2', 3: 'N3', 4: 'REM'}
    str_labels = ['W', 'N1', 'N2', 'N3', 'REM']
    total = 0

    if isinstance(staging_json, dict):
        # 형식 1: {'sleep_stage': [0,0,1,2,...]}
        stages = staging_json.get('sleep_stage', [])
        if stages:
            for s in stages:
                key = int_to_label.get(int(s)) if isinstance(s, (int, float)) else None
                if key:
                    counts[key] += 1
                    total += 1
        if total == 0:
            # 형식 2: {'sleep_stage_prob': [[p0,p1,p2,p3,p4], ...]}
            probs = staging_json.get('sleep_stage_prob', [])
            for epoch in probs:
                if isinstance(epoch, (list, tuple)) and len(epoch) >= 5:
                    idx = epoch.index(max(epoch[:5]))
                    counts[str_labels[idx]] += 1
                    total += 1
    elif isinstance(staging_json, list):
        # 기존 형식: [[W,N1,N2,N3,REM], ...]
        for epoch in staging_json:
            if isinstance(epoch, (list, tuple)) and len(epoch) >= 5:
                idx = epoch.index(max(epoch[:5]))
                counts[str_labels[idx]] += 1
                total += 1

    if total == 0:
        return None
    return {k: round(v / total * 100, 1) for k, v in counts.items()}


def _eeg_psd_band_summary(psd_json):
    """
    EEG psd JSON에서 상대 파워 대역 요약 추출.
    지원 형식:
      - {'related_psd': [delta, theta, alpha, sigma, beta, gamma]}  (비율, ×100 → %)
      - {'delta': 0.18, 'theta': 0.16, ...}  (기존 형식)
    """
    if not psd_json or not isinstance(psd_json, dict):
        return None

    # 새 형식: related_psd 리스트 [delta, theta, alpha, sigma, beta, gamma]
    related = psd_json.get('related_psd')
    if related and isinstance(related, list) and len(related) >= 5:
        band_names = ['delta', 'theta', 'alpha', 'sigma', 'beta', 'gamma']
        result = {}
        for i, name in enumerate(band_names):
            if i < len(related):
                try:
                    result[name] = round(float(related[i]) * 100, 1)
                except (TypeError, ValueError):
                    pass
        return result if result else None

    # 기존 형식: {band: value}
    bands = ['delta', 'theta', 'alpha', 'sigma', 'beta', 'gamma']
    result = {}
    for b in bands:
        val = psd_json.get(b) or psd_json.get(b.capitalize())
        if val is not None:
            try:
                result[b] = round(float(val), 1)
            except (TypeError, ValueError):
                pass
    return result if result else None


def _build_ai_prompt(exp: 'Experiments') -> str:  # noqa: F821
    """NeuroTx Clinical Report 스타일의 임상 분석 프롬프트 생성."""

    sex_str = '남성(M)' if str(exp.sex) == '0' else ('여성(F)' if str(exp.sex) == '1' else 'N/A')
    mdate = exp.measurement_date.strftime('%Y-%m-%d %H:%M') if exp.measurement_date else 'N/A'

    # ── HRV 단계별 수치 수집 ──────────────────────────────────────
    hrv = exp.hrv
    phases = []
    phase_defs = [
        ('Baseline',     hrv.baseline     if hrv else None),
        ('Stimulation1', hrv.stimulation1 if hrv else None),
        ('Recovery1',    hrv.recovery1    if hrv else None),
        ('Stimulation2', hrv.stimulation2 if hrv else None),
        ('Recovery2',    hrv.recovery2    if hrv else None),
    ]
    for pname, pobj in phase_defs:
        d = _hrv_phase_summary(pobj)
        phases.append((pname, d))

    # RMSSD 단계별 변화 텍스트 생성
    rmssd_rows = []
    prev_rmssd = None
    for pname, d in phases:
        if d:
            rmssd_val = d['rmssd']
            sym = _rmssd_change_symbol(prev_rmssd, rmssd_val)
            rmssd_rows.append(f"  {pname:<14} RMSSD={_fmt(rmssd_val,'ms')}  {sym}")
            prev_rmssd = rmssd_val
        else:
            rmssd_rows.append(f"  {pname:<14} 데이터 없음")

    # 최대 RMSSD / Baseline RMSSD → vagal responsiveness 계산
    rmssd_vals = [d['rmssd'] for _, d in phases if d and d['rmssd'] is not None]
    vagal_grade = 'N/A'
    if len(rmssd_vals) >= 2:
        try:
            baseline_r = float(rmssd_vals[0])
            peak_r = max(float(v) for v in rmssd_vals)
            if baseline_r > 0:
                pct = (peak_r - baseline_r) / baseline_r * 100
                vagal_grade = (
                    'EXCELLENT (누적형 +{:.0f}%)'.format(pct) if pct >= 80 else
                    'GOOD (+{:.0f}%)'.format(pct) if pct >= 40 else
                    'MODERATE (+{:.0f}%)'.format(pct) if pct >= 15 else
                    'LOW (+{:.0f}%)'.format(pct)
                )
        except (TypeError, ValueError):
            pass

    # HRV 단계별 상세 표
    hrv_table_rows = []
    for pname, d in phases:
        if not d:
            hrv_table_rows.append(f"  {pname}: 데이터 없음")
            continue
        hrv_table_rows.append(
            f"  [{pname}]\n"
            f"    시간영역: SDNN={_fmt(d['sdnn'],'ms')} | RMSSD={_fmt(d['rmssd'],'ms')} | "
            f"SDSD={_fmt(d['sdsd'],'ms')} | NN50={_fmt(d['nn50'],'')} | pNN50={_fmt(d['pnn50'],'%')} | "
            f"TRI={_fmt(d['tri_index'])}\n"
            f"    주파수영역: VLF={_fmt(d['vlf'],'%')} | LF={_fmt(d['lf'],'%')} | "
            f"HF={_fmt(d['hf'],'%')} | LF/HF={_fmt(d['lh_ratio'])} | "
            f"Norm-LF={_fmt(d['norm_lf'],'%')} | Norm-HF={_fmt(d['norm_hf'],'%')}"
        )

    # ── 수면 데이터 ───────────────────────────────────────────────
    rpt = exp.report
    sleep_rows = []
    sleep_stage_rows = []
    sleep_eff_grade = 'N/A'
    if rpt:
        sleep_eff = rpt.sleep_eff
        if sleep_eff is not None:
            try:
                se = float(sleep_eff)
                sleep_eff_grade = 'EXCELLENT (≥90%)' if se >= 90 else \
                    'GOOD (85–89%)' if se >= 85 else \
                    'FAIR (75–84%)' if se >= 75 else 'LOW (<75%)'
            except (TypeError, ValueError):
                pass

        sleep_rows = [
            f"  TIB={_fmt(rpt.tib,'min')} | TST={_fmt(rpt.tst,'min')} | TWT={_fmt(rpt.twt,'min')}",
            f"  WASO={_fmt(rpt.waso,'min')} | 수면잠복기={_fmt(rpt.sleep_latency,'min')} | "
            f"REM잠복기={_fmt(rpt.rem_latency,'min')} | 수면효율={_fmt(rpt.sleep_eff,'%')}",
        ]
        sleep_stage_rows = [
            f"  N1={_fmt(rpt.sleep_n1_tst,'%')} ({_fmt(rpt.sleep_n1_min,'min')}) | "
            f"N2={_fmt(rpt.sleep_n2_tst,'%')} ({_fmt(rpt.sleep_n2_min,'min')}) | "
            f"N3={_fmt(rpt.sleep_n3_tst,'%')} ({_fmt(rpt.sleep_n3_min,'min')}) | "
            f"NREM합={_fmt(rpt.sleep_nrem_tst,'%')} | REM={_fmt(rpt.sleep_rem_tst,'%')} ({_fmt(rpt.sleep_rem_min,'min')})",
        ]
    else:
        sleep_rows = ['  수면 데이터 없음']

    # ── EEG 데이터 ────────────────────────────────────────────────
    eeg = exp.eeg
    eeg_psd_text = '  EEG 데이터 없음'
    eeg_staging_text = '  수면 단계 데이터 없음'
    if eeg:
        psd_summary = _eeg_psd_band_summary(eeg.psd) if eeg.psd else None
        if psd_summary:
            eeg_psd_text = '  ' + ' | '.join(
                f"{b.capitalize()}={psd_summary[b]}%" for b in ['delta','theta','alpha','sigma','beta','gamma'] if b in psd_summary
            )
        else:
            eeg_psd_text = '  PSD JSON 데이터 파싱 불가 (토포맵 이미지 기반 데이터)'

        stage_dist = _sleep_stage_distribution_from_staging(eeg.sleep_staging) if eeg.sleep_staging else None
        if stage_dist:
            eeg_staging_text = (
                f"  W={stage_dist['W']}% | N1={stage_dist['N1']}% | "
                f"N2={stage_dist['N2']}% | N3={stage_dist['N3']}% | REM={stage_dist['REM']}%"
            )

    # ── 프롬프트 조합 (기존 텍스트 방식, 내부 참조용) ──────────────
    prompt = f"""당신은 수면·자율신경계·뇌 네트워크 분야의 전문 임상 신경생리학 AI입니다.
아래 피험자 데이터를 바탕으로 NeuroTx Clinical Report 스타일의 **한국어** 정밀 임상 보고서를 작성하세요.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[피험자 정보]
  이름: {exp.name} | 나이: {exp.age if exp.age else 'N/A'}세 | 성별: {sex_str}
  생년월일: {exp.birth if exp.birth else 'N/A'} | 측정일시: {mdate}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[HRV 단계별 상세 수치]
{chr(10).join(hrv_table_rows) if hrv_table_rows else '  HRV 데이터 없음'}

[RMSSD 단계별 추이 (자율신경 반응 핵심 지표)]
{chr(10).join(rmssd_rows)}
  → Vagal Responsiveness 등급: {vagal_grade}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[수면 구조 데이터]
{chr(10).join(sleep_rows)}
  수면 효율 등급: {sleep_eff_grade}

[수면 단계 분포]
{chr(10).join(sleep_stage_rows) if sleep_stage_rows else '  없음'}

[EEG 수면 단계 분포 (epoch 기반, sleep_staging JSON)]
{eeg_staging_text}

[EEG 주파수 대역 상대 파워 (PSD)]
{eeg_psd_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 보고서 작성 지침

아래 7개 섹션을 **반드시 순서대로** 모두 작성하세요.

---

### EXECUTIVE SUMMARY — 임상 통합 요약
3개 축(① 자율신경 반응성, ② 수면 구조, ③ 기질적 프로파일)의 1~2문장 요약 후,
**SO WHAT — 임상적 시사점** 단락(3~4문장)으로 이 환자의 전체 임상 의미를 종합하세요.

---

### Section 01 | 자율신경계 기저선 분석 (Autonomic Baseline Assessment)
- Baseline 단계 HRV 수치 해석 (교감/부교감 균형 상태)
- SDNN·RMSSD·LF/HF 비를 중심으로 자율신경 상태 등급 판정
- 임상 Exhibit 표(단계 / 수치 / 해석) 포함
- **Key takeaway** 포함

---

### Section 02 | 단계별 자율신경 반응 매핑 (Phase-by-Phase ANS Response)
- Baseline → Stim1 → Rec1 → Stim2 → Rec2 순서로 RMSSD 변화 분석
- ▲/▼/─ 기호 사용, 반응 패턴 분류 (단회 반응형 / 누적형 / 지연형 / 비반응형)
- 임상 Exhibit 표(단계 / RMSSD / 변화 / 자율신경 상태) 포함
- **Key takeaway** 포함

---

### Section 03 | 수면 구조 및 EEG 스펙트럼 분석 (Sleep Architecture & EEG Spectrum)
- 수면 단계 분포(N1/N2/N3/REM) 정상 범위 대비 해석
- 수면 효율·잠복기·WASO 임상 의미
- EEG PSD 데이터(있는 경우) 주파수 대역별 해석
- 임상 Exhibit 표(단계 / 비율 / 정상범위 / 해석) 포함
- **Key takeaway** 포함

---

### Section 04 | 다축 통합 분석 (Multi-modal Integration)
- HRV × 수면 구조 × EEG 스펙트럼의 동기화 여부 평가
- "Golden Window" 해당 여부 — Rec2 단계 다축 동기화 평가
- 5축 통합 판정 Exhibit 표 포함 (Spectral / Sleep / Vagal / FC 추정 / 종합)
- **Key takeaway** 포함

---

### Section 05 | 치료 권고사항 및 예후 (Therapeutic Recommendations & Prognosis)
- 최적 자극 타이밍 (Chronotherapy 권고)
- 보조 요법 (수면 위생 / 호흡 동조 / 운동 등)
- 단기·중기·장기 예후 종합 평가 Exhibit 표
- **Key takeaway** 포함

---

### CONCLUDING REMARKS — 임상 종합 결론
5개 축에서 일관된 결론을 3~5문장으로 종합하고,
마지막 문장은 해당 환자의 치료 방향을 압축한 **핵심 임상 메시지**로 마무리하세요.

---

## 추가 작성 규칙
- 모든 수치는 임상적 맥락 안에서 해석 (단순 나열 금지)
- 데이터가 없는 항목은 "데이터 없음 — 추가 측정 권고"로 명시
- 한국어 본문 + 핵심 임상 용어는 영어 병기
- Exhibit 표는 마크다운 표 형식(| 컬럼 | ... |) 사용
- 각 섹션은 명확한 헤더(###)로 구분
"""
    return prompt


def _collect_report_data(exp) -> dict:
    """Experiment 객체에서 PDF 생성에 필요한 전체 데이터를 수집."""
    sex_str = '남성(M)' if str(exp.sex) == '0' else ('여성(F)' if str(exp.sex) == '1' else 'N/A')
    mdate = exp.measurement_date.strftime('%Y-%m-%d') if exp.measurement_date else 'N/A'

    # 환자 정보
    patient = {
        'name': exp.name,
        'age': exp.age,
        'sex': sex_str,
        'birth': str(exp.birth) if exp.birth else 'N/A',
        'measurement_date': mdate,
    }

    # HRV 단계별
    hrv = exp.hrv
    phase_defs = [
        ('Baseline',     '기저선',   hrv.baseline     if hrv else None),
        ('Stimulation1', '자극1',    hrv.stimulation1 if hrv else None),
        ('Recovery1',    '회복1',    hrv.recovery1    if hrv else None),
        ('Stimulation2', '자극2',    hrv.stimulation2 if hrv else None),
        ('Recovery2',    '회복2',    hrv.recovery2    if hrv else None),
    ]
    hrv_phases = []
    for name_en, name_ko, obj in phase_defs:
        d = _hrv_phase_summary(obj)
        if d:
            hrv_phases.append({'name': name_en, 'name_ko': name_ko, **d})
        else:
            hrv_phases.append({'name': name_en, 'name_ko': name_ko})

    # 수면 데이터
    rpt = exp.report
    sleep = {}
    if rpt:
        sleep = {
            'tib': rpt.tib, 'tst': rpt.tst, 'twt': rpt.twt, 'waso': rpt.waso,
            'sleep_latency': rpt.sleep_latency, 'rem_latency': rpt.rem_latency,
            'sleep_eff': rpt.sleep_eff,
            'n1_min': rpt.sleep_n1_min, 'n2_min': rpt.sleep_n2_min,
            'n3_min': rpt.sleep_n3_min, 'nrem_min': rpt.sleep_nrem_min,
            'rem_min': rpt.sleep_rem_min,
            'n1_pct': rpt.sleep_n1_tst, 'n2_pct': rpt.sleep_n2_tst,
            'n3_pct': rpt.sleep_n3_tst, 'nrem_pct': rpt.sleep_nrem_tst,
            'rem_pct': rpt.sleep_rem_tst,
        }

    # EEG 데이터
    eeg_data = {'has_data': False}
    eeg = exp.eeg
    if eeg:
        eeg_data['has_data'] = True
        staging = _sleep_stage_distribution_from_staging(eeg.sleep_staging)
        if staging:
            eeg_data['staging_dist'] = staging
        psd = _eeg_psd_band_summary(eeg.psd)
        if psd:
            eeg_data['psd_bands'] = psd

    # HRV 이미지 (단계별 heart_rate)
    hrv_images = []
    if exp.hrv:
        for name_en, name_ko, obj in phase_defs:
            if obj:
                hrv_images.append({
                    'name': name_en,
                    'name_ko': name_ko,
                    'heart_rate': _img_url(obj.heart_rate),
                    'comparison': _img_url(obj.comparison),
                })

    # EEG 이미지 (Baseline 기준 — topography / COH / PLV)
    eeg_images = {}
    if exp.eeg and exp.eeg.baseline:
        b = exp.eeg.baseline
        bands = ['delta', 'theta', 'alpha', 'beta', 'gamma']
        eeg_images['topography'] = {
            band: _img_url(getattr(b, f'topography_{band}', None)) for band in bands
        }
        eeg_images['connectivity_coh'] = {
            band: _img_url(getattr(b, f'connectivity_{band}', None)) for band in bands
        }
        eeg_images['connectivity_plv'] = {
            band: _img_url(getattr(b, f'connectivity2_{band}', None)) for band in bands
        }

    return {
        'patient': patient, 'hrv_phases': hrv_phases, 'sleep': sleep, 'eeg': eeg_data,
        'hrv_images': hrv_images, 'eeg_images': eeg_images,
    }


def _build_ai_prompt_json(data: dict) -> str:
    """Claude에게 구조화된 JSON 리포트 반환을 요청하는 프롬프트."""
    import json as _json

    patient = data['patient']
    hrv_phases = data['hrv_phases']
    sleep = data['sleep']
    eeg = data['eeg']

    # HRV 요약 텍스트
    hrv_lines = []
    prev_rmssd = None
    for p in hrv_phases:
        rmssd = p.get('rmssd')
        sym = _rmssd_change_symbol(prev_rmssd, rmssd)
        hrv_lines.append(
            f"  {p['name']} ({p['name_ko']}): "
            f"SDNN={_fmt(p.get('sdnn'),'ms')}, RMSSD={_fmt(rmssd,'ms')} {sym}, "
            f"pNN50={_fmt(p.get('pnn50'),'%')}, LF/HF={_fmt(p.get('lh_ratio'))}, "
            f"VLF={_fmt(p.get('vlf'),'%')}, LF={_fmt(p.get('lf'),'%')}, HF={_fmt(p.get('hf'),'%')}"
        )
        prev_rmssd = rmssd

    sleep_lines = []
    if sleep:
        sleep_lines = [
            f"  TIB={_fmt(sleep.get('tib'),'min')}, TST={_fmt(sleep.get('tst'),'min')}, "
            f"WASO={_fmt(sleep.get('waso'),'min')}, 수면효율={_fmt(sleep.get('sleep_eff'),'%')}",
            f"  수면잠복기={_fmt(sleep.get('sleep_latency'),'min')}, REM잠복기={_fmt(sleep.get('rem_latency'),'min')}",
            f"  N1={_fmt(sleep.get('n1_pct'),'%')}({_fmt(sleep.get('n1_min'),'min')}), "
            f"N2={_fmt(sleep.get('n2_pct'),'%')}({_fmt(sleep.get('n2_min'),'min')}), "
            f"N3={_fmt(sleep.get('n3_pct'),'%')}({_fmt(sleep.get('n3_min'),'min')}), "
            f"REM={_fmt(sleep.get('rem_pct'),'%')}({_fmt(sleep.get('rem_min'),'min')})",
        ]

    eeg_lines = []
    if eeg.get('staging_dist'):
        sd = eeg['staging_dist']
        eeg_lines.append(
            f"  수면 단계 분포(epoch): W={sd.get('W')}%, N1={sd.get('N1')}%, "
            f"N2={sd.get('N2')}%, N3={sd.get('N3')}%, REM={sd.get('REM')}%"
        )
    if eeg.get('psd_bands'):
        pb = eeg['psd_bands']
        eeg_lines.append(
            f"  EEG PSD: " +
            ', '.join(f"{k.capitalize()}={v}%" for k, v in pb.items())
        )

    prompt = f"""당신은 수면·자율신경계 분야의 전문 임상 신경생리학 AI입니다.
아래 피험자 데이터를 분석하여 NeuroTx Clinical Report 형식의 한국어 임상 보고서를 작성하고,
반드시 아래 JSON 형식으로만 응답하세요. JSON 외 다른 텍스트(코드블록 포함)는 절대 출력하지 마세요.
※ 각 필드의 글자 수 제한을 반드시 준수하세요. 전체 응답이 JSON으로 완결되어야 합니다.

=== 피험자 데이터 ===

[환자 정보]
  이름: {patient['name']} | 나이: {patient['age']}세 | 성별: {patient['sex']}
  생년월일: {patient['birth']} | 측정일: {patient['measurement_date']}

[HRV 단계별 데이터 (▲상승/▼하강/─불변)]
{chr(10).join(hrv_lines) if hrv_lines else '  데이터 없음'}

[수면 구조 데이터]
{chr(10).join(sleep_lines) if sleep_lines else '  데이터 없음'}

[EEG 데이터]
{chr(10).join(eeg_lines) if eeg_lines else '  데이터 없음'}

=== 요청 JSON 구조 ===

{{
  "executive_summary": "3개 축(자율신경 반응성·수면 구조·기질적 프로파일) 종합 요약 (2~3문장, 200자 이내)",
  "so_what": "임상적 시사점 — 신경생리학적 의미와 치료 전망 (2~3문장, 200자 이내)",
  "sections": [
    {{
      "number": "01",
      "title_ko": "자율신경계 기저선 분석",
      "title_en": "Autonomic Baseline Assessment",
      "content": "Baseline HRV 수치 해석, 교감/부교감 균형, SDNN·RMSSD·LF/HF 분석 (200~250자)",
      "key_takeaway": "핵심 결론 1문장 (80자 이내)"
    }},
    {{
      "number": "02",
      "title_ko": "단계별 자율신경 반응 매핑",
      "title_en": "Phase-by-Phase ANS Response Mapping",
      "content": "RMSSD 변화 추이(▲▼─), 반응 패턴 분류, Vagal Responsiveness 등급 (200~250자)",
      "key_takeaway": "핵심 결론 1문장 (80자 이내)"
    }},
    {{
      "number": "03",
      "title_ko": "수면 구조 분석",
      "title_en": "Sleep Architecture Assessment",
      "content": "수면 단계 분포 정상 범위 대비 해석, 수면효율·잠복기·WASO 의미, EEG PSD 해석 (200~250자)",
      "key_takeaway": "핵심 결론 1문장 (80자 이내)"
    }},
    {{
      "number": "04",
      "title_ko": "다축 통합 분석",
      "title_en": "Multi-modal Integration Analysis",
      "content": "HRV × 수면 × EEG 다축 동기화 평가, Golden Window 판단, 치료 효과 예측 (200~250자)",
      "key_takeaway": "핵심 결론 1문장 (80자 이내)"
    }},
    {{
      "number": "05",
      "title_ko": "치료 권고사항 및 예후",
      "title_en": "Therapeutic Recommendations & Prognosis",
      "content": "최적 자극 타이밍, 프로토콜 권고, 보조 요법, 단기·중기 예후 (200~250자)",
      "key_takeaway": "핵심 결론 1문장 (80자 이내)"
    }}
  ],
  "concluding_remarks": "종합 결론, 치료 방향 핵심 메시지 (3~4문장, 300자 이내)"
}}
"""
    return prompt


class AIReportView(APIView):
    """AI 임상 리포트 생성 — DB 저장 없이 구조화 JSON 반환 (PDF는 클라이언트에서 생성)."""

    def post(self, request, pk):
        try:
            exp = Experiments.objects.select_related(
                'hrv__baseline', 'hrv__stimulation1', 'hrv__recovery1',
                'hrv__stimulation2', 'hrv__recovery2',
                'eeg__baseline', 'eeg__stimulation1', 'eeg__recovery1',
                'eeg__stimulation2', 'eeg__recovery2',
                'report'
            ).get(pk=pk)
        except Experiments.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            return Response(
                {'error': 'ANTHROPIC_API_KEY가 설정되어 있지 않습니다.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        report_data = _collect_report_data(exp)
        prompt = _build_ai_prompt_json(report_data)

        try:
            client = anthropic.Anthropic(api_key=api_key)
            message = client.messages.create(
                model='claude-opus-4-6',
                max_tokens=16000,
                messages=[{'role': 'user', 'content': prompt}]
            )
            raw = message.content[0].text.strip()
            # 혹시 ```json ... ``` 블록이 있으면 제거
            if raw.startswith('```'):
                raw = raw.split('```')[1]
                if raw.startswith('json'):
                    raw = raw[4:]
            ai_json = json.loads(raw)
        except json.JSONDecodeError as e:
            return Response(
                {'error': f'AI 응답 파싱 실패: {str(e)}', 'raw': raw[:500]},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        except Exception as e:
            return Response(
                {'error': f'AI 리포트 생성 실패: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response({**report_data, 'ai': ai_json}, status=status.HTTP_200_OK)