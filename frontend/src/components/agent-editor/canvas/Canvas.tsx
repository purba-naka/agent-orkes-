// ---------------------------------------------------------------------------
// Canvas — the React Flow surface.
//
// Responsibilities:
//   1. build RF nodes from document.nodes + named_exits
//   2. build RF edges from extractEdgePairs
//   3. resolve positions: loadPositions() first, runLayout() as fallback
//   4. persist positions on drag stop (onNodeDragStop)
//   5. render parallel groups from deriveGroups() (inside ViewportPortal)
//   6. floating legend + diagnostics list
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  MarkerType,
  MiniMap,
  ReactFlow,
  ViewportPortal,
  applyNodeChanges,
  useReactFlow,
} from '@xyflow/react'
import type {
  Connection,
  Edge,
  Node,
  NodeChange,
  OnNodeDrag,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react'
import type { AgentItem, Diagnostic, ToolItem, ToolRevision } from '../../../api'
import type { EditorState } from '../useAgentEditor'
import type { EdgePair } from '../lib/edges'
import { extractEdgePairs } from '../lib/edges'
import type { ParallelGroup } from '../lib/layout'
import { NODE_HEIGHT, NODE_WIDTH, deriveGroups, placeNewNodes, runLayout } from '../lib/layout'
import { clearPositions, computeSignature, loadPositions, savePositions } from '../lib/positions'
import { buildEdgeDiagMap, buildNodeDiagMap, countNodeDiags } from '../lib/diagnostics'
import { AgentNodeCard } from './AgentNodeCard'
import type { AgentCardData } from './AgentNodeCard'
import { ExitNode } from './ExitNode'
import { ParallelGroupBox } from './ParallelGroup'

// MUST live outside the component: a fresh object per render makes React Flow
// remount every node on every render (flickering canvas, vanishing selection).
const nodeTypes = {
  agentCard: AgentNodeCard,
  exitNode: ExitNode,
}

export interface CanvasProps {
  agentId: string
  doc: Record<string, any>
  diagnostics: Diagnostic[]
  selection: EditorState['selection']
  agents: AgentItem[]
  tools: ToolItem[]
  theme: 'light' | 'dark'
  /** increment to force a full relayout (Reset layout button) */
  resetSignal: number
  onSelectNode: (nodeId: string) => void
  onSelectEdge: (edgeIndex: number) => void
  onClearSelection: () => void
  onConnectEdge: (source: string, target: string) => void
  onDocChange: (doc: Record<string, any>) => void
  onDropCreateNode: (payload: { kind: 'agent'; mode: 'ref'; agentRevisionId: string }) => void
}

interface TokenColors {
  fgMuted: string
  success: string
  accent: string
  warning: string
  destructive: string
  border: string
}

const FALLBACK_COLORS: TokenColors = {
  fgMuted: 'gray',
  success: 'green',
  accent: 'orange',
  warning: 'goldenrod',
  destructive: 'red',
  border: 'lightgray',
}

export function Canvas({
  agentId,
  doc,
  diagnostics,
  selection,
  agents,
  tools,
  theme,
  resetSignal,
  onSelectNode,
  onSelectEdge,
  onClearSelection,
  onConnectEdge,
  onDocChange,
  onDropCreateNode,
}: CanvasProps) {
  // ------------------------------------------------------------------
  // Document projections
  // ------------------------------------------------------------------
  const rawNodes: Array<Record<string, any>> = useMemo(() => doc.nodes || [], [doc.nodes])
  const rawEdges: Array<Record<string, any>> = useMemo(() => doc.edges || [], [doc.edges])
  const namedExits: string[] = useMemo(() => doc.named_exits || ['success'], [doc.named_exits])
  const entryNodeId: string = doc.entry_node_id || ''
  const exitIds = useMemo(() => namedExits.map((n) => `__exit_${n}`), [namedExits])
  const nodeIds = useMemo(() => [...rawNodes.map((n) => n.id), ...exitIds], [rawNodes, exitIds])
  const pairs: EdgePair[] = useMemo(() => extractEdgePairs(rawEdges, namedExits), [rawEdges, namedExits])
  const signature = useMemo(() => computeSignature(nodeIds), [nodeIds])

  // ------------------------------------------------------------------
  // Positions: cache first, dagre fallback. The memo only recomputes when
  // the node SET changes (signature) or the user resets the layout.
  //
  // A partially cached layout keeps every known position and only places the
  // new nodes. Re-running dagre here instead would move nodes the user never
  // touched, which reads as the whole canvas jumping on every insert.
  // ------------------------------------------------------------------
  const lastResetRef = useRef(resetSignal)
  const basePositions = useMemo(() => {
    const skipCache = resetSignal !== lastResetRef.current
    lastResetRef.current = resetSignal
    if (skipCache) clearPositions(agentId)
    const loaded = skipCache ? null : loadPositions(agentId, nodeIds)
    if (!loaded) return { positions: runLayout(nodeIds, pairs).positions, fromCache: false }
    const complete = nodeIds.every((id) => loaded[id])
    if (complete) return { positions: loaded, fromCache: true }
    return { positions: placeNewNodes(nodeIds, loaded), fromCache: false }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on signature + reset only
  }, [agentId, signature, resetSignal])

  useEffect(() => {
    if (!basePositions.fromCache) {
      savePositions(agentId, nodeIds, basePositions.positions)
    }
  }, [agentId, nodeIds, basePositions])



  // ------------------------------------------------------------------
  // Labels for card subtitles (tool / referenced agent revisions)
  // ------------------------------------------------------------------
  const toolRevisionById = useMemo(() => {
    const map = new Map<string, { tool: ToolItem; revision: ToolRevision }>()
    tools.forEach((tool) =>
      (tool.revisions || []).forEach((revision) => map.set(revision.id, { tool, revision }))
    )
    return map
  }, [tools])

  const agentRevisionLabels = useMemo(() => {
    const labels = new Map<string, string>()
    agents.forEach((agent) => {
      const revision = agent.active_revision
      if (revision) labels.set(revision.id, `${agent.name} · Rev #${revision.revision_number}`)
    })
    return labels
  }, [agents])

  // ------------------------------------------------------------------
  // Diagnostics
  // ------------------------------------------------------------------
  const nodeDiagMap = useMemo(() => buildNodeDiagMap(diagnostics, rawNodes), [diagnostics, rawNodes])
  const edgeDiagMap = useMemo(() => buildEdgeDiagMap(diagnostics), [diagnostics])
  const nodeDiagCounts = useMemo(() => countNodeDiags(nodeDiagMap), [nodeDiagMap])

  // ------------------------------------------------------------------
  // Theme-aware token colors. Read ONCE per theme change — never inside a
  // render loop (getComputedStyle forces a reflow).
  // ------------------------------------------------------------------
  const [colors, setColors] = useState<TokenColors>(FALLBACK_COLORS)
  useEffect(() => {
    const cs = getComputedStyle(document.documentElement)
    const read = (cssVar: string, fallbackKey: keyof TokenColors) =>
      cs.getPropertyValue(cssVar).trim() || FALLBACK_COLORS[fallbackKey]
    setColors({
      fgMuted: read('--color-fg-muted', 'fgMuted'),
      success: read('--color-success', 'success'),
      accent: read('--color-accent', 'accent'),
      warning: read('--color-warning', 'warning'),
      destructive: read('--color-destructive', 'destructive'),
      border: read('--color-border', 'border'),
    })
  }, [theme])

  // ------------------------------------------------------------------
  // React Flow nodes
  // ------------------------------------------------------------------
  // Specs describe everything about a node EXCEPT its position and measured
  // size: those two belong to React Flow (see onNodesChange below).
  const nodeSpecs = useMemo(() => {
    const specs: Array<Omit<Node, 'position'>> = rawNodes.map((n) => {
      const counts = nodeDiagCounts.get(n.id) || { errors: 0, warnings: 0 }
      const isTool = n.kind === 'tool'
      let subtitle = ''
      if (isTool) {
        const selected = toolRevisionById.get(n.tool_revision_id)
        subtitle = selected
          ? `${selected.tool.name} · Rev #${selected.revision.revision_number} · ${selected.revision.risk_level} risk`
          : `Revision ${n.tool_revision_id || 'not selected'}`
      } else if (n.agent?.mode === 'ref') {
        subtitle = agentRevisionLabels.get(n.agent.agent_revision_id) || `Revision ${n.agent.agent_revision_id || 'not selected'}`
      }
      const data: AgentCardData = {
        nodeId: n.id,
        kind: isTool ? 'tool' : 'agent',
        mode: n.agent?.mode,
        subtitle,
        isEntry: n.id === entryNodeId,
        isSelected: selection.type === 'node' && selection.nodeId === n.id,
        errorCount: counts.errors,
        warningCount: counts.warnings,
      }
      return {
        id: n.id,
        type: 'agentCard',
        data: data as unknown as Record<string, unknown>,
        selected: selection.type === 'node' && selection.nodeId === n.id,
      }
    })

    namedExits.forEach((exitName) => {
      specs.push({
        id: `__exit_${exitName}`,
        type: 'exitNode',
        data: { exitName } as unknown as Record<string, unknown>,
        selectable: false,
      })
    })

    return specs
  }, [rawNodes, entryNodeId, selection, nodeDiagCounts, toolRevisionById, agentRevisionLabels, namedExits])

  // ------------------------------------------------------------------
  // React Flow owns the live node array. Rebuilding it from a memo on every
  // frame of a drag would throw away the `measured` size React Flow just wrote
  // and fight its internal position, which reads as a flickering card and
  // handles collapsed onto a single point.
  // ------------------------------------------------------------------
  const [rfNodes, setRfNodes] = useState<Node[]>([])
  const lastSyncedReset = useRef(resetSignal)
  useEffect(() => {
    const isReset = lastSyncedReset.current !== resetSignal
    lastSyncedReset.current = resetSignal
    setRfNodes((prev) => {
      const previous = isReset ? new Map<string, Node>() : new Map(prev.map((n) => [n.id, n]))
      return nodeSpecs.map((spec) => {
        const existing = previous.get(spec.id)
        const position = existing?.position ?? basePositions.positions[spec.id] ?? { x: 0, y: 0 }
        return existing ? { ...existing, ...spec, position } : { ...spec, position }
      })
    })
  }, [nodeSpecs, basePositions, resetSignal])

  const positions = useMemo(() => {
    const map: Record<string, { x: number; y: number }> = {}
    rfNodes.forEach((n) => {
      map[n.id] = n.position
    })
    return map
  }, [rfNodes])

  // ------------------------------------------------------------------
  // React Flow edges
  // ------------------------------------------------------------------
  const rfEdges: Edge[] = useMemo(() => {
    let subIdx = 0
    return pairs.map((p) => {
      const diags = edgeDiagMap.get(p.edgeIndex) || []
      const hasError = diags.some((d) => d.severity === 'error')
      const isSelected = selection.type === 'edge' && selection.edgeIndex === p.edgeIndex

      let color = colors.fgMuted
      if (hasError) color = colors.destructive
      else if (p.kind === 'exit') color = colors.success
      else if (p.kind === 'semantic') color = colors.accent
      else if (p.kind === 'mechanical') color = colors.warning

      const strokeWidth = hasError ? 3 : p.isFallback ? 1 : 2
      const dash = p.isFallback ? '3 3' : '6 4'

      const id = `rf-edge-${p.edgeIndex}-${subIdx++}`
      return {
        id,
        source: p.source,
        target: p.target,
        label: p.label,
        selected: isSelected,
        data: { edgeIndex: p.edgeIndex },
        style: {
          stroke: color,
          strokeWidth: isSelected ? strokeWidth + 1 : strokeWidth,
          strokeDasharray: dash,
        },
        markerEnd: { type: MarkerType.ArrowClosed, color },
        labelBgStyle: { fill: 'var(--color-bg-elevated)', fillOpacity: 0.9 },
      }
    })
  }, [pairs, colors, edgeDiagMap, selection])

  // ------------------------------------------------------------------
  // Parallel groups
  // ------------------------------------------------------------------
  const groups: ParallelGroup[] = useMemo(
    () => deriveGroups(pairs, positions, rawEdges),
    [pairs, positions, rawEdges]
  )

  // ------------------------------------------------------------------
  // Interaction handlers
  // ------------------------------------------------------------------
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    // Keep React Flow's full node state, including measured dimensions and
    // selection, instead of competing with its live drag position.
    setRfNodes((prev) => applyNodeChanges(changes, prev))
  }, [])

  const onNodeDragStop = useCallback<OnNodeDrag>(
    (_event, node) => {
      // Persist the merged layout; failures are swallowed inside savePositions.
      savePositions(agentId, nodeIds, { ...positions, [node.id]: node.position })
    },
    [agentId, nodeIds, positions]
  )

  const handleNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      if (node.id.startsWith('__exit_')) return
      onSelectNode(node.id)
    },
    [onSelectNode]
  )

  const handleEdgeClick = useCallback(
    (event: React.MouseEvent, edge: Edge) => {
      event.preventDefault()
      const edgeIndex = (edge.data as any)?.edgeIndex
      if (typeof edgeIndex === 'number') onSelectEdge(edgeIndex)
    },
    [onSelectEdge]
  )

  const handleConnect = useCallback(
    (conn: Connection) => {
      if (!conn.source || !conn.target) return
      if (conn.source === conn.target) return
      const exists = pairs.some((p) => p.source === conn.source && p.target === conn.target)
      if (exists) return
      onConnectEdge(conn.source, conn.target)
    },
    [pairs, onConnectEdge]
  )

  const handleNodesDelete = useCallback(
    (nodesToDelete: Node[]) => {
      const ids = new Set(nodesToDelete.map((n) => n.id))
      if (ids.size === 0) return
      const updated: Record<string, any> = { ...doc }
      updated.nodes = (updated.nodes || []).filter((n: any) => !ids.has(n.id))
      // Cover all five edge shapes: direct/exit (string source),
      // join (sources[]), semantic/mechanical (array source, routes,
      // default, then, else).
      updated.edges = (doc.edges || []).filter((e: any) => {
        if (typeof e.source === 'string' && ids.has(e.source)) return false
        if (Array.isArray(e.source) && ids.has(e.source[0])) return false
        if (typeof e.target === 'string' && ids.has(e.target)) return false
        if (Array.isArray(e.sources) && (e.sources as string[]).some((s) => ids.has(s))) return false
        if (typeof e.then === 'string' && ids.has(e.then)) return false
        if (typeof e.else === 'string' && ids.has(e.else)) return false
        if (typeof e.default === 'string' && ids.has(e.default)) return false
        if (e.routes && Object.values(e.routes).some((t) => ids.has(t as string))) return false
        return true
      })
      if (updated.entry_node_id && ids.has(updated.entry_node_id)) {
        updated.entry_node_id = updated.nodes[0]?.id || ''
      }
      onDocChange(updated)
      onClearSelection()
    },
    [doc, onDocChange, onClearSelection]
  )

  const handleEdgesDelete = useCallback(
    (edgesToDelete: Edge[]) => {
      const indices = new Set(
        edgesToDelete.map((e) => (e.data as any)?.edgeIndex).filter((i) => typeof i === 'number')
      )
      if (indices.size === 0) return
      const updated: Record<string, any> = { ...doc }
      updated.edges = (doc.edges || []).filter((_: any, i: number) => !indices.has(i))
      onDocChange(updated)
      onClearSelection()
    },
    [doc, onDocChange, onClearSelection]
  )

  const handleDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  const handleDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault()
      const raw = event.dataTransfer.getData('application/reactflow')
      if (!raw) return
      try {
        const payload = JSON.parse(raw)
        if (payload?.kind === 'agent' && payload?.agentRevisionId) {
          onDropCreateNode({ kind: 'agent', mode: 'ref', agentRevisionId: payload.agentRevisionId })
        }
      } catch {
        // Malformed payload: ignore the drop.
      }
    },
    [onDropCreateNode]
  )

  return (
    <div className="editor-canvas" onDragOver={handleDragOver} onDrop={handleDrop}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onNodeClick={handleNodeClick}
        onNodeDragStop={onNodeDragStop}
        onNodesDelete={handleNodesDelete}
        onEdgeClick={handleEdgeClick}
        onEdgesDelete={handleEdgesDelete}
        onConnect={handleConnect}
        onPaneClick={onClearSelection}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.3}
        maxZoom={1.75}
        deleteKeyCode={['Backspace', 'Delete']}
      >
        <ViewportPortal>
          {groups.map((group) => (
            <ParallelGroupBox key={group.id} group={group} />
          ))}
        </ViewportPortal>
        <Background color={colors.border} gap={16} />
        {/* No <Controls> — the topbar already owns zoom in/out/fit view. */}
        <MiniMap
          pannable
          zoomable
          /* React Flow reads the svg's width/height ATTRIBUTES from this prop;
             sizing via CSS alone leaves a 200x150 svg overflowing the panel. */
          style={{ width: 168, height: 104 }}
          nodeColor={() => colors.fgMuted}
          nodeStrokeColor={() => colors.fgMuted}
          maskColor="rgba(0, 0, 0, 0.5)"
        />
      </ReactFlow>

      <EdgeLegend />

      <DiagnosticsList
        diagnostics={diagnostics}
        rawNodes={rawNodes}
        positions={positions}
        onSelectNode={onSelectNode}
        onSelectEdge={onSelectEdge}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Floating legend
