// ---------------------------------------------------------------------------
// EditorShell — the three right-hand columns of the canvas editor
// (the icon rail is App.tsx's job): Palette | Canvas area | Inspector panel.
//
// Owns the single useReducer via useAgentEditor and wires every child to it
// with plain props (architecture decision: props + composition, no context
// store beyond ConfigTab's local accordion context).
//
// MUST be rendered with key={agent.id} — the reducer initializes lazily from
// the agent's draft, and the key guarantees a fresh mount per agent.
// ---------------------------------------------------------------------------

import { useCallback, useState } from 'react'
import { ReactFlowProvider } from '@xyflow/react'
import type { AgentItem, ModelItem, ToolItem } from '../../api'
import { useAgentEditor } from './useAgentEditor'
import { clearPositions } from './lib/positions'
import { Palette, deriveNodeId } from './Palette'
import { Topbar } from './Topbar'
import { Canvas } from './canvas/Canvas'
import { InspectorPanel } from './panel/InspectorPanel'

export interface EditorShellProps {
  agent: AgentItem
  models: ModelItem[]
  tools: ToolItem[]
  agents: AgentItem[]
  onExit: () => void
  onAgentChanged: () => void
  theme: 'light' | 'dark'
}

/**
 * Side column visibility persists across sessions — a collapsed column is a
 * workspace preference, not per-agent state. Storage failures are swallowed:
 * the user only loses the preference, never their work.
 */
function readPref(key: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(`agent-editor:${key}`)
    return raw === null ? fallback : raw === '1'
  } catch {
    return fallback
  }
}

function writePref(key: string, value: boolean): boolean {
  try {
    localStorage.setItem(`agent-editor:${key}`, value ? '1' : '0')
  } catch {
    // Private browsing / quota exceeded: ignore.
  }
  return value
}

export function EditorShell(props: EditorShellProps) {
  return (
    <ReactFlowProvider>
      <EditorShellInner {...props} />
    </ReactFlowProvider>
  )
}

