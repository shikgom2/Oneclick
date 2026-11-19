# -*- coding:utf-8 -*-
from django.db import models
from backend.model import TimeStampedModel


class HRV(TimeStampedModel):
    nni = models.JSONField(
        null=True,
        db_column='HRV_NNI'
    )
    rmssd = models.JSONField(
        null=True,
        db_column='HRV_RMSSD'
    )
    baseline = models.ForeignKey(
        'ecg.HRVBaseline',
        null=True, on_delete=models.CASCADE,
        related_name='hrv', db_column='HRV_BASELINE_ID'
    )
    stimulation1 = models.ForeignKey(
        'ecg.HRVStimulation1',
        null=True, on_delete=models.CASCADE,
        related_name='hrv', db_column='HRV_STIMULATION_1_ID'
    )
    recovery1 = models.ForeignKey(
        'ecg.HRVRecovery1',
        null=True, on_delete=models.CASCADE,
        related_name='hrv', db_column='HRV_RECOVERY_1_ID'
    )
    stimulation2 = models.ForeignKey(
        'ecg.HRVStimulation2',
        null=True, on_delete=models.CASCADE,
        related_name='hrv', db_column='HRV_STIMULATION_2_ID'
    )
    recovery2 = models.ForeignKey(
        'ecg.HRVRecovery2',
        null=True, on_delete=models.CASCADE,
        related_name='hrv', db_column='HRV_RECOVERY_2_ID'
    )
    # ── 6-phase 모드 (baseline, stim1~4, recovery) 에서 추가 ──
    # recovery1/recovery2 를 재사용하지 않고 새 컬럼을 쓰는 이유:
    # 키 하나가 모드와 무관하게 정확히 하나의 뜻(라벨·순서)을 갖게 하기 위해서다.
    # stimulation3·stimulation4 는 9-phase 모드도 같은 뜻으로 쓴다. recovery 는 6-phase 전용.
    stimulation3 = models.ForeignKey(
        'ecg.HRVStimulation3',
        null=True, on_delete=models.CASCADE,
        related_name='hrv', db_column='HRV_STIMULATION_3_ID'
    )
    stimulation4 = models.ForeignKey(
        'ecg.HRVStimulation4',
        null=True, on_delete=models.CASCADE,
        related_name='hrv', db_column='HRV_STIMULATION_4_ID'
    )
    recovery = models.ForeignKey(
        'ecg.HRVRecovery',
        null=True, on_delete=models.CASCADE,
        related_name='hrv', db_column='HRV_RECOVERY_ID'
    )
    # ── 9-phase 모드 (baseline, stim1, rec1, stim2, rec2, stim3, rec3, stim4, rec4) ──
    # 새 키는 recovery3·recovery4 둘뿐이다. 같은 원칙으로, 9-phase 의 마지막 구간
    # recovery4 와 6-phase 의 recovery 는 별개 키다 (같은 세션에 함께 나오지 않는다).
    recovery3 = models.ForeignKey(
        'ecg.HRVRecovery3',
        null=True, on_delete=models.CASCADE,
        related_name='hrv', db_column='HRV_RECOVERY_3_ID'
    )
    recovery4 = models.ForeignKey(
        'ecg.HRVRecovery4',
        null=True, on_delete=models.CASCADE,
        related_name='hrv', db_column='HRV_RECOVERY_4_ID'
    )
    note = models.TextField(
        null=True, blank=True,
        db_column='HRV_NOTE'
    )

    class Meta:
        db_table = 'OC_HRV'


class HRVParameter(TimeStampedModel):
    # >> 1. Time-Domain
    sdnn = models.FloatField(
        null=True, blank=True,
        db_column='HRV_SDNN'
    )
    rmssd = models.FloatField(
        null=True, blank=True,
        db_column='HRV_RMSSD'
    )
    sdsd = models.FloatField(
        null=True, blank=True,
        db_column='HRV_SDSD'
    )
    nn50 = models.FloatField(
        null=True, blank=True,
        db_column='HRV_NN50'
    )
    pnn50 = models.FloatField(
        null=True, blank=True,
        db_column='HRV_pNN50'
    )
    tri_index = models.FloatField(
        null=True, blank=True,
        db_column='HRV_TRI_Index'
    )

    # >> 2. Frequency-Domain
    vlf_rel_power = models.FloatField(
        null=True, blank=True,
        db_column='HRV_REL_VLF'
    )
    lf_rel_power = models.FloatField(
        null=True, blank=True,
        db_column='HRV_REL_LF'
    )
    hf_rel_power = models.FloatField(
        null=True, blank=True,
        db_column='HRV_REF_HF'
    )
    lh_ratio = models.FloatField(
        null=True, blank=True,
        db_column='HRV_LH_RATIO'
    )
    norm_lf = models.FloatField(
        null=True, blank=True,
        db_column='HRV_NORM_LF'
    )
    norm_hf = models.FloatField(
        null=True, blank=True,
        db_column='HRV_NORM_HF'
    )
    psd = models.JSONField(
        null=False,
        db_column='HRV_PSD'
    )
    heart_rate = models.ImageField(
        null=False, blank=False,
        db_column='HRV_HEAD_RATE'
    )
    comparison = models.ImageField(
        null=False, blank=False,
        db_column='HRV_COMPARISON'
    )

    class Meta:
        abstract = True


class HRVBaseline(HRVParameter):
    class Meta:
        db_table = 'OC_HRV_BASELINE'


class HRVStimulation1(HRVParameter):
    class Meta:
        db_table = 'OC_HRV_STIMULATION_1'


class HRVRecovery1(HRVParameter):
    class Meta:
        db_table = 'OC_HRV_RECOVERY_1'


class HRVStimulation2(HRVParameter):
    class Meta:
        db_table = 'OC_HRV_STIMULATION_2'


class HRVRecovery2(HRVParameter):
    class Meta:
        db_table = 'OC_HRV_RECOVERY_2'


class HRVStimulation3(HRVParameter):
    class Meta:
        db_table = 'OC_HRV_STIMULATION_3'


class HRVStimulation4(HRVParameter):
    class Meta:
        db_table = 'OC_HRV_STIMULATION_4'


class HRVRecovery(HRVParameter):
    """6-phase 모드의 마지막 회복 구간 ('회복')."""
    class Meta:
        db_table = 'OC_HRV_RECOVERY'


class HRVRecovery3(HRVParameter):
    """9-phase 모드의 3차 회복 구간 ('3차 회복')."""
    class Meta:
        db_table = 'OC_HRV_RECOVERY_3'


class HRVRecovery4(HRVParameter):
    """9-phase 모드의 4차 회복 구간 ('4차 회복'). 6-phase 의 HRVRecovery 와는 별개다."""
    class Meta:
        db_table = 'OC_HRV_RECOVERY_4'
