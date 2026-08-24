# -*- coding:utf-8 -*-
from django.db import models
from backend.model import TimeStampedModel


class EEG(TimeStampedModel):
    psd = models.JSONField(
        null=False,
        db_column='EEG_PSD'
    )
    sleep_staging = models.JSONField(
        null=False,
        db_column='EEG_SLEEP_STAGING'
    )
    frontal_limbic = models.ForeignKey(
        'eeg.EEGFrontalLimbic',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_FRONTAL_LIMBIC_ID'
    )
    baseline = models.ForeignKey(
        'eeg.EEGBaseline',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_BASELINE_ID'
    )
    stimulation1 = models.ForeignKey(
        'eeg.EEGStimulation1',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_STIMULATION_1_ID'
    )
    recovery1 = models.ForeignKey(
        'eeg.EEGRecovery1',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_RECOVERY_1_ID'
    )
    stimulation2 = models.ForeignKey(
        'eeg.EEGStimulation2',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_STIMULATION_2_ID'
    )
    recovery2 = models.ForeignKey(
        'eeg.EEGRecovery2',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_RECOVERY_2_ID'
    )
    # ── 6-phase 모드 (baseline, stim1~4, recovery) 전용 (ecg.HRV 와 동일 원칙) ──
    stimulation3 = models.ForeignKey(
        'eeg.EEGStimulation3',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_STIMULATION_3_ID'
    )
    stimulation4 = models.ForeignKey(
        'eeg.EEGStimulation4',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_STIMULATION_4_ID'
    )
    recovery = models.ForeignKey(
        'eeg.EEGRecovery',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_RECOVERY_ID'
    )
    diff1 = models.ForeignKey(
        'eeg.EEGDiff1',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_DIFF_1_ID'
    )
    diff2 = models.ForeignKey(
        'eeg.EEGDiff2',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_DIFF_2_ID'
    )
    diff3 = models.ForeignKey(
        'eeg.EEGDiff3',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_DIFF_3_ID'
    )
    diff4 = models.ForeignKey(
        'eeg.EEGDiff4',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_DIFF_4_ID'
    )
    # 6-phase 는 인접 쌍이 5개(recovery-stim4 포함)라 diff5 가 필요하다.
    diff5 = models.ForeignKey(
        'eeg.EEGDiff5',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_DIFF_5_ID'
    )
    faa = models.ForeignKey(
        'eeg.EEGFAA',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_FAA_ID'
    )
    psd_spectrogram = models.ForeignKey(
        'eeg.EEGPSDSpectrogram',
        null=True, on_delete=models.CASCADE,
        related_name='eeg', db_column='EEG_PSD_SPECTOGRAM_ID'
    )
    

    spindle_coupling = models.JSONField(
        null=True, blank=True,
        db_column='EEG_SPINDLE_COUPLING'
    )

    note = models.TextField(
        null=True, blank=True,
        db_column='EEG_NOTE'
    )

    class Meta:
        db_table = 'OC_EEG'


class EEGFrontalLimbic(TimeStampedModel):
    delta = models.ImageField(
        null=False, blank=False,
        db_column='EEG_FRONTAL_LIMBIC_DELTA'
    )
    theta = models.ImageField(
        null=False, blank=False,
        db_column='EEG_FRONTAL_LIMBIC_THETA'
    )
    alpha = models.ImageField(
        null=False, blank=False,
        db_column='EEG_FRONTAL_LIMBIC_ALPHA'
    )
    sigma = models.ImageField(
        null=True, blank=True,
        db_column='EEG_FRONTAL_LIMBIC_SIGMA'
    )
    beta = models.ImageField(
        null=False, blank=False,
        db_column='EEG_FRONTAL_LIMBIC_BETA'
    )
    gamma = models.ImageField(
        null=False, blank=False,
        db_column='EEG_FRONTAL_LIMBIC_GAMMA'
    )