function EditorShellInner({
  agent,
  models,
  tools,
  agents,
  onExit,
  onAgentChanged,
  theme,
}: EditorShellProps) {
  const { state, dispatch, isDirty, saveDraft, publish, reloadFromServer, overwriteServer } =
    useAgentEditor({ agent, models, tools, agents, onExit, onAgentChanged })

  /** increment to force a dagre relayout (Reset layout button) */
  const [resetSignal, setResetSignal] = useState(0)
  /** Collapsing the palette gives its width to the canvas. */
  const [paletteOpen, setPaletteOpen] = useState(() => readPref('palette', true))

  const togglePalette = useCallback(
    () => setPaletteOpen((o) => writePref('palette', !o)),
    []
  )

  const doc = state.doc

  const handleDocChange = useCallback(
    (updated: Record<string, any>) => dispatch({ type: 'docChanged', doc: updated }),
    [dispatch]
  )

  const handleSelectNode = useCallback(
    (nodeId: string) => dispatch({ type: 'selectNode', nodeId }),
    [dispatch]
  )

  const handleSelectEdge = useCallback(
    (edgeIndex: number) => dispatch({ type: 'selectEdge', edgeIndex }),
    [dispatch]
  )

  const handleClearSelection = useCallback(
    () => dispatch({ type: 'clearSelection' }),
    [dispatch]
  )

  /** Drag-to-connect creates a `direct` edge and selects it immediately. */
  const handleConnectEdge = useCallback(
    (source: string, target: string) => {
      if (!doc) return
      const edges = doc.edges || []
      handleDocChange({ ...doc, edges: [...edges, { kind: 'direct', source, target }] })
      dispatch({ type: 'selectEdge', edgeIndex: edges.length })
    },
    [doc, handleDocChange, dispatch]
  )

  /** "New inline node" button in the palette. */
  const handleNewInlineNode = useCallback(() => {
    if (!doc) return
    const nodes: Array<Record<string, any>> = doc.nodes || []
    const existing = new Set(nodes.map((n) => n.id))
    const id = deriveNodeId(`${agent.name} node`, existing)
    const updated = {
      ...doc,
      nodes: [
        ...nodes,
        {
          id,
          kind: 'agent',
          agent: {
            mode: 'inline',
            model_revision_id: null,
            system_prompt: '',
            input_schema: { type: 'object' },
            output_schema: { type: 'object' },
            tool_revision_ids: [],
            agent_tool_revision_ids: [],
            context_policy: { upstream: [] },
            middleware_policy: {},
            review_output: false,
          },
        },
      ],
    }
    handleDocChange(updated)
    dispatch({ type: 'selectNode', nodeId: id })
  }, [doc, agent.name, handleDocChange, dispatch])

  /** Palette drag-and-drop: create a `ref` node for a published agent. */
  const handleDropCreateNode = useCallback(
    (payload: { kind: 'agent'; mode: 'ref'; agentRevisionId: string }) => {
      if (!doc || payload.kind !== 'agent') return
      const nodes: Array<Record<string, any>> = doc.nodes || []
      const existing = new Set(nodes.map((n) => n.id))
      const source = agents.find(
        (a) => a.active_revision && a.active_revision.id === payload.agentRevisionId
      )
      const id = deriveNodeId(source?.name || 'ref agent', existing)
      const updated = {
        ...doc,
        nodes: [
          ...nodes,
          {
            id,
            kind: 'agent',
            agent: {
              mode: 'ref',
              agent_revision_id: payload.agentRevisionId,
              input_mapping: { prompt: ['input', 'prompt'] },
            },
          },
        ],
      }
      handleDocChange(updated)
      dispatch({ type: 'selectNode', nodeId: id })
    },
    [doc, agents, handleDocChange, dispatch]
  )

  /** Delete a node and every edge that touches it (all five edge shapes). */
  const handleDeleteNode = useCallback(
    (nodeId: string) => {
      if (!doc) return
      const nodes: Array<Record<string, any>> = ((doc.nodes || []) as Array<Record<string, any>>).filter(
        (n) => n.id !== nodeId
      )
      const edges = ((doc.edges || []) as Array<Record<string, any>>).filter((e) => {
        if (e.source === nodeId || e.target === nodeId) return false
        if (Array.isArray(e.sources) && e.sources.includes(nodeId)) return false
        if (Array.isArray(e.source) && e.source[0] === nodeId) return false
        return true
      })
      const updated: Record<string, any> = { ...doc, nodes, edges }
      if (doc.entry_node_id === nodeId) {
        updated.entry_node_id = nodes[0]?.id || ''
      }
      handleDocChange(updated)
      dispatch({ type: 'clearSelection' })
    },
    [doc, handleDocChange, dispatch]
  )

  const handleDeleteEdge = useCallback(
    (edgeIndex: number) => {
      if (!doc) return
      const updated = {
        ...doc,
        edges: ((doc.edges || []) as Array<Record<string, any>>).filter(
          (_e, i) => i !== edgeIndex
        ),
      }
      handleDocChange(updated)
      dispatch({ type: 'clearSelection' })
    },
    [doc, handleDocChange, dispatch]
  )

  const handleSetEntry = useCallback(
    (nodeId: string) => {
      if (!doc) return
      handleDocChange({ ...doc, entry_node_id: nodeId })
    },
    [doc, handleDocChange]
  )

  const handleResetLayout = useCallback(() => {
    clearPositions(agent.id)
    setResetSignal((s) => s + 1)
  }, [agent.id])

  if (!doc) {
    return (
      <div className="editor-shell editor-shell-empty">
        <p className="hint">This agent has no draft document.</p>
      </div>
    )
  }

  return (
    <div className="editor-shell">
      <Palette
        currentAgentId={agent.id}
        agents={agents}
        onNewInlineNode={handleNewInlineNode}
        open={paletteOpen}
      />

      <div className="editor-canvas-area">
        <Topbar
          agent={agent}
          version={state.version}
          isDirty={isDirty}
          saving={state.saving}
          publishing={state.publishing}
          status={state.status}
          statusAt={state.statusAt}
          onBack={onExit}
          onSave={() => void saveDraft()}
          onPublish={() => void publish()}
          onResetLayout={handleResetLayout}
          paletteOpen={paletteOpen}
          onTogglePalette={togglePalette}
        />
        <Canvas
          agentId={agent.id}
          doc={doc}
          diagnostics={state.diagnostics}
          selection={state.selection}
          agents={agents}
          tools={tools}
          theme={theme}
          resetSignal={resetSignal}
          onSelectNode={handleSelectNode}
          onSelectEdge={handleSelectEdge}
          onClearSelection={handleClearSelection}
          onConnectEdge={handleConnectEdge}
          onDocChange={handleDocChange}
          onDropCreateNode={handleDropCreateNode}
        />
      </div>

      <InspectorPanel
        document={doc}
        selection={state.selection}
        agent={agent}
        models={models}
        tools={tools}
        onChange={handleDocChange}
        onSetEntry={handleSetEntry}
        onDeleteNode={handleDeleteNode}
        onDeleteEdge={handleDeleteEdge}
      />

      {state.conflict && (
        <div className="conflict-overlay" role="dialog" aria-modal="true" aria-label="Draft conflict">
          <div className="conflict-dialog">
            <h4>Draft ini diubah di tempat lain</h4>
            <p>
              Ada versi yang lebih baru (v{state.conflict.currentVersion}) di server.
              Pilih salah satu — tidak ada merge otomatis.
            </p>
            <div className="button-row">
              <button
                type="button"
                className="btn"
                onClick={reloadFromServer}
              >
                Muat ulang dan buang perubahan saya
              </button>
              <button
                type="button"
                className="btn primary"
                onClick={overwriteServer}
              >
                Timpa dengan versi saya
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
