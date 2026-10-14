"""Ordered integer-domain timing allocation, independent of target clocks."""


def allocate_tick_domains(times, lower, upper, nearest, protected):
    """Choose ordered target ticks while keeping protected intervals positive.

    Callers supply exact integer domains and nearest ticks, including fixed
    anchors. Adjacent source boundaries may coalesce. Positive protected
    intervals add a one-tick edge, even across intervening boundaries.
    Return None if no allocation satisfies the supplied domains and edges.
    Invalid source positions, domains or interval references raise ValueError.
    Input arrays and intervals are never modified.
    """
    times, lower, upper, nearest = map(tuple, (times, lower, upper, nearest))
    if not (len(times) == len(lower) == len(upper) == len(nearest)):
        raise ValueError('Timing arrays must have the same length')
    if any(type(value) is not int for array in (times, lower, upper, nearest)
           for value in array):
        raise ValueError('Timing positions and domains must be integers')
    if any(t < 0 for t in times) or any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError('Source positions must be nonnegative and strictly ordered')
    index = {t: i for i, t in enumerate(times)}
    outgoing = [[(i + 1, 0)] if i + 1 < len(times) else []
                for i in range(len(times))]
    incoming = [[] for _ in times]
    for interval in protected:
        try:
            a, b = interval['start_vgmticks'], interval['end_vgmticks']
        except (KeyError, TypeError) as exc:
            raise ValueError('Protected intervals require source start/end positions') from exc
        if (type(a) is not int or type(b) is not int or a > b
                or a not in index or b not in index):
            raise ValueError('Protected intervals must reference ordered source positions')
        if b > a:
            outgoing[index[a]].append((index[b], 1))
    latest = list(upper)
    for i in range(len(times) - 1, -1, -1):
        for j, delta in outgoing[i]:
            latest[i] = min(latest[i], latest[j] - delta)
            incoming[j].append((i, delta))
        if lower[i] > latest[i]:
            return None
    chosen = []
    for i in range(len(times)):
        earliest = max([lower[i]] + [chosen[j] + delta for j, delta in incoming[i]])
        if earliest > latest[i]:
            return None
        chosen.append(min(latest[i], max(earliest, nearest[i])))
    return dict(zip(times, chosen))
