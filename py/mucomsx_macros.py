"""Lossless MUCOMSX macros over already rendered, self-contained music units.

Balanced finite-loop trees and atomic-unit candidates share the inherited
MSX exact candidate allocator. MGSDRV parsing and macro syntax are not used.
"""
import hashlib
import re
from dataclasses import dataclass
from collections import defaultdict
from bisect import bisect_right

from mml_macros import exact_macro_candidate


MAX_DEFINITION_BYTES = 1023
MAX_CALL_DEPTH = 8
MAX_BODY_CHARACTERS = 512
ALLOCATION_PORTFOLIO = ((24, 0), (64, 0), (64, 1), (64, 2))


def _compact_units(tracks, *, limit=32, max_width=64, encoding='shift_jis'):
    """Return (physical definitions, compacted unit lists, inspectable report).

    Callers own musical rendering and finite repeat structure. Uppercase L
    marks a source song-loop boundary and is never extracted or crossed.
    Definitions contain no calls, so generated call depth is at most one.
    Expansion is checked against both original atomic units and encoded text.
    Byte sizes include spaces between units and newlines after definitions.
    """
    if limit < 0 or max_width < 1:
        raise ValueError('Invalid macro search bounds')
    original = {part: list(units) for part, units in tracks.items()}
    for units in original.values():
        for unit in units:
            if not isinstance(unit, str):
                raise TypeError('Rendered musical units must be strings')
            if any(char in unit for char in '\r\n{};'):
                raise ValueError('Musical units must contain no lines, braces or comments')
            if '*' in unit:
                raise ValueError('Pre-existing macro calls are not supported')
    result = {part: list(units) for part, units in original.items()}
    parts = list(result)
    definitions, bodies, audit = [], {}, []
    measure = lambda text: len(text.encode(encoding))
    for number in range(1, limit + 1):
        ref = f'*{number}'
        definition = lambda body: f'# {ref}{{{body}}}'
        available = MAX_DEFINITION_BYTES - measure(definition(''))
        best = exact_macro_candidate(
            [(None, result[part]) for part in parts], ref, definition,
            max_width=max_width, max_body_size=min(available, MAX_BODY_CHARACTERS), measure=measure,
            blocked=lambda unit: '*' in unit or '&' in unit or
            re.search(r'(?<![A-Za-z])L(?![A-Za-z])', unit) is not None)
        if best is None:
            break
        saving, sequence, selected = best
        body = ' '.join(sequence)
        line = definition(body)
        if measure(line) > MAX_DEFINITION_BYTES:
            raise AssertionError('Macro definition exceeds physical line limit')
        definitions.append(line)
        bodies[ref] = sequence
        audit.append(dict(macro_id=number, body=body, units=list(sequence),
                          definition_bytes=measure(line), estimated_saving=saving,
                          occurrence_index_space='current_compacted_units',
                          occurrences=[dict(part=parts[bi], unit_start=start,
                                            unit_count=len(sequence))
                                       for bi, start in selected]))
        for bi, start in reversed(selected):
            result[parts[bi]][start:start + len(sequence)] = [ref]
    proof = {}
    for part in parts:
        expanded = [leaf for unit in result[part]
                    for leaf in bodies.get(unit, (unit,))]
        before = ' '.join(original[part]).encode(encoding)
        after = ' '.join(expanded).encode(encoding)
        if expanded != original[part] or before != after:
            raise AssertionError('Macro expansion changed rendered musical units')
        proof[part] = dict(unit_identity=True, byte_identity=True,
                           expanded_bytes=len(before),
                           sha256=hashlib.sha256(before).hexdigest())
    before_size = sum(measure(' '.join(units)) for units in original.values())
    after_size = (sum(measure(' '.join(units)) for units in result.values())
                  + sum(measure(line + '\n') for line in definitions))
    return definitions, result, dict(
        algorithm='msx_exact_sequence_greedy', encoding=encoding,
        max_definition_bytes=MAX_DEFINITION_BYTES, max_call_depth=MAX_CALL_DEPTH,
        selected_call_depth=1 if definitions else 0, original_bytes=before_size,
        selected_bytes=after_size, saving_bytes=before_size - after_size,
        macros=audit, expansion_proof=proof)


@dataclass
class _Loop:
    children: list
    count: int


_NUM = r'[+-]?(?:\$[0-9a-fA-F]+|\d+)'
_TOKEN = re.compile(
    r'y(?:[A-Z]{2},' + _NUM + ',' + _NUM + '|' + _NUM + ',' + _NUM + ')'
    r'|E' + _NUM + '(?:,' + _NUM + '){5}'
    r'|KD' + _NUM + '(?:,' + _NUM + '){3}'
    r'|@W\d+|@\d+'
    r'|[PDoOvVClLqQtTmMSwp]' + _NUM + '(?:,' + _NUM + ')*'
    r'|[a-gr](?:[+-])?(?:%\d+|\d+\.*)?'
    r'|[&L<>]')


