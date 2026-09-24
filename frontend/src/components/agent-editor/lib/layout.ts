// ---------------------------------------------------------------------------
// Layout — thin dagre wrapper plus parallel-group derivation.
//
// Pure functions: no React, no DOM, no fetch. Everything here is testable
// with Vitest (see graph.test.ts).
// ---------------------------------------------------------------------------

import dagre from '@dagrejs/dagre'
import type { EdgePair } from './edges'

export const NODE_WIDTH = 240
export const NODE_HEIGHT = 96

export interface LayoutResult {
  positions: Record<string, { x: number; y: number }>
}

/**
 * Computes node positions with dagre (Sugiyama-style layered layout).
 *
 * Left-to-right: flow reads along the reading direction, and it matches the
 * source/target handles on the left and right edges of each card.
 *
 * Dagre returns node *centers*; React Flow positions nodes by their
 * *top-left corner*, so we subtract half the node size. Forgetting this
 * makes edges attach in the wrong places.
 *
 * Edges pointing at nodes missing from `nodeIds` are skipped, not fatal —
 * the document may be temporarily invalid while the user is editing.
 */
export function runLayout(
  nodeIds: string[],
  pairs: EdgePair[]
): LayoutResult {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 48, ranksep: 96 })
  g.setDefaultEdgeLabel(() => ({}))

  nodeIds.forEach((id) => {
    g.setNode(id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  })

  const known = new Set(nodeIds)
  pairs.forEach((p) => {
    if (known.has(p.source) && known.has(p.target)) {
      g.setEdge(p.source, p.target)
    }
  })

  dagre.layout(g)

  const positions: Record<string, { x: number; y: number }> = {}
  nodeIds.forEach((id) => {
    const node = g.node(id)
    if (node) {
      positions[id] = {
        x: node.x - NODE_WIDTH / 2,
        y: node.y - NODE_HEIGHT / 2,
      }
    }
  })

  return { positions }
}

/**
 * Places only the nodes missing from `known`, leaving every known position
 * byte-identical.
 *
 * Adding a node used to discard the whole cache and re-run dagre, which threw
 * away the layout the user had arranged by hand and made the entire canvas
 * jump. New nodes are instead stacked in a column clear of the existing
 * bounding box, so nothing already on screen moves and new nodes never land
 * on top of each other.
 */
export function placeNewNodes(
  nodeIds: string[],
  known: Record<string, { x: number; y: number }>
): Record<string, { x: number; y: number }> {
  const missing = nodeIds.filter((id) => !known[id])
  if (missing.length === 0) return { ...known }

  const placed = Object.values(known)
  const right = placed.length > 0 ? Math.max(...placed.map((p) => p.x)) + NODE_WIDTH + GAP : 0
  const top = placed.length > 0 ? Math.min(...placed.map((p) => p.y)) : 0

  const out = { ...known }
  missing.forEach((id, index) => {
    out[id] = { x: right, y: top + index * (NODE_HEIGHT + GAP) }
  })
  return out
}

const GAP = 48

// ---------------------------------------------------------------------------
// Parallel groups ("PARALLEL FLOW" containers)
// ---------------------------------------------------------------------------

export interface ParallelGroup {
  id: string
  label: string
  nodeIds: string[]
  x: number
  y: number
  width: number
  height: number
}

const GROUP_PADDING = 28

/**
 * Derives parallel-flow containers from `join` edges.
 *
 * The document has no group concept; a `join` with multiple sources
 * structurally means "several branches converge here". For every join we
 * walk backwards from each source until the branches meet at a common
 * ancestor (the fan-out point). The nodes between that point and the join
 * are the group members.
 *
 * DELIBERATE LIMITATION: if nested groups (one containing another) or two
 * groups with overlapping members are detected, we return an empty array.
 * Intersecting bounding boxes look worse than no group at all. This is not
 * a bug — do not "fix" it without also fixing the rendering.
 */
