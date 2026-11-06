"""Output dialect and compiler capabilities, separate from source-chip mappings."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MucomProfile:
    name: str
    description: str
    compiler: str | None
    generation_only_reason: str = ''
    blueprint_dialect: str = 'mucom88'


PROFILES = (
    MucomProfile('mucom', 'MUCOM88 / mucomDotNET', 'mucom88'),
    MucomProfile('mucomsx', 'MUCOMSX', 'mucomsx'),
    MucomProfile('mucomsx-opn', 'MUCOMSX dialect under OPN hardware assumptions', None,
                 'OPN-board runtime validation is pending'),
    MucomProfile('md', 'Mega Drive MUC for mucomMD2vgm', None,
                 'No mucomMD2vgm compiler adapter is configured'),
)


def target_names():
    return tuple(profile.name for profile in PROFILES)


def get_target_profile(name):
    for profile in PROFILES:
        if profile.name == name:
            return profile
    raise ValueError('Unknown MUCOM profile: ' + str(name))


def profile_info(name):
    return asdict(get_target_profile(name))
