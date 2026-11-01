"""YM2612 musical gates: the shared FM gate builder with MD part names.

mucomMD2vgm assigns the six FM channels to parts A-F, so the OPNA
builder is reused with that mapping; gate semantics are unchanged.
"""
from .opna_music import audit_fm_key_edges, build_fm_gates, dump_gates  # noqa: F401


def build_opn2_gates(analysis):
    return build_fm_gates(analysis, parts='ABCDEF')
