"""YM2612 musical gates: the shared FM gate builder with MD part names.

mucomMD2vgm assigns FM channels 1-6 to parts A, B, C, H, I, J — the
same letters MUCOM88 uses for OPNA FM — so the OPNA builder's default
mapping applies unchanged.
"""
from .opna_music import audit_fm_key_edges, build_fm_gates, dump_gates  # noqa: F401


def build_opn2_gates(analysis):
    return build_fm_gates(analysis, parts='ABCHIJ')
