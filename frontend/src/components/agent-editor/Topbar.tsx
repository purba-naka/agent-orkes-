// ---------------------------------------------------------------------------
// Editor topbar — floats over the canvas (position: absolute, semi
// transparent, blurred). Contains the ONLY way out of the editor, contextual
// badges, zoom controls, and the save/publish actions.
// ---------------------------------------------------------------------------

import { useEffect, useState } from 'react'
import { useReactFlow } from '@xyflow/react'
import { ArrowLeft, Maximize2, PanelLeft, RotateCcw, Save, UploadCloud, ZoomIn, ZoomOut } from 'lucide-react'
import type { AgentItem } from '../../api'

export interface TopbarProps {
  agent: AgentItem
  version: number
  isDirty: boolean
  saving: boolean
  publishing: boolean
  status: string | null
  /** changes identity on every new status so toasts re-appear */
  statusAt: number
  onBack: () => void
  onSave: () => void
  onPublish: () => void
  onResetLayout: () => void
  /** Collapsing the node palette widens the canvas. */
  paletteOpen: boolean
  onTogglePalette: () => void
}

export function Topbar({
  agent,
  version,
  isDirty,
  saving,
  publishing,
  status,
  statusAt,
  onBack,
  onSave,
  onPublish,
  onResetLayout,
  paletteOpen,
  onTogglePalette,
}: TopbarProps) {
  const { zoomIn, zoomOut, fitView } = useReactFlow()

  // Status is a short-lived toast, never a permanent banner — banners push
  // the layout, which is unacceptable on a full-bleed canvas. The reducer
  // keeps the message until the next action replaces it; this local state
  // only handles the visual auto-dismiss.
  const [toastDismissed, setToastDismissed] = useState(false)
  useEffect(() => {
    setToastDismissed(false)
    if (!status) return
    const timer = window.setTimeout(() => setToastDismissed(true), 4000)
    return () => window.clearTimeout(timer)
  }, [status, statusAt])

  return (
    <div className="editor-topbar-overlay">
      <div className="editor-topbar-row">
        <button
          type="button"
          className="btn small icon-only"
          onClick={onTogglePalette}
          aria-pressed={paletteOpen}
          aria-label={paletteOpen ? 'Hide node palette' : 'Show node palette'}
          title={paletteOpen ? 'Hide node palette' : 'Show node palette'}
        >
          <PanelLeft size={14} aria-hidden="true" />
        </button>

        <button type="button" className="btn small editor-back-button" onClick={onBack}>
          <ArrowLeft size={13} aria-hidden="true" />
          All agents
        </button>

        <span className="editor-topbar-name" title={agent.name}>{agent.name}</span>

        {isDirty && <span className="editor-badge badge-dirty">Unsaved changes</span>}
        <span className="editor-badge">Draft v{version}</span>
        {agent.active_revision_id && agent.active_revision && (
          <span className="editor-badge badge-published">
            Published rev #{agent.active_revision.revision_number}
          </span>
        )}

        <span className="editor-topbar-spacer" />

        <div className="editor-zoom-controls" role="group" aria-label="Canvas zoom">
          <button type="button" className="btn small icon-only" onClick={() => zoomIn({ duration: 200 })} aria-label="Zoom in" title="Zoom in">
            <ZoomIn size={14} aria-hidden="true" />
          </button>
          <button type="button" className="btn small icon-only" onClick={() => zoomOut({ duration: 200 })} aria-label="Zoom out" title="Zoom out">
            <ZoomOut size={14} aria-hidden="true" />
          </button>
          <button type="button" className="btn small icon-only" onClick={() => fitView({ duration: 300, padding: 0.2 })} aria-label="Fit view" title="Fit view">
            <Maximize2 size={14} aria-hidden="true" />
          </button>
          <button type="button" className="btn small" onClick={onResetLayout} title="Reset node layout (dagre)">
            <RotateCcw size={13} aria-hidden="true" />
            Reset layout
          </button>
        </div>

        <button type="button" className="btn" onClick={onSave} disabled={saving || !isDirty}>
          <Save size={13} aria-hidden="true" />
          {saving ? 'Saving…' : 'Save draft'}
        </button>
        <button type="button" className="btn primary" onClick={onPublish} disabled={publishing}>
          <UploadCloud size={13} aria-hidden="true" />
          {publishing ? 'Publishing…' : 'Publish'}
        </button>

      </div>

      {status && !toastDismissed && (
        <div key={statusAt} className="editor-toast" role="status">
          {status}
        </div>
      )}
    </div>
  )
}
