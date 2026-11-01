"""YM2203 musical gates: the shared FM gate builder with native part names.

MUCOM88 assigns the three OPN FM channels to parts A, B, C — the same
letters the chip's home driver has always used — so the OPNA builder
applies with a three-letter mapping; channels 3-5 simply never occur.
"""
from .opna_music import audit_fm_key_edges, build_fm_gates, dump_gates  # noqa: F401


def build_opn_gates(analysis):
    return build_fm_gates(analysis, parts='ABC')