export function deriveGroups(
  pairs: EdgePair[],
  positions: Record<string, { x: number; y: number }>,
  edges: any[]
): ParallelGroup[] {
  const groups: { id: string; nodeIds: string[] }[] = []

  // Outgoing adjacency over node-to-node pairs only (ignore synthetic exits).
  const outgoing = new Map<string, string[]>()
  const incoming = new Map<string, string[]>()
  pairs.forEach((p) => {
    if (p.target.startsWith('__exit_')) return
    const outs = outgoing.get(p.source) || []
    outs.push(p.target)
    outgoing.set(p.source, outs)
    const ins = incoming.get(p.target) || []
    ins.push(p.source)
    incoming.set(p.target, ins)
  })

  // Backward ancestors (excluding the node itself) via BFS.
  function ancestorsOf(start: string): Set<string> {
    const seen = new Set<string>()
    const queue = [...(incoming.get(start) || [])]
    while (queue.length > 0) {
      const id = queue.shift()!
      if (seen.has(id) || id === start) continue
      seen.add(id)
      ;(incoming.get(id) || []).forEach((p) => queue.push(p))
    }
    return seen
  }

  let groupSeq = 0
  edges.forEach((e, idx) => {
    if (!e || e.kind !== 'join' || !Array.isArray(e.sources) || !e.target) return
    if (e.sources.length < 2) return

    // Ancestor sets per source; the common ancestor is the closest node
    // present in every set.
    const ancestorSets = e.sources.map((s: string) => ancestorsOf(s))
    if (ancestorSets.some((s: Set<string>) => s.size === 0)) return

    let common: string | null = null
    // Prefer ancestors of the first source, ordered by BFS discovery.
    for (const candidate of ancestorSets[0]) {
      if (ancestorSets.every((s: Set<string>) => s.has(candidate))) {
        common = candidate
        break
      }
    }
    if (common === null) return

    // Members: nodes strictly between the branch point and the join target,
    // i.e. ancestors of the join target that are descendants of `common`
    // (excluding `common` itself and the join target).
    const members = new Set<string>(e.sources as string[])
    const belowCommon = descendantsOf(common, outgoing, /* stopAt */ e.target)
    belowCommon.forEach((id) => {
      if (id !== e.target && id !== common) members.add(id)
    })
    if (members.size < 2) return

    groups.push({
      id: `group-${idx}-${groupSeq++}`,
      nodeIds: [...members],
    })
  })

  if (groups.length === 0) return []

  // Bail-out on nesting / overlap.
  for (let i = 0; i < groups.length; i++) {
    for (let j = i + 1; j < groups.length; j++) {
      const a = new Set(groups[i].nodeIds)
      if (groups[j].nodeIds.some((id) => a.has(id))) return []
    }
  }

  return groups.map((g) => {
    const ids = g.nodeIds.filter((id) => positions[id])
    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity
    ids.forEach((id) => {
      const pos = positions[id]
      minX = Math.min(minX, pos.x)
      minY = Math.min(minY, pos.y)
      maxX = Math.max(maxX, pos.x + NODE_WIDTH)
      maxY = Math.max(maxY, pos.y + NODE_HEIGHT)
    })
    if (ids.length === 0 || minX === Infinity) {
      return { id: g.id, label: 'PARALLEL FLOW', nodeIds: ids, x: 0, y: 0, width: 0, height: 0 }
    }
    return {
      id: g.id,
      label: 'PARALLEL FLOW',
      nodeIds: ids,
      x: minX - GROUP_PADDING,
      y: minY - GROUP_PADDING,
      width: maxX - minX + GROUP_PADDING * 2,
      height: maxY - minY + GROUP_PADDING * 2,
    }
  })
}

/** Forward BFS from `start`, not crossing `stopAt`. */
function descendantsOf(
  start: string,
  outgoing: Map<string, string[]>,
  stopAt: string
): Set<string> {
  const seen = new Set<string>()
  const queue = [...(outgoing.get(start) || [])]
  while (queue.length > 0) {
    const id = queue.shift()!
    if (seen.has(id) || id === start || id === stopAt) continue
    seen.add(id)
    ;(outgoing.get(id) || []).forEach((c) => queue.push(c))
  }
  return seen
}

// Re-export the type so panel/canvas code can import from layout.ts alone.
export type { EdgePair }
