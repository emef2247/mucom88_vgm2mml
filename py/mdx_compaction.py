"""Target-only compaction of the MDX subset emitted by opm_mdx_structure.

Source units and source-loop plans are untouched. Raw writes are never removed.
Setter reuse is conservative at raw tone writes and every source-loop entry.
Duration loops retain the exact encoded rest/note chunk sequence, including
the trailing '&' inside each tied-note loop body (MDX compiler lookahead).
Short relative setters retain known target values and absolute loop entries.
"""
import re

# External MDX MML compiler limit, not a limit on source-loop discovery.
MDX_REPEAT_DEPTH = 64


def tokens(text):
    return re.findall(r'\[|\]\d+|[^\s\[\]]+', text)


def compact(text, track, *, durations=True):
    """Return compact target text and an inspectable decision log.

    Token positions refer to the uncompacted track body, not source events.
    The original target units retain source event/Segment membership.
    """
    source = tokens(text)
    decisions = []
    # These are target interpreter/compiler settings, not native chip state.
    state = {}
    retained = []
    for index, token in enumerate(source):
        if token in ('[', 'L'):
            # The first pass and every back edge must execute the body's first
            # setter. An incoming value alone cannot justify deleting it.
            state = {}
        match = re.fullmatch(r'(@v|@|v|p|q|D|o)(-?\d+)', token)
        if match:
            kind, value = match.groups()
            value = int(value)
            if state.get(kind) == value:
                decisions.append(dict(track=track, token_index=index, action='omit_setter',
                                      before=token, after='', reason='unchanged target setting',
                                      bytes_saved=0 if kind == 'o' else 3 if kind == 'D' else 2))
                continue
            previous = state.get(kind)
            if kind in ('o', 'v', '@v') and previous is not None:
                delta = value - previous
                relative = ('>' if delta > 0 else '<') if kind == 'o' else (')' if delta > 0 else '(')
                relative *= abs(delta)
                if 1 <= abs(delta) <= 2 and len(relative) <= len(token):
                    decisions.append(dict(track=track, token_index=index, action='relative_setter',
                                          before=token, after=relative,
                                          reason=f'{kind} changes by {delta} from known target value {previous}',
                                          bytes_saved=0 if kind == 'o' else 2 - abs(delta)))
                    token = relative
            state[kind] = value
            if kind in ('v', '@v'):
                # Coarse v and fine @v have different one-step scales.
                state.pop('@v' if kind == 'v' else 'v', None)
            if kind == '@':
                # Voice loading refreshes tone/TL/pan at the next note.
                # Keep the following volume/pan setter explicitly on a reload.
                state.pop('@v', None)
                state.pop('v', None)
                state.pop('p', None)
        elif re.fullmatch(r'[<>]+', token):
            # Accept our own output conservatively on a second compaction.
            if 'o' in state:
                for symbol in token:
                    state['o'] = max(0, min(255, state['o'] + (1 if symbol == '>' else -1)))
        elif re.fullmatch(r'[()]+', token):
            kind = '@v' if '@v' in state else 'v' if 'v' in state else None
            if kind is not None:
                for symbol in token:
                    state[kind] = max(0, min(127 if kind == '@v' else 15,
                                            state[kind] + (1 if symbol == ')' else -1)))
        elif token.startswith('y'):
            register, _ = map(int, token[1:].split(','))
            # Raw writes bypass the MDX interpreter's cached voice/pan/volume.
            # Reloading the same voice may be necessary to restore operators.
            if 0x40 <= register <= 0xff or 0x20 <= register <= 0x27:
                for kind in ('@', '@v', 'v', 'p'):
                    state.pop(kind, None)
        retained.append((index, token))

    if not durations:
        return ' '.join(t for _, t in retained), decisions
    output = []
    index = depth = 0
    while index < len(retained):
        position, token = retained[index]
        if token == '[':
            depth += 1
        elif token.startswith(']'):
            depth -= 1
        if re.fullmatch(r'r%\d+', token):
            duration = int(token[2:])
            consumed = 1
            if token == 'r%128':
                while index + consumed < len(retained) and retained[index + consumed][1] == token:
                    consumed += 1
                duration *= consumed
            blocks, tail = divmod(duration, 128)
            # MDX's rest encoder emits one byte per 128 ticks. A finite loop
            # costs six bytes, so at least eight equal chunks yield a saving.
            if blocks >= 8 and depth < MDX_REPEAT_DEPTH:
                parts, saved = [], 0
                while blocks >= 8:
                    count = min(blocks, 255)
                    parts.append(f'[r%128]{count}')
                    saved += count - 7
                    blocks -= count
                remaining = blocks * 128 + tail
                if remaining:
                    if remaining <= 256:
                        parts.append(f'r%{remaining}')
                    else:
                        parts.extend(['r%128'] * blocks)
                        if tail:
                            parts.append(f'r%{tail}')
                replacement = ' '.join(parts)
                output.append(replacement)
                decisions.append(dict(track=track, token_index=position, action='rest_chunks',
                                      before=' '.join(t for _, t in retained[index:index+consumed]), after=replacement,
                                      reason='same ordered 128-tick rest chunks', bytes_saved=saved))
                index += consumed
                continue
        if re.fullmatch(r'(?:[a-g][+#-]?|n\d+,)(?:%256|1)', token):
            stop = index
            while (stop + 1 < len(retained) and retained[stop][1] == token
                   and retained[stop+1][1] == '&'):
                stop += 2
            count = (stop-index)//2
            if count >= 4 and depth < MDX_REPEAT_DEPTH:
                repeat = min(count, 255)
                replacement = f'[{token} &]{repeat}'
                output.append(replacement)
                decisions.append(dict(track=track, token_index=position, action='tied_chunks',
                                      before=' '.join(t for _, t in retained[index:index+2*repeat]),
                                      after=replacement, reason='identical note-and-tie pairs',
                                      bytes_saved=3*repeat-9))
                index += 2*repeat
                continue
        output.append(token)
        index += 1
    return ' '.join(output), decisions