// ---------------------------------------------------------------------------

function EdgeLegend() {
  return (
    <details className="canvas-legend" aria-label="Edge legend">
      <summary>Edges</summary>
      <div className="canvas-legend-body">
        <div className="canvas-legend-row"><span className="canvas-swatch swatch-muted" />direct: A → B, sequential</div>
        <div className="canvas-legend-row"><span className="canvas-swatch swatch-success" />exit: ends at a named exit</div>
        <div className="canvas-legend-row"><span className="canvas-swatch swatch-muted" />join: many into one, waits all</div>
        <div className="canvas-legend-row"><span className="canvas-swatch swatch-accent" />semantic: routes by verdict</div>
        <div className="canvas-legend-row"><span className="canvas-swatch swatch-warning" />mechanical: routes by condition</div>
        <div className="canvas-legend-row"><span className="canvas-swatch swatch-destructive" />error / invalid</div>
      </div>
    </details>
  )
}

// ---------------------------------------------------------------------------
// Floating diagnostics list (two-layer display, layer 2)
//
// Layer 1 is the badge on each node card; this list answers "how much is
// still broken" and includes document-level diagnostics that no card can
// host. Clicking an item selects the related node/edge and centers the
// viewport on it (setCenter).
// ---------------------------------------------------------------------------

