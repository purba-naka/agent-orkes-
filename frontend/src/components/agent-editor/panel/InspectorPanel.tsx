// ---------------------------------------------------------------------------
// Inspector panel — right column, 360px, three contexts (doc §5.1):
//   selection node  -> Config tab (that node's config)
//   selection edge  -> EdgeEditor for that edge
//   no selection    -> TopologyForm (entry_node_id, named_exits, recursion)
// Test and History tabs are always available regardless of selection.
// ---------------------------------------------------------------------------

import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'
import { FlaskConical, History, Settings2 } from 'lucide-react'
import type { AgentItem, ModelItem, ToolItem } from '../../../api'
import type { EditorState } from '../useAgentEditor'
import { ConfigTab } from './ConfigTab'
import { EdgeEditor } from './EdgeEditor'
import { TopologyForm } from './TopologyForm'
import { TestTab } from './TestTab'
import { HistoryTab } from './HistoryTab'

type PanelTab = 'config' | 'test' | 'history'

const MIN_WIDTH = 280
const MAX_WIDTH = 640
const DEFAULT_WIDTH = 360
const STORAGE_KEY = 'agent-editor:inspector-width'

function clampWidth(width: number): number {
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, width))
}

function readWidth(): number {
  try {
    const stored = Number(localStorage.getItem(STORAGE_KEY))
    return Number.isFinite(stored) && stored > 0 ? clampWidth(stored) : DEFAULT_WIDTH
  } catch {
    return DEFAULT_WIDTH
  }
}

function saveWidth(width: number): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(width))
  } catch {
    // Losing a layout preference must not interrupt editing.
  }
}

export interface InspectorPanelProps {
  document: Record<string, any>
  selection: EditorState['selection']
  agent: AgentItem
  models: ModelItem[]
  tools: ToolItem[]
  onChange: (updatedDoc: Record<string, any>) => void
  onSetEntry: (nodeId: string) => void
  onDeleteNode: (nodeId: string) => void
  onDeleteEdge: (edgeIndex: number) => void
}

export function InspectorPanel({
  document,
  selection,
  agent,
  models,
  tools,
  onChange,
  onSetEntry,
  onDeleteNode,
  onDeleteEdge,
}: InspectorPanelProps) {
  const [tab, setTab] = useState<PanelTab>('config')
  const [width, setWidth] = useState(readWidth)
  const widthRef = useRef(width)
  const drag = useRef<{ x: number; width: number } | null>(null)

  function updateWidth(nextWidth: number) {
    const clamped = clampWidth(nextWidth)
    widthRef.current = clamped
    setWidth(clamped)
  }

  function handleResizeStart(event: PointerEvent<HTMLDivElement>) {
    drag.current = { x: event.clientX, width }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  function handleResizeMove(event: PointerEvent<HTMLDivElement>) {
    if (!drag.current) return
    updateWidth(drag.current.width + drag.current.x - event.clientX)
  }

  function handleResizeEnd(event: PointerEvent<HTMLDivElement>) {
    if (!drag.current) return
    drag.current = null
    event.currentTarget.releasePointerCapture(event.pointerId)
    saveWidth(widthRef.current)
  }

  function handleResizeKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    const nextWidth = clampWidth(width + (event.key === 'ArrowLeft' ? 16 : -16))
    updateWidth(nextWidth)
    saveWidth(nextWidth)
  }

  // Selecting something is a request to edit it — switch to the first tab.
  useEffect(() => {
    setTab('config')
  }, [selection])

  const firstTabLabel =
    selection.type === 'edge' ? 'Edge' : selection.type === 'node' ? 'Config' : 'Topology'

  return (
    <aside className="inspector-panel" aria-label="Inspector" style={{ width }}>
      <div
        className="inspector-resize-handle"
        role="separator"
        aria-label="Resize inspector"
        aria-orientation="vertical"
        aria-valuemin={MIN_WIDTH}
        aria-valuemax={MAX_WIDTH}
        aria-valuenow={width}
        tabIndex={0}
        onPointerDown={handleResizeStart}
        onPointerMove={handleResizeMove}
        onPointerUp={handleResizeEnd}
        onPointerCancel={handleResizeEnd}
        onKeyDown={handleResizeKey}
      />
      <div className="panel-tabs" role="tablist" aria-label="Inspector tabs">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'config'}
          className={`panel-tab ${tab === 'config' ? 'active' : ''}`}
          onClick={() => setTab('config')}
        >
          <Settings2 size={13} aria-hidden="true" />
          {firstTabLabel}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'test'}
          className={`panel-tab ${tab === 'test' ? 'active' : ''}`}
          onClick={() => setTab('test')}
        >
          <FlaskConical size={13} aria-hidden="true" />
          Test
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'history'}
          className={`panel-tab ${tab === 'history' ? 'active' : ''}`}
          onClick={() => setTab('history')}
        >
          <History size={13} aria-hidden="true" />
          History
        </button>
      </div>

      <div className="panel-body">
        {tab === 'config' && selection.type === 'node' && (
          <ConfigTab
            document={document}
            nodeId={selection.nodeId}
            models={models}
            tools={tools}
            onChange={onChange}
            onSetEntry={onSetEntry}
            onDeleteNode={onDeleteNode}
          />
        )}
        {tab === 'config' && selection.type === 'edge' && (
          <EdgeEditor
            document={document}
            edgeIndex={selection.edgeIndex}
            onChange={onChange}
            onDelete={() => onDeleteEdge(selection.edgeIndex)}
          />
        )}
        {tab === 'config' && selection.type === 'none' && (
          <TopologyForm document={document} onChange={onChange} />
        )}
        {tab === 'test' && <TestTab agent={agent} />}
        {tab === 'history' && <HistoryTab agent={agent} />}
      </div>
    </aside>
  )
}