class EEGPSDSpectrogram(TimeStampedModel):
    cz  = models.ImageField(null=True, blank=True, db_column='EEG_PSD_CZ')
    c3  = models.ImageField(null=True, blank=True, db_column='EEG_PSD_C3')
    c4  = models.ImageField(null=True, blank=True, db_column='EEG_PSD_C4')
    fp1 = models.ImageField(null=True, blank=True, db_column='EEG_PSD_FP1')
    fp2 = models.ImageField(null=True, blank=True, db_column='EEG_PSD_FP2')
    f3  = models.ImageField(null=True, blank=True, db_column='EEG_PSD_F3')
    f4  = models.ImageField(null=True, blank=True, db_column='EEG_PSD_F4')
    f7  = models.ImageField(null=True, blank=True, db_column='EEG_PSD_F7')
    f8  = models.ImageField(null=True, blank=True, db_column='EEG_PSD_F8')
    t3  = models.ImageField(null=True, blank=True, db_column='EEG_PSD_T3')
    t4  = models.ImageField(null=True, blank=True, db_column='EEG_PSD_T4')
    p3  = models.ImageField(null=True, blank=True, db_column='EEG_PSD_P3')
    p4  = models.ImageField(null=True, blank=True, db_column='EEG_PSD_P4')

    class Meta:
        db_table = 'OC_EEG_PSD_SPECTOGRAM'


class EEGFAA(TimeStampedModel):
    faa_baseline = models.ImageField(
        null=False, blank=False,
        db_column='EEG_FRONTAL_ASYMMETRY_BASELINE'
    )
    faa_stimulation1 = models.ImageField(
        null=False, blank=False,
        db_column='EEG_FRONTAL_ASYMMETRY_STIMULATION1'
    )
    faa_recovery1 = models.ImageField(
        null=False, blank=False,
        db_column='EEG_FRONTAL_ASYMMETRY_RECOVERY1'
    )
    faa_stimulation2 = models.ImageField(
        null=False, blank=False,
        db_column='EEG_FRONTAL_ASYMMETRY_STIMULATION2'
    )
    faa_recovery2 = models.ImageField(
        null=False, blank=False,
        db_column='EEG_FRONTAL_ASYMMETRY_RECOVERY2'
    )
    # 6-phase 전용. 기존 행에는 값이 없으므로 null 허용이어야 한다
    # (기존 5개 컬럼의 null=False 는 이미 쌓인 데이터 때문에 그대로 둔다).
    faa_stimulation3 = models.ImageField(
        null=True, blank=True,
        db_column='EEG_FRONTAL_ASYMMETRY_STIMULATION3'
    )
    faa_stimulation4 = models.ImageField(
        null=True, blank=True,
        db_column='EEG_FRONTAL_ASYMMETRY_STIMULATION4'
    )
    faa_recovery = models.ImageField(
        null=True, blank=True,
        db_column='EEG_FRONTAL_ASYMMETRY_RECOVERY'
    )

    class Meta:
        db_table = 'OC_EEG_FAA'