def _parse_muc(text):
    """Conservative lexer for generated MUC, never the MGSDRV parser."""
    root, stack, at = [], [], 0
    nodes = root
    while at < len(text):
        if text[at].isspace():
            at += 1
            continue
        if text[at] == '[':
            child = []
            loop = _Loop(child, 0)
            nodes.append(loop)
            stack.append((nodes, loop))
            nodes = child
            at += 1
            continue
        if text[at] == ']':
            if not stack:
                raise ValueError('Unbalanced finite loop')
            count = re.match(r'\](\d+)', text[at:])
            if count is None or int(count[1]) < 2:
                raise ValueError('Finite loop requires an explicit count >=2')
            nodes, loop = stack.pop()
            loop.count = int(count[1])
            at += len(count[0])
            continue
        match = _TOKEN.match(text, at)
        if match is None:
            raise ValueError('Unsupported MUC token at ' + repr(text[at:at + 20]))
        token = match[0]
        # An incomplete multi-argument command must not leave bare numeric
        # operands that a candidate could move independently.
        nodes.append(token)
        at = match.end()
    if stack:
        raise ValueError('Unbalanced finite loop')
    return root


def _node_text(node):
    return ('[' + ' '.join(map(_node_text, node.children)) + ']' + str(node.count)
            if isinstance(node, _Loop) else node)


def _tree_signature(nodes):
    return tuple(('loop', node.count, _tree_signature(node.children))
                 if isinstance(node, _Loop) else node for node in nodes)


def _locations(roots):
    """Enumerate complete track trees and their nested finite-loop bodies."""
    def walk(nodes, path):
        yield nodes, path
        for index, node in enumerate(nodes):
            if isinstance(node, _Loop):
                yield from walk(node.children, path + (index,))
    for part, nodes in roots.items():
        yield from walk(nodes, (part,))


def _candidate_sequences(locations, max_width):
    """Enumerate exactly repeated sibling sequences, pruning singleton prefixes.

    Counts span every track and nested-loop body. Tie-ending prefixes remain
    eligible for extension even though they cannot themselves become macros.
    Any extension of a globally singleton prefix is also globally singleton,
    so dropping it changes neither candidate ranking nor overlap selection.
    """
    words = [[_node_text(node) for node in nodes] for nodes, _ in locations]
    blocked = [['*' in word or re.search(r'(?<![A-Za-z])L(?![A-Za-z])', word)
                for word in row] for row in words]
    frontier = defaultdict(list)
    for loc, row in enumerate(words):
        for start, word in enumerate(row):
            if word != '&' and not blocked[loc][start] and len(word) <= MAX_BODY_CHARACTERS:
                frontier[(word,)].append((loc, start))
    candidates = {}
    for width in range(1, max_width + 1):
        following = defaultdict(list)
        for sequence, positions in frontier.items():
            if len(positions) < 2:
                continue
            eligible = [(loc, start) for loc, start in positions
                        if sequence[-1] != '&' and
                        (start + width == len(words[loc]) or words[loc][start + width] != '&')]
            if len(eligible) >= 2:
                candidates[sequence] = eligible
            if width == max_width:
                continue
            body_length = sum(map(len, sequence)) + width - 1
            for loc, start in positions:
                stop = start + width
                if stop == len(words[loc]) or blocked[loc][stop]:
                    continue
                word = words[loc][stop]
                if body_length + 1 + len(word) <= MAX_BODY_CHARACTERS:
                    following[sequence + (word,)].append((loc, start))
        frontier = following
        if not frontier:
            break
    return candidates


