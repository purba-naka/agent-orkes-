// ---------------------------------------------------------------------------
// Edge flattening — pure logic moved out of the old GraphCanvas rfEdges memo.
//
// The agent document has five edge kinds with very different shapes. Dagre and
// the canvas both need a flat list of uniform (source, target) pairs; this
// module is the single place that performs the flattening.
//
// CRITICAL: for `semantic` and `mechanical` edges the `source` field is NOT a
// string — it is a two-element array [nodeId, fieldName]. Read it as
// `e.source?.[0]`. Treating it as a string silently takes the first character
// of the node id and draws a wrong graph.
// ---------------------------------------------------------------------------

export type EdgeKind = 'direct' | 'exit' | 'join' | 'semantic' | 'mechanical'

export interface EdgePair {
  /** index of the edge in document.edges — used to map diagnostics */
  edgeIndex: number
  /** source node ID */
  source: string
  /** target node ID, or `__exit_<name>` for a named exit */
  target: string
  /** label displayed on the edge */
  label?: string
  /** originating kind — used to pick color / line style */
  kind: EdgeKind
  /** true for default/else branches — drawn thinner */
  isFallback?: boolean
}

/** Resolves a route target: named exits become synthetic `__exit_<name>` ids. */
function resolveTarget(target: string, namedExits: string[]): string {
  return namedExits.includes(target) ? `__exit_${target}` : target
}

export function extractEdgePairs(
  edges: any[],
  namedExits: string[]
): EdgePair[] {
  const pairs: EdgePair[] = []
  const exits = namedExits || []

  ;(edges || []).forEach((e, idx) => {
    if (!e || typeof e.kind !== 'string') return

    if (e.kind === 'direct') {
      if (e.source && e.target) {
        pairs.push({
          edgeIndex: idx,
          source: e.source,
          target: e.target,
          label: 'direct',
          kind: 'direct',
        })
      }
    } else if (e.kind === 'exit') {
      if (e.source) {
        pairs.push({
          edgeIndex: idx,
          source: e.source,
          target: `__exit_${e.result_name || 'success'}`,
          label: `exit: ${e.result_name || 'success'}`,
          kind: 'exit',
        })
      }
    } else if (e.kind === 'join') {
      const sources: string[] = e.sources || []
      sources.forEach((s) => {
        if (s && e.target) {
          pairs.push({
            edgeIndex: idx,
            source: s,
            target: e.target,
            label: 'join: all',
            kind: 'join',
          })
        }
      })
    } else if (e.kind === 'semantic') {
      // `source` is [nodeId, fieldName] — an ARRAY, not a string.
      const sNode = e.source?.[0]
      const fName = e.source?.[1]
      const routes: Record<string, string> = e.routes || {}
      Object.entries(routes).forEach(([value, target]) => {
        if (sNode && target) {
          pairs.push({
            edgeIndex: idx,
            source: sNode,
            target: resolveTarget(target, exits),
            label: `${fName}="${value}"`,
            kind: 'semantic',
          })
        }
      })
      if (e.default && sNode) {
        pairs.push({
          edgeIndex: idx,
          source: sNode,
          target: resolveTarget(e.default, exits),
          label: `${fName}=default`,
          kind: 'semantic',
          isFallback: true,
        })
      }
    } else if (e.kind === 'mechanical') {
      // `source` is [nodeId, fieldName] — an ARRAY, not a string.
      const sNode = e.source?.[0]
      const fName = e.source?.[1]
      const op = e.operator
      const val = e.value
      if (e.then && sNode) {
        pairs.push({
          edgeIndex: idx,
          source: sNode,
          target: resolveTarget(e.then, exits),
          label: `${fName} ${op} ${val}`,
          kind: 'mechanical',
        })
      }
      if (e.else && sNode) {
        pairs.push({
          edgeIndex: idx,
          source: sNode,
          target: resolveTarget(e.else, exits),
          label: 'else',
          kind: 'mechanical',
          isFallback: true,
        })
      }
    }
  })

  return pairs
}
