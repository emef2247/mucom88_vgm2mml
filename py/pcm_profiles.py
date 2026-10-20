"""Explicit target PCM profiles; source IR and default conversion stay intact."""
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class PcmProfile:
    name: str
    rate: Fraction
    mdx_divider: int
    mdx_frequency: int
    chunk_seconds: int
    mdx_clock_hz: int = 8000000


PROFILES={
    'standard':PcmProfile('standard',Fraction(15625),512,4,8),
    'low':PcmProfile('low',Fraction(15625,2),1024,2,16),
    'ultra_low':PcmProfile('ultra_low',Fraction(15625,4),1024,0,32,4000000),
}


def get_profile(name='standard'):
    try:return PROFILES[name]
    except (KeyError,TypeError):raise ValueError('PCM profile must be standard, low or ultra_low') from None
