# -*- coding:utf-8 -*-
from ecg.models import *
from rest_framework import serializers


class HRVBaseLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = HRVBaseline
        fields = '__all__'


class HRVStimulation1Serializer(serializers.ModelSerializer):
    class Meta:
        model = HRVStimulation1
        fields = '__all__'


class HRVRecovery1Serializer(serializers.ModelSerializer):
    class Meta:
        model = HRVRecovery1
        fields = '__all__'


class HRVStimulation2Serializer(serializers.ModelSerializer):
    class Meta:
        model = HRVStimulation2
        fields = '__all__'


class HRVRecovery2Serializer(serializers.ModelSerializer):
    class Meta:
        model = HRVRecovery2
        fields = '__all__'


class HRVStimulation3Serializer(serializers.ModelSerializer):
    class Meta:
        model = HRVStimulation3
        fields = '__all__'


class HRVStimulation4Serializer(serializers.ModelSerializer):
    class Meta:
        model = HRVStimulation4
        fields = '__all__'


class HRVRecoverySerializer(serializers.ModelSerializer):
    class Meta:
        model = HRVRecovery
        fields = '__all__'


class HRVRecovery3Serializer(serializers.ModelSerializer):
    class Meta:
        model = HRVRecovery3
        fields = '__all__'


class HRVRecovery4Serializer(serializers.ModelSerializer):
    class Meta:
        model = HRVRecovery4
        fields = '__all__'


class HRVSerializer(serializers.ModelSerializer):
    pk = serializers.IntegerField(read_only=True)
    baseline = HRVBaseLineSerializer(read_only=True)
    stimulation1 = HRVStimulation1Serializer(read_only=True)
    recovery1 = HRVRecovery1Serializer(read_only=True)
    stimulation2 = HRVStimulation2Serializer(read_only=True)
    recovery2 = HRVRecovery2Serializer(read_only=True)
    # phase 는 합집합의 시간 순서로 둔다 (experiments.views.CONNECTIVITY_PHASES 와 같은 순서).
    # 세션에 없는 phase 는 키를 빼지 않고 null 로 나간다.
    stimulation3 = HRVStimulation3Serializer(read_only=True)
    recovery3 = HRVRecovery3Serializer(read_only=True)
    stimulation4 = HRVStimulation4Serializer(read_only=True)
    recovery4 = HRVRecovery4Serializer(read_only=True)
    recovery = HRVRecoverySerializer(read_only=True)

    class Meta:
        model = HRV
        fields = (
            'pk',
            'nni',
            'rmssd',
            'baseline',
            'stimulation1',
            'recovery1',
            'stimulation2',
            'recovery2',
            'stimulation3',
            'recovery3',
            'stimulation4',
            'recovery4',
            'recovery',
            'note'
        )


class HRVNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = HRV
        fields = (
            'pk',
            'note'
        )
