// ---------------------------------------------------------------------------
// Palette — permanent second column: search box, "New inline node" button,
// and a searchable list of the workspace's other published agents.
//
// Agent items are draggable onto the canvas (HTML5 drag-and-drop) to create
// a `ref` node. The drop position is deliberately ignored — decision 5:
// adding a node changes the node set, which discards the position cache and
// triggers a full dagre relayout. Do not "fix" this; it keeps behavior
// consistent with every other way of adding nodes.
// ---------------------------------------------------------------------------

import { useMemo, useState } from 'react'
import { Bot, Plus, Search, X } from 'lucide-react'
import type { AgentItem } from '../../api'

export interface PaletteProps {
  /** the agent currently being edited — never listed (no self-reference) */
  currentAgentId: string
  agents: AgentItem[]
  onNewInlineNode: () => void
  /** false collapses the column to zero width; the canvas takes the space */
  open?: boolean
}

/**
 * Derives a backend-valid node id (`^[a-z][a-z0-9_]{0,63}$`) from an agent
 * name: lowercase, spaces become underscores, everything else is dropped.
 * Collisions get a numeric suffix.
 */
export function deriveNodeId(baseName: string, existingIds: Set<string>): string {
  let slug = baseName
    .toLowerCase()
    .replace(/\s+/g, '_')
    .replace(/[^a-z0-9_]/g, '')
    .slice(0, 64)
  if (!/^[a-z]/.test(slug)) slug = `node_${slug}`.slice(0, 64)
  if (slug.length === 0) slug = 'node'
  let candidate = slug
  let n = 2
  while (existingIds.has(candidate)) {
    const suffix = `_${n++}`
    candidate = slug.slice(0, 64 - suffix.length) + suffix
  }
  return candidate
}

export function Palette({ currentAgentId, agents, onNewInlineNode, open = true }: PaletteProps) {
  const [search, setSearch] = useState('')

  const publishedOthers = useMemo(
    () => agents.filter((a) => a.id !== currentAgentId && a.active_revision),
    [agents, currentAgentId]
  )

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase()
    if (!query) return publishedOthers
    return publishedOthers.filter(
      (a) =>
        a.name.toLowerCase().includes(query) ||
        (a.description || '').toLowerCase().includes(query)
    )
  }, [publishedOthers, search])

  return (
    <aside
      className={`editor-palette ${open ? 'open' : ''}`}
      aria-label="Node palette"
      aria-hidden={!open}
      inert={!open ? true : undefined}
    >
      <div className="palette-header">
        <div className="palette-search">
          <Search size={14} aria-hidden="true" />
          <input
            type="text"
            placeholder="Search agents…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search agents"
          />
          {search && (
            <button
              type="button"
              className="palette-search-clear"
              onClick={() => setSearch('')}
              aria-label="Clear search"
            >
              <X size={12} aria-hidden="true" />
            </button>
          )}
        </div>
        <button type="button" className="btn palette-new-inline" onClick={onNewInlineNode}>
          <Plus size={13} aria-hidden="true" />
          New inline node
        </button>
      </div>

      <div className="palette-section-label">Published agents</div>
      <div className="palette-list">
        {filtered.length === 0 ? (
          <p className="hint palette-empty">
            {search ? 'No matching agents.' : 'No other published agents yet — publish one, then drag it here as a node.'}
          </p>
        ) : (
          filtered.map((agent) => {
            const revision = agent.active_revision
            if (!revision) return null
            return (
              <div
                key={agent.id}
                className="palette-item"
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData(
                    'application/reactflow',
                    JSON.stringify({ kind: 'agent', mode: 'ref', agentRevisionId: revision.id })
                  )
                  e.dataTransfer.effectAllowed = 'move'
                }}
                title={`${agent.name} — drag onto the canvas`}
              >
                <span className="palette-item-icon" aria-hidden="true">
                  <Bot size={14} />
                </span>
                <span className="palette-item-copy">
                  <span className="palette-item-name">{agent.name}</span>
                  <span className="palette-item-meta">Rev #{revision.revision_number} · drag to add</span>
                </span>
              </div>
            )
          })
        )}
      </div>
    </aside>
  )
}
