"""Conservative notation compaction for native MUCOM88 track bodies.

The caller emits C128 before this body. Duration spelling changes only exact
integer counts <=111; C changes inside a body suspend normalization until C128.
Source musical units and control trajectories are never edited. Loop entries,
back edges and unknown side effects invalidate cached setter values. No voice,
volume, pan, hardware, key-delay or raw register command is removed.
"""
import re

from source_loop_plan import expanded_tokens

NATIVE_REPEAT_DEPTH = 16
NATIVE_REPEAT_COUNT = 255


def tokens(text):
    return re.findall(r'\[|\]\d*|[^\s\[\]]+', text)


def _length(count):
    """Return a divisor/dot spelling with exactly the original C128 count."""
    if not 1 <= count <= 111:
        return None
    candidates = []
    # Never depend on the compiler's integer-division rounding. A dotted value
    # must have an even base count, so its mathematical half is integral too.
    for divisor in range(1, 129):
        if 128 % divisor:
            continue
        base = 128 // divisor
        if base == count:
            candidates.append(str(divisor))
        if base % 2 == 0 and base + base // 2 == count:
            candidates.append(str(divisor) + '.')
    return min(candidates, key=lambda word: (len(word), '.' in word, word)) if candidates else None


def _chunk(stream, index):
    """Existing emitted rest or trailing-tied note, including its KD reset."""
    words = []
    if index < len(stream) and re.fullmatch(r'KD\d+,\d+,\d+,\d+', stream[index][1]):
        words.append(stream[index][1])
        index += 1
    if index >= len(stream):
        return None
    match = re.fullmatch(r'([a-gr][+-]?)%(\d+)', stream[index][1])
    if not match or not 1 <= int(match[2]) <= 111:
        return None
    words.append(stream[index][1])
    if match[1] == 'r':
        # KD attached to a rest is not part of our emitted duration grammar.
        return tuple(words) if len(words) == 1 else None
    if index + 1 < len(stream) and stream[index + 1][1] == '&':
        words.append('&')
        return tuple(words)
    return None


def _duration_loops(stream, part):
    output, decisions = [], []
    index = depth = 0
    while index < len(stream):
        position, word = stream[index]
        if word == '[':
            depth += 1
        elif word.startswith(']'):
            depth -= 1
        block = _chunk(stream, index) if depth < NATIVE_REPEAT_DEPTH else None
        count = 1
        if block:
            width = len(block)
            while count < NATIVE_REPEAT_COUNT and _chunk(stream, index + count * width) == block:
                count += 1
            if count > 1:
                end = index + count * width
                plain = ' '.join(word for _, word in stream[index:end])
                folded = '[' + ' '.join(block) + ']' + str(count)
                if len(folded) < len(plain):
                    replacement = [(position, '[')] + [(position, word) for word in block] + [(position, ']' + str(count))]
                    output.extend(replacement)
                    decisions.append(dict(track=part, token_index=position,
                                          action='duration_loop', before=plain, after=folded,
                                          reason='exact existing native duration chunks; ordered ties/KD retained',
                                          bytes_saved=0))
                    index = end
                    continue
        output.append((position, word))
        index += 1
    before = ' '.join(word for _, word in stream)
    after = ' '.join(word for _, word in output)
    if expanded_tokens(before) != expanded_tokens(after):
        raise AssertionError('Native duration loop changed ordered target tokens')
    return output, decisions

def compact(text, part, *, durations=True):
    """Return target text and decisions indexed in the original token stream.

    Octaves on PCM part K are deliberately untouched. Absolute D setters are
    reused only while no intervening unknown command could modify driver state.
    Byte savings are unclaimed: notation size is not encoded MUB data size.
    """
    state = {}
    retained, decisions = [], []
    for index, original in enumerate(tokens(text)):
        word = original
        match = re.fullmatch(r'(o|D)(-?\d+)', word)
        if match:
            kind, raw_value = match.groups()
            value = int(raw_value)
            eligible = kind == 'D' or (part != 'K' and 1 <= value <= 8)
            if eligible and state.get(kind) == value:
                decisions.append(dict(track=part, token_index=index,
                                      action='omit_setter', before=original, after='',
                                      reason='unchanged known absolute target setting', bytes_saved=0))
                continue
            if eligible:
                state[kind] = value
            else:
                state.clear()
        elif not (re.fullmatch(r'[a-gr](?:[+-])?(?:%\d+|\d+\.?)', word) or word == '&'):
            # Includes [, ]N, L, @voice, H, KD, raw y, relative octave,
            # volume and all commands not explicitly established harmless.
            state.clear()
        retained.append((index, word))
    if not durations:
        return ' '.join(word for _, word in retained), decisions
    retained, folds = _duration_loops(retained, part)
    decisions.extend(folds)
    clock = 128
    output = []
    for index, original in retained:
        word = original
        length = re.fullmatch(r'([a-gr](?:[+-])?)%(\d+)', word)
        if length and clock == 128:
            spelling = _length(int(length[2]))
            if spelling is not None:
                word = length[1] + spelling
                decisions.append(dict(track=part, token_index=index,
                                      action='duration_spelling', before=original, after=word,
                                      reason='exact C128 divisor/dot count', bytes_saved=0))
        change = re.fullmatch(r'C(\d+)', word)
        if change:
            clock = int(change[1])
        output.append(word)
    return ' '.join(output), decisions
