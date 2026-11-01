"""Source-native musical HAL; independent of MUCOM target constraints."""
from .native_source_music import build_native_performance
from .score_structure import build as build_score_guide
from .native_vgm import write_native_vgm
from .native_music import (NativeControl, NativeDevice, NativePacket,
    NativePerformance, NativePercussion, NativeUnit, NativeVoice)

__all__ = ['build_native_performance', 'build_score_guide', 'write_native_vgm',
    'NativeControl', 'NativeDevice', 'NativePacket', 'NativePerformance',
    'NativePercussion', 'NativeUnit', 'NativeVoice']
