"""Lossless target-text macros; never cross synchronization comment boundaries."""
from collections import defaultdict
import re


def exact_macro_candidate(blocks, ref, definition, *, max_width=24,
                          max_body_size=160, min_width=1, measure=len,
                          blocked=lambda word: '*' in word):
    """MSX exact sequence search over adapter-supplied atomic strings.

    Blocks contain (grammar_key, words). Parsing and definition syntax belong
    to the caller. Greedy non-overlapping selection and first-best tie order
    intentionally match the inherited MGSDRV macro allocator.
    """
    candidates = defaultdict(list)
    for bi, (grammar, nodes) in enumerate(blocks):
        lengths = list(map(measure, nodes))
        excluded = [blocked(node) for node in nodes]
        for start in range(len(nodes)):
            if nodes[start] == '&' or excluded[start]:
                continue
            body_length = 0
            for width in range(1, min(max_width, len(nodes) - start) + 1):
                stop = start + width
                body_length += lengths[stop - 1] + (width > 1)
                if body_length > max_body_size or excluded[stop - 1]:
                    break
                if width < min_width or nodes[stop - 1] == '&':
                    continue
                if stop < len(nodes) and nodes[stop] == '&':
                    continue
                candidates[(grammar, tuple(nodes[start:stop]))].append((bi, start))
    best = None
    for (_, seq), positions in candidates.items():
        body = ' '.join(seq)
        per_use = measure(body) - measure(ref)
        cost = measure(definition(body) + '\n')
        if len(positions) < 2 or len(positions) * per_use - cost <= 0:
            continue
        selected, ends = [], {}
        for bi, start in positions:
            if start >= ends.get(bi, 0):
                selected.append((bi, start))
                ends[bi] = start + len(seq)
        saving = len(selected) * per_use - cost
        if len(selected) > 1 and saving > 0 and (best is None or saving > best[0]):
            best = saving, seq, selected
    return best


def compress_macros(text, limit=32):
    from mml_sync import _parse, _text

    # Avoid renumbering externally supplied definitions.
    if re.search(r'^\*\d+\s*=', text, re.M):
        return text
    parts = []
    rhythm = bool(re.search(r'^#opll_mode\s+1\b', text, re.M))
    for line in text.splitlines():
        match = re.fullmatch(r'([1-9a-h])\s+(.*)', line, re.I)
        if match:
            ch, body = match[1].lower(), match[2]
            if parts and isinstance(parts[-1], list) and parts[-1][0] == ch:
                parts[-1][1] += ' ' + body
            else:
                parts.append([ch, body])
        else:
            parts.append(line)
    blocks = [part for part in parts if isinstance(part, list)]
    for block in blocks:
        block[1] = [_text(node) for node in _parse(block[1], rhythm=rhythm and block[0] == 'f')]
    definitions = []
    for number in range(limit):
        ref = f'*{number}'
        best = exact_macro_candidate(
            [(rhythm and ch == 'f', nodes) for ch, nodes in blocks], ref,
            lambda body: f'{ref} = {{ {body} }}', max_width=24,
            max_body_size=160, min_width=4)
        if best is None:
            break
        _, seq, selected = best
        definitions.append(f'{ref} = {{ ' + ' '.join(seq) + ' }')
        for bi, start in reversed(selected):
            blocks[bi][1][start:start+len(seq)] = [ref]
    if not definitions:
        return text
    lines, inserted = [], False
    for part in parts:
        if isinstance(part, str):
            lines.append(part)
            continue
        if not inserted:
            lines.extend(definitions)
            inserted = True
        ch, nodes = part
        line = ch + ' '
        for word in ' '.join(nodes).split():
            if len(line) + len(word) + 1 > 120:
                lines.append(line.rstrip())
                line = ch + ' '
            line += word + ' '
        lines.append(line.rstrip())
    result = '\n'.join(lines) + '\n'
    return result if len(result) < len(text) else text