function DiagnosticsList({
  diagnostics,
  rawNodes,
  positions,
  onSelectNode,
  onSelectEdge,
}: {
  diagnostics: Diagnostic[]
  rawNodes: Array<Record<string, any>>
  positions: Record<string, { x: number; y: number }>
  onSelectNode: (nodeId: string) => void
  onSelectEdge: (edgeIndex: number) => void
}) {
  const hasErrors = diagnostics.some((d) => d.severity === 'error')
  const [open, setOpen] = useState(hasErrors)

  useEffect(() => {
    setOpen(hasErrors)
  }, [hasErrors])

  if (diagnostics.length === 0) return null

  const errorCount = diagnostics.filter((d) => d.severity === 'error').length
  const warningCount = diagnostics.length - errorCount

  return (
    <div className={`canvas-diagnostics ${open ? 'open' : ''}`}>
      <button
        type="button"
        className="canvas-diagnostics-header"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <AlertTriangle size={13} className="canvas-diag-icon" aria-hidden="true" />
        <span>
          {errorCount} error{errorCount === 1 ? '' : 's'}, {warningCount} warning{warningCount === 1 ? '' : 's'}
        </span>
        {open ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronUp size={13} aria-hidden="true" />}
      </button>
      {open && (
        <ul className="canvas-diagnostics-list">
          {diagnostics.map((d, i) => (
            <DiagnosticRow
              key={`${d.code}-${d.path}-${i}`}
              diagnostic={d}
              rawNodes={rawNodes}
              positions={positions}
              onSelectNode={onSelectNode}
              onSelectEdge={onSelectEdge}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function DiagnosticRow({
  diagnostic: d,
  rawNodes,
  positions,
  onSelectNode,
  onSelectEdge,
}: {
  diagnostic: Diagnostic
  rawNodes: Array<Record<string, any>>
  positions: Record<string, { x: number; y: number }>
  onSelectNode: (nodeId: string) => void
  onSelectEdge: (edgeIndex: number) => void
}) {
  const { setCenter } = useReactFlow()
  const nodeMatch = d.path.match(/\/nodes\/(\d+)/)
  const edgeMatch = d.path.match(/\/edges\/(\d+)/)

  function handleClick() {
    if (nodeMatch) {
      const node = rawNodes[parseInt(nodeMatch[1], 10)]
      if (node) {
        onSelectNode(node.id)
        const pos = positions[node.id]
        if (pos) {
          setCenter(pos.x + NODE_WIDTH / 2, pos.y + NODE_HEIGHT / 2, { zoom: 1, duration: 300 })
        }
      }
    } else if (edgeMatch) {
      onSelectEdge(parseInt(edgeMatch[1], 10))
    }
  }

  return (
    <li>
      <button type="button" className={`canvas-diagnostic-item ${d.severity}`} onClick={handleClick}>
        <span className="code">[{d.code}]</span>
        <span className="path">{d.path}</span>
        <span className="msg">{d.message}</span>
      </button>
    </li>
  )
}
