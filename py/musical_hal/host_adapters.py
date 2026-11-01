"""Explicit MUCOM host contracts for inherited PSG/SCC interpretation.

These imports resolve in this repository's ordinary ``py`` module directory.
The HAL never searches a sibling checkout or changes Python's import path.
"""


def settings(segment, chip):
    from psg_scc_performance import settings as source_settings
    return source_settings(segment, chip)


def legacy_performances(source, root, stem, performances, opll_segments, raw):
    observed = {event.command for event in source['events']}
    if not performances and observed & {0xa0, 0xd2}:
        from vgm_reader import parse_vgm
        from psg import build_segments as build_psg
        from scc import build_segments as build_scc
        from psg_scc_performance import analyze
        folder = root/'native_legacy'
        folder.mkdir(exist_ok=True)
        path = folder/(stem+'.source.vgm')
        path.write_bytes(raw)
        traces = parse_vgm(str(path), str(folder), include_vgmticks=True)
        performances = (
            analyze(build_psg(traces[2], folder, stem, dump_passes=True), 'psg'),
            analyze(build_scc(traces[3], folder, dump_passes=True, stem=stem).segments, 'scc'))
    return tuple(performances), opll_segments