class EEGParameter(TimeStampedModel):
    topography_delta = models.ImageField(
        null=False, blank=False,
        db_column='EEG_TOPOGRAPHY_DELTA'
    )
    topography_theta = models.ImageField(
        null=False, blank=False,
        db_column='EEG_TOPOGRAPHY_THETA'
    )
    topography_alpha = models.ImageField(
        null=False, blank=False,
        db_column='EEG_TOPOGRAPHY_ALPHA'
    )
    topography_sigma = models.ImageField(
        null=True, blank=True,
        db_column='EEG_TOPOGRAPHY_SIGMA'
    )
    topography_beta = models.ImageField(
        null=False, blank=False,
        db_column='EEG_TOPOGRAPHY_BETA'
    )
    topography_gamma = models.ImageField(
        null=False, blank=False,
        db_column='EEG_TOPOGRAPHY_GAMMA'
    )

    connectivity_delta = models.ImageField(
        null=False, blank=False,
        db_column='EEG_CONNECTIVITY_DELTA_COH'
    )
    connectivity_theta = models.ImageField(
        null=False, blank=False,
        db_column='EEG_CONNECTIVITY_THETA_COH'
    )
    connectivity_alpha = models.ImageField(
        null=False, blank=False,
        db_column='EEG_CONNECTIVITY_ALPHA_COH'
    )
    connectivity_sigma = models.ImageField(
        null=True, blank=True,
        db_column='EEG_CONNECTIVITY_SIGMA_COH'
    )
    connectivity_beta = models.ImageField(
        null=False, blank=False,
        db_column='EEG_CONNECTIVITY_BETA_COH'
    )
    connectivity_gamma = models.ImageField(
        null=False, blank=False,
        db_column='EEG_CONNECTIVITY_GAMMA_COH'
    )

    connectivity2_delta = models.ImageField(
        null=True, blank=True,
        db_column='EEG_CONNECTIVITY_DELTA_PLV'
    )
    connectivity2_theta = models.ImageField(
        null=True, blank=True,
        db_column='EEG_CONNECTIVITY_THETA_PLV'
    )
    connectivity2_alpha = models.ImageField(
        null=True, blank=True,
        db_column='EEG_CONNECTIVITY_ALPHA_PLV'
    )
    connectivity2_sigma = models.ImageField(
        null=True, blank=True,
        db_column='EEG_CONNECTIVITY_SIGMA_PLV'
    )
    connectivity2_beta = models.ImageField(
        null=True, blank=True,
        db_column='EEG_CONNECTIVITY_BETA_PLV'
    )
    connectivity2_gamma = models.ImageField(
        null=True, blank=True,
        db_column='EEG_CONNECTIVITY_GAMMA_PLV'
    )
    faa = models.ImageField(
        null=True, blank=True,
        db_column='EEG_FRONTAL_ALPHA_ASYMMETRY'
    )

    class Meta:
        abstract = True


class EEGBaseline(EEGParameter):
    class Meta:
        db_table = 'OC_EEG_BASELINE'


class EEGStimulation1(EEGParameter):
    class Meta:
        db_table = 'OC_EEG_STIMULATION_1'


class EEGRecovery1(EEGParameter):
    class Meta:
        db_table = 'OC_EEG_RECOVERY_1'


class EEGStimulation2(EEGParameter):
    class Meta:
        db_table = 'OC_EEG_STIMULATION_2'


class EEGRecovery2(EEGParameter):
    class Meta:
        db_table = 'OC_EEG_RECOVERY_2'


class EEGStimulation3(EEGParameter):
    class Meta:
        db_table = 'OC_EEG_STIMULATION_3'


class EEGStimulation4(EEGParameter):
    class Meta:
        db_table = 'OC_EEG_STIMULATION_4'


class EEGRecovery(EEGParameter):
    """6-phase 모드의 마지막 회복 구간 ('회복')."""
    class Meta:
        db_table = 'OC_EEG_RECOVERY'


