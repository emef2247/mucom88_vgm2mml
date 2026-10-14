"""Native MUCOM rendering of shared source musical-unit repeat plans."""
import re
from musical_structure import source_plan
from source_loop_plan import expanded_tokens

NATIVE_REPEAT_DEPTH = 16


def repeat_depth(text):
    depth = maximum = 0
    for token in re.findall(r'\[|\]\d+|[^\s\[\]]+', text):
        if token == '[':
            depth += 1
            maximum = max(maximum, depth)
        elif token.startswith(']'):
            depth -= 1
    if depth:
        raise ValueError('Unbalanced target repeat')
    return maximum


def limit_repeat_depth(text, maximum=NATIVE_REPEAT_DEPTH):
    """Expand only excess target nesting; leave the native source tree intact."""
    stack = [[]]
    for token in re.findall(r'\[|\]\d+|[^\s\[\]]+', text):
        if token == '[':
            stack.append([])
        elif token.startswith(']'):
            if len(stack) == 1:
                raise ValueError('Unmatched target repeat end')
            body = stack.pop()
            stack[-1].append((body, int(token[1:])))
        else:
            stack[-1].append(token)
    if len(stack) != 1:
        raise ValueError('Unmatched target repeat start')
    expanded = []
    def render(nodes, depth=0):
        parts = []
        for node in nodes:
            if isinstance(node, str):
                parts.append(node)
            elif depth >= maximum:
                body, count = node
                expanded.append(dict(depth=depth + 1, repeats=count))
                parts.extend([render(body, depth)] * count)
            else:
                body, count = node
                parts.append('[' + render(body, depth + 1) + ']' + str(count))
        return ' '.join(parts)
    result = render(stack[0])
    if expanded_tokens(result) != expanded_tokens(text):
        raise AssertionError('Depth limiting changed projected commands')
    return result, expanded


def _native_repeat_members(keys, rows):
    """Target feasibility may select only repeats present in native evidence."""
    for row in rows:
        start,width,count=row['unit_start'],row['unit_width'],row['repeats']
        body=keys[start:start+width]
        if any(keys[start+i*width:start+(i+1)*width]!=body for i in range(count)):
            raise AssertionError('Target repeat is not present in native structure')


def _normalized_slice_keys(unit, keys, lengths):
    """Use accepted timing only where native action/state meaning is explicit."""
    if unit.kind == 'rest':
        return tuple((unit.kind, length) for length in lengths)
    if unit.kind == 'fm' and all(
            isinstance(key, tuple) and len(key) == 2
            and isinstance(key[1], tuple) and len(key[1]) == 2
            and key[1][0] in ('attack', 'held_control') for key in keys):
        return tuple((unit.kind, key[1], length) for key, length in zip(keys, lengths))
    # Auxiliary identities can include timed decoder/envelope actions. Keep
    # their native equality until a domain-specific semantic adapter exists.
    return tuple((unit.kind, key, length) for key, length in zip(keys, lengths))


def structure_track(units, plain_commands, slice_commands, slice_keys, slice_durations,
                    *, loops=True, normalize_lengths=False):
    """Retain native catalogs alongside explicitly accepted timing views."""
    outer_keys=tuple(unit.key for unit in units)
    outer=source_plan(outer_keys)
    outer_timing=source_plan((key,tuple(lengths)) for key,lengths in zip(outer_keys,slice_durations))
    normalized_keys=tuple(_normalized_slice_keys(unit,tuple(keys),lengths)
        for unit,keys,lengths in zip(units,slice_keys,slice_durations))
    normalized_outer_keys=tuple((unit.kind,keys) for unit,keys in zip(units,normalized_keys))
    normalized_outer=source_plan(normalized_outer_keys) if normalize_lengths else None
    nested_commands,inner_rows,children,timing_children,normalized_children=[],[],[],[],[]
    cache={}
    for index,(commands,keys,lengths) in enumerate(zip(slice_commands,slice_keys,slice_durations)):
        keys=tuple(keys)
        views=[('native',keys),('target_timing',tuple(zip(keys,lengths)))]
        if normalize_lengths:views.append(('normalized',normalized_keys[index]))
        rendered=[]
        plans={}
        for basis,candidate_keys in views:
            if candidate_keys not in cache:cache[candidate_keys]=source_plan(candidate_keys)
            child=cache[candidate_keys];plans[basis]=child
            text,rows=child.render(commands)
            _native_repeat_members(candidate_keys,rows)
            if basis!='normalized':_native_repeat_members(keys,rows)
            if expanded_tokens(text)!=expanded_tokens(' '.join(commands)):
                raise AssertionError('Inner musical structuring changed projected commands')
            rendered.append((text,rows,basis))
        selected=min(rendered,key=lambda item:len(item[0]))
        children.append(plans['native']);timing_children.append(plans['target_timing'])
        if normalize_lengths:normalized_children.append(plans['normalized'])
        nested_commands.append(selected[0])
        for _,rows,basis in rendered:
            inner_rows.extend(dict(row,unit_index=index,planning_basis=basis,
                selected_child=basis==selected[2]) for row in rows)
    plain=' '.join(plain_commands)
    outer_views=[('native',outer,outer_keys),('target_timing',outer_timing,outer_keys)]
    if normalize_lengths:outer_views.append(('normalized',normalized_outer,normalized_outer_keys))
    details=dict(plain=plain,inner_rows=inner_rows,children=children,
        feasible_children=timing_children,feasible_outer=outer_timing,
        normalized_children=normalized_children,normalized_outer=normalized_outer,
        normalized_slice_keys=normalized_keys if normalize_lengths else ())
    if not loops:
        for row in inner_rows:row['target_selected']=False
        return outer,[],plain,dict(details,selected='expanded',depth_expansions=[],maximum_depth=0,
            selected_before_depth_limit=plain,outer_projections={basis:[] for basis,_,_ in outer_views})
    candidates=[]
    for basis,plan,equality_keys in outer_views:
        for nesting,commands in (('outer_only',plain_commands),('nested',nested_commands)):
            text,rows=plan.render(commands)
            before_limit=text
            _native_repeat_members(equality_keys,rows)
            text,limits=limit_repeat_depth(text)
            if expanded_tokens(text)!=expanded_tokens(plain):
                raise AssertionError('Musical structuring changed projected commands')
            candidates.append((text,rows,limits,basis,nesting,before_limit))
    # Stable ties retain the native view and existing outer-only spelling.
    result,rows,limits,basis,nesting,before_limit=min(candidates,key=lambda item:len(item[0]))
    projections={}
    for candidate_basis,_,_ in outer_views:
        own=min((item for item in candidates if item[3]==candidate_basis),key=lambda item:len(item[0]))
        projections[candidate_basis]=[dict(row,planning_basis=candidate_basis,
            target_selected=(candidate_basis==basis and own[4]==nesting)) for row in own[1]]
    rows=[dict(row,planning_basis=basis,target_selected=True) for row in rows]
    for row in inner_rows:
        row['target_selected']=nesting=='nested' and row['selected_child']
    return outer,rows,result,dict(details,
        selected=basis+'_'+nesting,depth_expansions=limits,maximum_depth=repeat_depth(result),
        selected_before_depth_limit=before_limit,outer_projections=projections)
