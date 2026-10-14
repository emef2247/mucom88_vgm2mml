# Authored FM clock listening

Native listening failure (2026-10-10): canonical CLOCK.mdx fails MMDSP load;
all four old CLK references are reported inaudible without animation. See
listening_results.json for host hashes. Do not use these as a native-passing
clock baseline. A separate FMSTATE-based diagnostic is generated with
tests/scripts/generate_fmstate_clock_controls.py into outputs/listen/clock_controls/;
its new native results remain unverified.

Original MIT public test: eight notes, volume/pan changes, explicit register Key-Off and final silence. Duration 8.388608 seconds. CLOCK.vgm is replayed from the native MXC 16384 us reference. CLOCK.reference.mml is that authored 16384 us source; converter MML may choose a different clock for the same physical performance. Musical register updates are sparse; wait-command sizes do not measure musical-event density. The clock variants are controlled listening references, not converter output.