def _structured(tracks, *, limit, max_width, encoding, first_rank=0):
    # Unit boundaries are rendering artifacts, not musical or macro barriers.
    # Parsing the full track also keeps ties spanning adjacent units atomic.
    roots = {part: _parse_muc(' '.join(units)) for part, units in tracks.items()}
    expected = {part: _tree_signature(nodes) for part, nodes in roots.items()}
    measure = lambda text: len(text.encode(encoding))
    definitions, bodies, audit = [], {}, []
    for number in range(1, limit + 1):
        ref = f'*{number}'
        definition = lambda body: f'# {ref}{{{body}}}'
        available = MAX_DEFINITION_BYTES - measure(definition(''))
        locations = list(_locations(roots))
        candidates = _candidate_sequences(locations, max_width)
        ranked = []
        for sequence, positions in candidates.items():
            body = ' '.join(sequence)
            if measure(body) > available:
                continue
            per_use = measure(body) - measure(ref)
            cost = measure(definition(body) + '\n')
            if len(positions) < 2 or len(positions) * per_use - cost <= 0:
                continue
            selected, ends, occupied = [], {}, defaultdict(list)
            # Prefer ancestors: replacing a parent subsumes child occurrences.
            for loc, start in sorted(positions, key=lambda p: (len(locations[p[0]][1]), p)):
                path = locations[loc][1]
                if start < ends.get(loc, 0):
                    continue
                covered = False
                for depth in range(1, len(path)):
                    starts = occupied.get(path[:depth], ())
                    index = bisect_right(starts, path[depth]) - 1
                    if index >= 0 and path[depth] < starts[index] + len(sequence):
                        covered = True
                        break
                if covered:
                    continue
                selected.append((loc, start))
                ends[loc] = start + len(sequence)
                occupied[path].append(start)
            saving = len(selected) * per_use - cost
            if len(selected) > 1 and saving > 0:
                ranked.append((saving, sequence, selected))
        if not ranked:
            break
        ranked.sort(key=lambda row: (-row[0], row[1]))
        rank = first_rank if number == 1 else 0
        saving, sequence, selected = ranked[min(rank, len(ranked) - 1)]
        body = ' '.join(sequence)
        definitions.append(definition(body))
        bodies[ref] = _parse_muc(body)
        audit.append(dict(macro_id=number, body=body, units=list(sequence),
                          definition_bytes=measure(definition(body)), estimated_saving=saving,
                          occurrence_index_space='current_merged_track_token_tree',
                          occurrences=[dict(part=locations[loc][1][0],
                              loop_path=list(locations[loc][1][1:]),
                              token_start=start, token_count=len(sequence))
                              for loc, start in selected]))
        for loc, start in sorted(selected, reverse=True):
            locations[loc][0][start:start + len(sequence)] = [ref]
    def expand(nodes):
        result = []
        for node in nodes:
            if isinstance(node, _Loop):
                result.append(_Loop(expand(node.children), node.count))
            elif node in bodies:
                result.extend(bodies[node])
            else:
                result.append(node)
        return result
    result, proof = {}, {}
    for part, nodes in roots.items():
        expanded = expand(nodes)
        if _tree_signature(expanded) != expected[part]:
            raise AssertionError('MUC macro expansion changed exact loop/token tree')
        result[part] = ([' '.join(map(_node_text, nodes))] if definitions
                        else list(tracks[part]))
        expanded_text = ' '.join(map(_node_text, expanded))
        source_text = ' '.join(tracks[part])
        normalized = expanded_text.encode(encoding)
        proof[part] = dict(token_identity=True, loop_tree_identity=True,
                           unit_identity=not definitions,
                           byte_identity=normalized == source_text.encode(encoding),
                           normalized_token_byte_identity=True,
                           expanded_bytes=len(normalized),
                           sha256=hashlib.sha256(normalized).hexdigest())
    before = sum(measure(' '.join(units)) for units in tracks.values())
    after = (sum(measure(' '.join(units)) for units in result.values())
             + sum(measure(line + '\n') for line in definitions))
    return definitions, result, dict(
        algorithm='msx_exact_sequence_greedy_token_tree', encoding=encoding,
        strategy=f'width{max_width}-first{first_rank}',
        search_width=max_width, first_candidate_rank=first_rank,
        max_body_characters=MAX_BODY_CHARACTERS,
        max_definition_bytes=MAX_DEFINITION_BYTES, max_call_depth=MAX_CALL_DEPTH,
        selected_call_depth=1 if definitions else 0, original_bytes=before, selected_bytes=after,
        saving_bytes=before - after, macros=audit, expansion_proof=proof)


def compact_tracks(tracks, *, limit=32, max_width=64, encoding='shift_jis'):
    """MSX allocation portfolio adapted to exact MUCOMSX command trees.

    The atomic baseline wins ties. Four bounded searches inspect merged tracks
    and nested loops. Bodies never call macros, split a tie, or cross L.
    Search bounds are policy, distinct from compiler physical-line limits.
    """
    limit = min(limit, 32)
    original = {part: list(units) for part, units in tracks.items()}
    baseline = _compact_units(original, limit=limit, max_width=max_width, encoding=encoding)
    baseline[2].update(strategy='baseline', max_body_characters=MAX_BODY_CHARACTERS)
    for proof in baseline[2]['expansion_proof'].values():
        proof.update(token_identity=True, loop_tree_identity=True)
    choices = [baseline]
    try:
        for width, rank in ALLOCATION_PORTFOLIO:
            candidate = _structured(original, limit=limit,
                max_width=min(width, max_width), first_rank=rank, encoding=encoding)
            if candidate:
                choices.append(candidate)
    except ValueError as error:
        baseline[2]['structured_search_status'] = str(error)
        return baseline
    chosen = min(choices, key=lambda choice: choice[2]['selected_bytes'])
    chosen[2]['alternatives'] = [dict(algorithm=choice[2]['algorithm'],
                                     strategy=choice[2]['strategy'],
                                     selected_bytes=choice[2]['selected_bytes'])
                                for choice in choices]
    chosen[2]['structured_search_status'] = 'searched'
    return chosen