class EEGDiffParameter(EEGParameter):
    # >> WAKE stage topography
    topography_delta_wake = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_DELTA_WAKE')
    topography_theta_wake = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_THETA_WAKE')
    topography_alpha_wake = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_ALPHA_WAKE')
    topography_sigma_wake = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_SIGMA_WAKE')
    topography_beta_wake  = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_BETA_WAKE')
    topography_gamma_wake = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_GAMMA_WAKE')

    # >> N1 stage topography
    topography_delta_n1 = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_DELTA_N1')
    topography_theta_n1 = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_THETA_N1')
    topography_alpha_n1 = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_ALPHA_N1')
    topography_sigma_n1 = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_SIGMA_N1')
    topography_beta_n1  = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_BETA_N1')
    topography_gamma_n1 = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_GAMMA_N1')

    # >> N2 stage topography
    topography_delta_n2 = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_DELTA_N2')
    topography_theta_n2 = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_THETA_N2')
    topography_alpha_n2 = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_ALPHA_N2')
    topography_sigma_n2 = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_SIGMA_N2')
    topography_beta_n2  = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_BETA_N2')
    topography_gamma_n2 = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_GAMMA_N2')

    # >> N3 stage topography
    topography_delta_n3 = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_DELTA_N3')
    topography_theta_n3 = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_THETA_N3')
    topography_alpha_n3 = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_ALPHA_N3')
    topography_sigma_n3 = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_SIGMA_N3')
    topography_beta_n3  = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_BETA_N3')
    topography_gamma_n3 = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_GAMMA_N3')

    # >> REM stage topography
    topography_delta_rem = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_DELTA_REM')
    topography_theta_rem = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_THETA_REM')
    topography_alpha_rem = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_ALPHA_REM')
    topography_sigma_rem = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_SIGMA_REM')
    topography_beta_rem  = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_BETA_REM')
    topography_gamma_rem = models.ImageField(null=True, blank=True, db_column='EEG_TOPOGRAPHY_GAMMA_REM')

    # >> WAKE stage connectivity
    connectivity_delta_wake = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_DELTA_WAKE')
    connectivity_theta_wake = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_THETA_WAKE')
    connectivity_alpha_wake = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_ALPHA_WAKE')
    connectivity_sigma_wake = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_SIGMA_WAKE')
    connectivity_beta_wake  = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_BETA_WAKE')
    connectivity_gamma_wake = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_GAMMA_WAKE')

    # >> N1 stage connectivity
    connectivity_delta_n1 = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_DELTA_N1')
    connectivity_theta_n1 = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_THETA_N1')
    connectivity_alpha_n1 = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_ALPHA_N1')
    connectivity_sigma_n1 = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_SIGMA_N1')
    connectivity_beta_n1  = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_BETA_N1')
    connectivity_gamma_n1 = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_GAMMA_N1')

    # >> N2 stage connectivity
    connectivity_delta_n2 = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_DELTA_N2')
    connectivity_theta_n2 = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_THETA_N2')
    connectivity_alpha_n2 = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_ALPHA_N2')
    connectivity_sigma_n2 = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_SIGMA_N2')
    connectivity_beta_n2  = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_BETA_N2')
    connectivity_gamma_n2 = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_GAMMA_N2')

    # >> N3 stage connectivity
    connectivity_delta_n3 = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_DELTA_N3')
    connectivity_theta_n3 = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_THETA_N3')
    connectivity_alpha_n3 = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_ALPHA_N3')
    connectivity_sigma_n3 = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_SIGMA_N3')
    connectivity_beta_n3  = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_BETA_N3')
    connectivity_gamma_n3 = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_GAMMA_N3')

    # >> REM stage connectivity
    connectivity_delta_rem = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_DELTA_REM')
    connectivity_theta_rem = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_THETA_REM')
    connectivity_alpha_rem = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_ALPHA_REM')
    connectivity_sigma_rem = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_SIGMA_REM')
    connectivity_beta_rem  = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_BETA_REM')
    connectivity_gamma_rem = models.ImageField(null=True, blank=True, db_column='EEG_CONNECTIVITY_GAMMA_REM')

    class Meta:
        abstract = True


class EEGDiff1(EEGDiffParameter):
    class Meta:
        db_table = 'OC_EEG_DIFF_1'


class EEGDiff2(EEGDiffParameter):
    class Meta:
        db_table = 'OC_EEG_DIFF_2'


class EEGDiff3(EEGDiffParameter):
    class Meta:
        db_table = 'OC_EEG_DIFF_3'


class EEGDiff4(EEGDiffParameter):
    class Meta:
        db_table = 'OC_EEG_DIFF_4'


class EEGDiff5(EEGDiffParameter):
    class Meta:
        db_table = 'OC_EEG_DIFF_5'
