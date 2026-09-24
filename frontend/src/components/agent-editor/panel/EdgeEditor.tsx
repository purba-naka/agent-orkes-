// ---------------------------------------------------------------------------
// Edge editor — the most intricate panel piece: the five edge kinds have
// totally different shapes (doc §5.3).
//
//   direct:     source, target
//   exit:       source, result_name (select from named_exits)
//   join:       sources (multi-select), target, join mode
//   semantic:   source node, source field, route table (value -> target), default
//   mechanical: source node, source field, operator, value, then, else
//
// For semantic/mechanical, `source` is stored as the array [nodeId, fieldName].
// The form has two separate inputs, but both write to that ONE array — this is
// the classic bug source (doc §5.3). `parseFieldPathMapping` moved verbatim
// from GraphCanvas.tsx (do not rewrite from scratch).
//
// Backend allowlists: mechanical operators are eq, ne, lt, lte, gt, gte, in,
// not_in, is_null, is_not_null; join only supports 'all'.
// ---------------------------------------------------------------------------

import { useMemo } from 'react'
import { Trash2 } from 'lucide-react'

/**
 * Moved verbatim from GraphCanvas.tsx — parses a JSON textarea value into a
 * field -> path-array mapping. Throws with a human message on invalid input.
 */
export function parseFieldPathMapping(
  value: string,
  label: string,
  required: boolean
): Record<string, string[]> | null {
  if (!value.trim()) {
    if (required) throw new Error(`${label} is required`)
    return null
  }

  const parsed: unknown = JSON.parse(value)
  if (
    typeof parsed !== 'object'
    || parsed === null
    || Array.isArray(parsed)
    || !Object.entries(parsed).every(([field, path]) => (
      field.length > 0
      && Array.isArray(path)
      && path.length > 0
      && path.every((part) => typeof part === 'string' && part.length > 0)
    ))
  ) {
    throw new Error(`${label} must map field names to non-empty field-path arrays`)
  }
  return parsed as Record<string, string[]>
}

export type EdgeKind = 'direct' | 'exit' | 'join' | 'semantic' | 'mechanical'

const EDGE_KINDS: EdgeKind[] = ['direct', 'exit', 'join', 'semantic', 'mechanical']

const MECHANICAL_OPS = [
  'eq',
  'ne',
  'lt',
  'lte',
  'gt',
  'gte',
  'in',
  'not_in',
  'is_null',
  'is_not_null',
] as const

export interface EdgeEditorProps {
  document: Record<string, any>
  edgeIndex: number
  onChange: (updatedDoc: Record<string, any>) => void
  onDelete: () => void
}

