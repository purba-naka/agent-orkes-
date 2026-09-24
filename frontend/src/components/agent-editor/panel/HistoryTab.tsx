// ---------------------------------------------------------------------------
// History tab — the agent's published revisions (GET /agents/{id}/revisions).
// Each row: revision number, created time, content_hash truncated to 10
// characters. The revision matching agent.active_revision_id is marked.
// ---------------------------------------------------------------------------

import { useEffect, useState } from 'react'
import { Clock, GitCommitVertical } from 'lucide-react'
import type { AgentItem, AgentRevision } from '../../../api'
import { api } from '../../../api'

export interface HistoryTabProps {
  agent: AgentItem
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

export function HistoryTab({ agent }: HistoryTabProps) {
  const [revisions, setRevisions] = useState<AgentRevision[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let isMounted = true
    setLoading(true)
    api
      .listAgentRevisions(agent.id)
      .then((revs) => {
        if (isMounted) {
          setRevisions(revs)
          setError(null)
        }
      })
      .catch((err: any) => {
        if (isMounted) setError(err.message || 'Failed to load revisions')
      })
      .finally(() => {
        if (isMounted) setLoading(false)
      })
    return () => {
      isMounted = false
    }
  }, [agent.id])

  return (
    <div className="history-tab">
      {loading ? (
        <p className="hint">Loading revisions…</p>
      ) : error ? (
        <div className="alert-box error"><p>{error}</p></div>
      ) : revisions.length === 0 ? (
        <p className="hint">
          No published revisions yet. Click <strong>Publish</strong> in the top bar to
          create one.
        </p>
      ) : (
        <ul className="history-list">
          {revisions.map((rev) => {
            const isActive = rev.id === agent.active_revision_id
            return (
              <li key={rev.id} className={`history-row ${isActive ? 'active' : ''}`}>
                <span className="history-icon" aria-hidden="true">
                  <GitCommitVertical size={14} />
                </span>
                <span className="history-copy">
                  <span className="history-title">
                    Revision #{rev.revision_number}
                    {isActive && <span className="editor-badge badge-published">active</span>}
                  </span>
                  <span className="history-meta">
                    <Clock size={11} aria-hidden="true" />
                    {formatTime(rev.created_at)} · {rev.content_hash.slice(0, 10)}
                  </span>
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
