"""Stateful spelling of already interpreted MUCOMSX PSG musical units.

This pass precedes exact source-plan projection. It changes neither source
notes nor target event times. Envelope/noise actions remain ordered events.
"""
import hashlib
import re
from collections import Counter

from mucomsx_macros import _parse_muc

_NOTE = re.compile(r'([a-gr](?:[+-])?)(?:%(\d+)|(\d+)(\.*))?$')
_SETTER = re.compile(r'([PDoov])([+-]?\d+)$')


def _duration(token):
    match = _NOTE.fullmatch(token)
    if match is None:
        return None
    if match[2] is not None:
        return int(match[2])
    if match[3] is None:
        raise ValueError('Generated PSG notes require explicit lengths')
    divisor = int(match[3])
    value = 128 / divisor
    for i in range(len(match[4])):
        value += 128 / divisor / 2 ** (i + 1)
    if not value.is_integer():
        raise ValueError('PSG duration is not an integer target tick')
    return int(value)


def _short_duration(token):
    match = _NOTE.fullmatch(token)
    if match is None or match[2] is None:
        return token
    ticks = int(match[2])
    if ticks > 0 and 128 % ticks == 0:
        short = match[1] + str(128 // ticks)
        if len(short) < len(token):
            return short
    return token


def _events(tokens, state):
    """Effective compiler state at timed events plus ordered target actions.

    E, w, m and S are intentionally events, not disposable assignments. v
    changes volume mode, so a following S invalidates any known software v.
    This is a generated-subset proof, supplemented by native public export.
    """
    result = []
    for token in tokens:
        setter = _SETTER.fullmatch(token)
        if setter:
            state[setter[1].lower()] = int(setter[2])
            if setter[1] == 'v':
                state['volume_mode'] = 'software'
        elif token in ('>', '<'):
            if 'o' not in state:
                raise ValueError('Relative octave needs a known initial octave')
            state['o'] += 1 if token == '>' else -1
        elif token.startswith(('E', 'w', 'm', 'S', '@')):
            result.append(('action', token))
            if token.startswith('S'):
                state.pop('v', None)
                state['volume_mode'] = 'hardware'
        elif token == '&':
            result.append(('tie',))
        else:
            ticks = _duration(token)
            if ticks is None:
                raise ValueError('Unsupported generated PSG command: ' + token)
            result.append(('timed', _NOTE.fullmatch(token)[1], ticks,
                           tuple(sorted(state.items()))))
    return result


def compact_psg_commands(commands, *, hardware, loop_index=None):
    """Prune persistent setters before source loops, with fresh state at L.

    Every E, noise period, hardware-envelope setup and explicit attack/tie
    remains present. Hardware v controls are never removed. Relative octave
    and divisor spelling use the generated C128 clock only.
    """
    if len(commands) != len(hardware):
        raise ValueError('PSG command/hardware metadata length differs')
    known = {}; before_state = {}; after_state = {}
    output = []; audit = []; removed = Counter(); before_events = []; after_events = []
    for index, (command, hw) in enumerate(zip(commands, hardware)):
        if index == loop_index:
            known.clear(); before_state.clear(); after_state.clear()
        tokens = _parse_muc(command)
        if any(not isinstance(token, str) for token in tokens):
            raise ValueError('PSG state spelling must precede finite loops/macros')
        words = []; omitted = []
        for token in tokens:
            setter = _SETTER.fullmatch(token)
            if setter:
                key = setter[1].lower(); value = int(setter[2])
                can_prune = key != 'v' or not hw
                if can_prune and known.get(key) == value:
                    omitted.append(token); removed[key] += 1
                else:
                    word = token
                    if key == 'o' and key in known and abs(known[key] - value) == 1:
                        word = '>' if value > known[key] else '<'
                    words.append(word)
                known[key] = value
            else:
                words.append(_short_duration(token))
                if token.startswith('S'):
                    known.pop('v', None)
        text = ' '.join(words)
        original_events = _events(tokens, before_state)
        compact_events = _events(words, after_state)
        if original_events != compact_events:
            raise AssertionError('PSG state spelling changed timed state or ordered actions')
        before_events.extend(original_events); after_events.extend(compact_events)
        output.append(text)
        audit.append(dict(note_index=index, before=command, after=text,
                          removed=omitted, effective_event_identity=True,
                          loop_entry=index == loop_index))
    signature = repr(before_events).encode('utf-8')
    return output, dict(algorithm='stateful_musical_units_before_exact_structure',
        source_notes_unchanged=True, effective_event_identity=before_events == after_events,
        event_count=len(before_events), event_sha256=hashlib.sha256(signature).hexdigest(),
        removed_setters=dict(removed), original_characters=sum(map(len, commands)),
        selected_characters=sum(map(len, output)), units=audit,
        retained_actions='all E/w/m/S and ties; state forgotten at song-loop entry')
