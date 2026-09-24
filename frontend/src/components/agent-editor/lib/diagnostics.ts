// ---------------------------------------------------------------------------
// Diagnostic mapping — moved from GraphCanvas.tsx (nodeDiagMap / edgeDiagMap).
//
// `path` (note: `path`, not `instance_path`) contains JSON-pointer-like
// strings such as `/nodes/3/model` or `/edges/1/routes`. The numeric index
// refers to the position in document.nodes / document.edges.
// ---------------------------------------------------------------------------

import type { Diagnostic } from '../../../api'

const NODE_PATH_RE = /\/nodes\/(\d+)/
const EDGE_PATH_RE = /\/edges\/(\d+)/

/** Maps diagnostics to the node id each one belongs to. */
export function buildNodeDiagMap(
  diagnostics: Diagnostic[],
  nodes: Array<Record<string, any>>
): Map<string, Diagnostic[]> {
  const map = new Map<string, Diagnostic[]>()
  diagnostics.forEach((d) => {
    const match = d.path.match(NODE_PATH_RE)
    if (match) {
      const node = nodes[parseInt(match[1], 10)]
      if (node) {
        const list = map.get(node.id) || []
        list.push(d)
        map.set(node.id, list)
      }
    }
  })
  return map
}

/** Maps diagnostics to the edge index each one belongs to. */
export function buildEdgeDiagMap(
  diagnostics: Diagnostic[]
): Map<number, Diagnostic[]> {
  const map = new Map<number, Diagnostic[]>()
  diagnostics.forEach((d) => {
    const match = d.path.match(EDGE_PATH_RE)
    if (match) {
      const idx = parseInt(match[1], 10)
      const list = map.get(idx) || []
      list.push(d)
      map.set(idx, list)
    }
  })
  return map
}

/** Error / warning counts per node id, for card badges. */
export function countNodeDiags(
  nodeDiagMap: Map<string, Diagnostic[]>
): Map<string, { errors: number; warnings: number }> {
  const counts = new Map<string, { errors: number; warnings: number }>()
  nodeDiagMap.forEach((diags, nodeId) => {
    counts.set(nodeId, {
      errors: diags.filter((d) => d.severity === 'error').length,
      warnings: diags.filter((d) => d.severity === 'warning').length,
    })
  })
  return counts
}
