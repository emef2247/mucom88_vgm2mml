"""Shared exact musical-unit structure; target notation is selected later."""
from collections import Counter
from source_loop_plan import SourceLoopPlan

def source_plan(keys):
    """Reuse the shared exact planner between provably unrepeatable leaves.

    A symbol occurring once cannot belong to an adjacent repeated body: that
    body would contain it at least twice. Such leaves are exact separators,
    not width/depth caps. Region catalogs are restored to global source indices.
    """
    from hierarchical_loops import Node
    from loop_structure import LoopStructure, Repeat, candidates
    keys=tuple(keys)
    counts=Counter(keys)
    cuts=[i for i,key in enumerate(keys) if counts[key]==1]
    if not cuts:
        # The shared solver already shortcuts complete repetitions of a body
        # of distinct symbols. The same lower bound applies to a periodic
        # region with a partial prefix: retain that prefix, then one repeat.
        # Equal-cost ordering prefers leaves before repeats in the shared DP.
        period=next((i for i in range(1,len(keys)) if keys[i]==keys[0]),0)
        if (period and len(keys)//period>=2 and len(set(keys[:period]))==period
                and all(key==keys[i%period] for i,key in enumerate(keys))):
            prefix=len(keys)%period
            tree=tuple(Node(i,i+1) for i in range(prefix))+(Node(prefix,len(keys),
                tuple(Node(i,i+1) for i in range(prefix,prefix+period)),len(keys)//period),)
            structure=LoopStructure(keys,candidates(keys),tree)
            return SourceLoopPlan(tree,keys,structure)
        return SourceLoopPlan.build(keys,strategy='structural')
    tree=[];catalog=[() for _ in keys];start=0
    def shifted(node,offset):
        return Node(node.start+offset,node.end+offset,
                    tuple(shifted(child,offset) for child in node.children),node.repeats)
    for stop in cuts+[len(keys)]:
        if start<stop:
            region=source_plan(keys[start:stop])
            tree.extend(shifted(node,start) for node in region.tree)
            for index,rows in enumerate(region.structure.catalog):
                catalog[start+index]=tuple(Repeat(row.start+start,row.width,row.max_repeats) for row in rows)
        if stop<len(keys): tree.append(Node(stop,stop+1))
        start=stop+1
    structure=LoopStructure(keys,tuple(catalog),tuple(tree))
    return SourceLoopPlan(structure.tree,keys,structure)