export function EdgeEditor({ document, edgeIndex, onChange, onDelete }: EdgeEditorProps) {
  const edges: Array<Record<string, any>> = document.edges || []
  const edge = edges[edgeIndex]
  const nodes: Array<Record<string, any>> = document.nodes || []
  const nodeIds: string[] = nodes.map((n) => n.id)
  const namedExits: string[] = useMemo(
    () => document.named_exits || ['success'],
    [document.named_exits]
  )

  if (!edge) {
    return <p className="hint">Edge not found — it may have been deleted.</p>
  }

  const kind = (edge.kind || 'direct') as EdgeKind

  // semantic / mechanical source is [nodeId, fieldName]
  const sourceNodeId = Array.isArray(edge.source) ? edge.source[0] : edge.source || ''
  const sourceField = Array.isArray(edge.source) ? edge.source[1] || '' : ''

  /** Any previous downstream target, whatever the previous kind was. */
  const previousTarget: string =
    (typeof edge.target === 'string' && edge.target)
    || (typeof edge.default === 'string' && edge.default)
    || (typeof edge.then === 'string' && edge.then)
    || Object.values(edge.routes || {})[0] as string | undefined
    || ''

  function replaceEdge(next: Record<string, any>) {
    const updated = {
      ...document,
      edges: edges.map((e, i) => (i === edgeIndex ? next : e)),
    }
    onChange(updated)
  }

  function patchEdge(patch: Record<string, any>) {
    replaceEdge({ ...edge, ...patch })
  }

  function changeKind(nextKind: EdgeKind) {
    let next: Record<string, any>
    switch (nextKind) {
      case 'direct':
        next = { kind: 'direct', source: sourceNodeId, target: previousTarget }
        break
      case 'exit':
        next = { kind: 'exit', source: sourceNodeId, result_name: namedExits[0] || 'success' }
        break
      case 'join':
        next = {
          kind: 'join',
          sources: [sourceNodeId],
          target: previousTarget,
          join: 'all',
        }
        break
      case 'semantic':
        next = {
          kind: 'semantic',
          source: [sourceNodeId, sourceField || 'result'],
          routes: {},
          default: previousTarget || null,
        }
        break
      case 'mechanical':
        next = {
          kind: 'mechanical',
          source: [sourceNodeId, sourceField || 'result'],
          operator: 'eq',
          value: '',
          then: previousTarget,
          else: '',
        }
        break
    }
    replaceEdge(next)
  }

  // --- route table helpers (semantic) ------------------------------------
  function setRouteValue(existingKey: string, nextKey: string) {
    const routes: Record<string, string> = { ...(edge.routes || {}) }
    const target = routes[existingKey]
    delete routes[existingKey]
    if (nextKey.trim()) routes[nextKey.trim()] = target
    patchEdge({ routes })
  }

  function setRouteTarget(key: string, target: string) {
    patchEdge({ routes: { ...(edge.routes || {}), [key]: target } })
  }

  function removeRoute(key: string) {
    const routes = { ...(edge.routes || {}) }
    delete routes[key]
    patchEdge({ routes })
  }

  function addRoute() {
    const routes = edge.routes || {}
    let n = 1
    while (`route_${n}` in routes) n += 1
    patchEdge({ routes: { ...routes, [`route_${n}`]: '' } })
  }

  // --- join sources -------------------------------------------------------
  function toggleJoinSource(nodeId: string) {
    const sources: string[] = edge.sources || []
    const next = sources.includes(nodeId)
      ? sources.filter((s) => s !== nodeId)
      : [...sources, nodeId]
    patchEdge({ sources: next })
  }

  return (
    <div className="edge-editor">
      <div className="config-tab-nodebar">
        <div>
          <strong>Edge #{edgeIndex}</strong>
          <span className="config-tab-nodekind">{kind} edge</span>
        </div>
        <div className="config-tab-actions">
          <button type="button" className="btn small danger" onClick={onDelete}>
            <Trash2 size={12} aria-hidden="true" />
            Delete
          </button>
        </div>
      </div>

      <div className="form-row">
        <label>Kind</label>
        <select value={kind} onChange={(e) => changeKind(e.target.value as EdgeKind)}>
          {EDGE_KINDS.map((k) => (
            <option key={k} value={k}>{k}</option>
          ))}
        </select>
      </div>

      {kind === 'direct' && (
        <>
          <div className="form-row">
            <label>Source node</label>
            <select
              value={typeof edge.source === 'string' ? edge.source : sourceNodeId}
              onChange={(e) => patchEdge({ source: e.target.value })}
            >
              <option value="">-- Select node --</option>
              {nodeIds.map((id) => <option key={id} value={id}>{id}</option>)}
            </select>
          </div>
          <div className="form-row">
            <label>Target node</label>
            <select
              value={edge.target || ''}
              onChange={(e) => patchEdge({ target: e.target.value })}
            >
              <option value="">-- Select node --</option>
              {nodeIds.map((id) => <option key={id} value={id}>{id}</option>)}
            </select>
          </div>
        </>
      )}

      {kind === 'exit' && (
        <>
          <div className="form-row">
            <label>Source node</label>
            <select
              value={typeof edge.source === 'string' ? edge.source : sourceNodeId}
              onChange={(e) => patchEdge({ source: e.target.value })}
            >
              <option value="">-- Select node --</option>
              {nodeIds.map((id) => <option key={id} value={id}>{id}</option>)}
            </select>
          </div>
          <div className="form-row">
            <label>Result name (named exit)</label>
            <select
              value={edge.result_name || ''}
              onChange={(e) => patchEdge({ result_name: e.target.value })}
            >
              {namedExits.map((exit) => <option key={exit} value={exit}>{exit}</option>)}
            </select>
          </div>
        </>
      )}

      {kind === 'join' && (
        <>
          <div className="form-row">
            <label>Source nodes (select at least 2)</label>
            <div className="ee-checklist">
              {nodeIds.map((id) => (
                <label key={id} className="ee-check">
                  <input
                    type="checkbox"
                    checked={(edge.sources || []).includes(id)}
                    onChange={() => toggleJoinSource(id)}
                  />
                  <span>{id}</span>
                </label>
              ))}
            </div>
          </div>
          <div className="form-row">
            <label>Target node</label>
            <select
              value={edge.target || ''}
              onChange={(e) => patchEdge({ target: e.target.value })}
            >
              <option value="">-- Select node --</option>
              {nodeIds.map((id) => <option key={id} value={id}>{id}</option>)}
            </select>
          </div>
          <div className="form-row">
            <label>Join mode</label>
            <select
              value={edge.join || 'all'}
              onChange={(e) => patchEdge({ join: e.target.value })}
            >
              <option value="all">all (fan-in waits for every source)</option>
            </select>
            <span className="hint">Only <code>join: all</code> is supported.</span>
          </div>
        </>
      )}

      {kind === 'semantic' && (
        <>
          <div className="form-row">
            <label>Source node</label>
            <select
              value={sourceNodeId}
              onChange={(e) => patchEdge({ source: [e.target.value, sourceField] })}
            >
              <option value="">-- Select node --</option>
              {nodeIds.map((id) => <option key={id} value={id}>{id}</option>)}
            </select>
          </div>
          <div className="form-row">
            <label>Source field (output field to route on)</label>
            <input
              type="text"
              value={sourceField}
              placeholder="e.g. result"
              onChange={(e) => patchEdge({ source: [sourceNodeId, e.target.value] })}
            />
          </div>
          <div className="form-row">
            <label>Routes (field value &rarr; target)</label>
            <div className="ee-routes">
              {Object.entries(edge.routes || {}).map(([value, target]) => (
                <div key={value} className="ee-route-row">
                  <input
                    type="text"
                    value={value}
                    aria-label="Route value"
                    onChange={(e) => setRouteValue(value, e.target.value)}
                  />
                  <span className="ee-route-arrow" aria-hidden="true">&rarr;</span>
                  <select
                    value={target as string}
                    aria-label="Route target"
                    onChange={(e) => setRouteTarget(value, e.target.value)}
                  >
                    <option value="">-- Target --</option>
                    {nodeIds.map((id) => <option key={id} value={id}>{id}</option>)}
                    {namedExits.map((exit) => (
                      <option key={exit} value={exit}>exit: {exit}</option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="btn small danger icon-only"
                    onClick={() => removeRoute(value)}
                    aria-label={`Remove route ${value}`}
                  >
                    <Trash2 size={12} aria-hidden="true" />
                  </button>
                </div>
              ))}
              <button type="button" className="btn small" onClick={addRoute}>
                Add route
              </button>
            </div>
          </div>
          <div className="form-row">
            <label>Default target</label>
            <select
              value={edge.default || ''}
              onChange={(e) => patchEdge({ default: e.target.value || null })}
            >
              <option value="">-- None --</option>
              {nodeIds.map((id) => <option key={id} value={id}>{id}</option>)}
              {namedExits.map((exit) => (
                <option key={exit} value={exit}>exit: {exit}</option>
              ))}
            </select>
          </div>
        </>
      )}

      {kind === 'mechanical' && (
        <>
          <div className="form-row">
            <label>Source node</label>
            <select
              value={sourceNodeId}
              onChange={(e) => patchEdge({ source: [e.target.value, sourceField] })}
            >
              <option value="">-- Select node --</option>
              {nodeIds.map((id) => <option key={id} value={id}>{id}</option>)}
            </select>
          </div>
          <div className="form-row">
            <label>Source field (output field to compare)</label>
            <input
              type="text"
              value={sourceField}
              placeholder="e.g. result"
              onChange={(e) => patchEdge({ source: [sourceNodeId, e.target.value] })}
            />
          </div>
          <div className="form-row">
            <label>Operator</label>
            <select
              value={edge.operator || 'eq'}
              onChange={(e) => patchEdge({ operator: e.target.value })}
            >
              {MECHANICAL_OPS.map((op) => <option key={op} value={op}>{op}</option>)}
            </select>
          </div>
          <div className="form-row">
            <label>Value</label>
            <input
              type="text"
              value={edge.value ?? ''}
              placeholder="e.g. done"
              disabled={edge.operator === 'is_null' || edge.operator === 'is_not_null'}
              onChange={(e) => patchEdge({ value: e.target.value })}
            />
          </div>
          <div className="form-row">
            <label>Then (condition true)</label>
            <select value={edge.then || ''} onChange={(e) => patchEdge({ then: e.target.value })}>
              <option value="">-- Target --</option>
              {nodeIds.map((id) => <option key={id} value={id}>{id}</option>)}
              {namedExits.map((exit) => (
                <option key={exit} value={exit}>exit: {exit}</option>
              ))}
            </select>
          </div>
          <div className="form-row">
            <label>Else (condition false)</label>
            <select value={edge.else || ''} onChange={(e) => patchEdge({ else: e.target.value })}>
              <option value="">-- Target --</option>
              {nodeIds.map((id) => <option key={id} value={id}>{id}</option>)}
              {namedExits.map((exit) => (
                <option key={exit} value={exit}>exit: {exit}</option>
              ))}
            </select>
          </div>
        </>
      )}
    </div>
  )
}
