import React, { useEffect, useMemo, useState } from 'react'
import { Bot, Network, Plus, Search, X } from 'lucide-react'
import type { AgentItem, ModelItem, ToolItem } from '../api'
import { api } from '../api'
import { EditorShell } from './agent-editor/EditorShell'

function isOrchestration(agent: AgentItem): boolean {
  const doc = agent.draft?.document || agent.active_revision?.document
  if (!doc) return false
  const nodeCount = (doc.nodes || []).length
  const edgeCount = (doc.edges || []).length
  return nodeCount > 1 || edgeCount > 0
}

export interface AgentsViewProps {
  editingAgentId: string | null
  onEditAgent: (id: string) => void
  onExitEditor: () => void
  theme: 'light' | 'dark'
}

export function AgentsView({ editingAgentId, onEditAgent, onExitEditor, theme }: AgentsViewProps) {
  const [agents, setAgents] = useState<AgentItem[]>([])
  const [models, setModels] = useState<ModelItem[]>([])
  const [tools, setTools] = useState<ToolItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Creation form state
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('You are a helpful assistant.')
  const [selectedModelRevId, setSelectedModelRevId] = useState<string>('')
  const [creating, setCreating] = useState(false)
  const [search, setSearch] = useState('')
  const [showCreateForm, setShowCreateForm] = useState(false)

  // Editor agent record (fetched fresh so draft/published state is current)
  const [editingAgent, setEditingAgent] = useState<AgentItem | null>(null)
  const [editorError, setEditorError] = useState<string | null>(null)

  async function loadData() {
    try {
      setLoading(true)
      const [agentList, modelList, toolList] = await Promise.all([
        api.listAgents(),
        api.listModels(),
        api.listTools(),
      ])
      setAgents(agentList)
      setModels(modelList)
      setTools(toolList)
      if (modelList.length > 0 && !selectedModelRevId) {
        const firstActive = modelList.find((m) => m.active_revision_id)
        if (firstActive?.active_revision_id) {
          setSelectedModelRevId(firstActive.active_revision_id)
        }
      }
      setError(null)
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  // Fetch the full agent record when an edit session starts.
  useEffect(() => {
    if (!editingAgentId) {
      setEditingAgent(null)
      setEditorError(null)
      return
    }
    let isMounted = true
    setEditingAgent(null)
    setEditorError(null)
    api
      .getAgent(editingAgentId)
      .then((agent) => {
        if (isMounted) setEditingAgent(agent)
      })
      .catch((err: any) => {
        if (isMounted) setEditorError(err.message || 'Failed to load agent')
      })
    return () => {
      isMounted = false
    }
  }, [editingAgentId])

  function exitEditor() {
    onExitEditor()
    // Draft versions / published badges in the list may have changed.
    loadData()
  }

  function handleAgentChanged() {
    // Refresh the list (and the agent record's published state) without
    // tearing down the editor — EditorShell is keyed by id, not identity.
    loadData()
    if (editingAgentId) {
      api
        .getAgent(editingAgentId)
        .then((agent) => setEditingAgent(agent))
        .catch(() => {
          // Best effort: the topbar badge may go stale until re-entry.
        })
    }
  }

  async function handleCreateAgent(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    try {
      setCreating(true)
      const created = await api.createAgent({
        name,
        description,
        system_prompt: systemPrompt,
        model_revision_id: selectedModelRevId || null,
      })
      setName('')
      setDescription('')
      await loadData()
      onEditAgent(created.id)
    } catch (err: any) {
      alert(`Error creating agent: ${err.message}`)
    } finally {
      setCreating(false)
    }
  }

  const filteredAgents = useMemo(() => {
    const query = search.trim().toLowerCase()
    if (!query) return agents
    return agents.filter((a) =>
      a.name.toLowerCase().includes(query)
      || (a.description || '').toLowerCase().includes(query))
  }, [agents, search])

  const singleAgents = filteredAgents.filter((a) => !isOrchestration(a))
  const orchestrations = filteredAgents.filter((a) => isOrchestration(a))

  function renderAgentCard(a: AgentItem) {
    const doc = a.draft?.document || a.active_revision?.document
    const nodeCount = (doc?.nodes || []).length
    const edgeCount = (doc?.edges || []).length
    return (
      <div
        key={a.id}
        className="agent-card"
        onClick={() => onEditAgent(a.id)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onEditAgent(a.id)
          }
        }}
      >
        <div className="agent-card-header">
          <h4>{a.name}</h4>
          <span className={`status-badge ${a.active_revision_id ? 'active' : 'disabled'}`}>
            {a.active_revision_id ? `Rev #${a.active_revision?.revision_number || 1}` : 'Draft Only'}
          </span>
        </div>
        <p className="agent-desc">{a.description || 'No description'}</p>
        <div className="agent-card-meta">
          <span>{isOrchestration(a) ? `${nodeCount} nodes / ${edgeCount} edges` : 'Single agent'}</span>
          <span>Draft v{a.draft?.version || 1}</span>
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------------
  // Editor mode: full-bleed canvas editor (rail + palette + canvas + panel)
  // ---------------------------------------------------------------------
  if (editingAgentId) {
    if (editorError) {
      return (
        <div className="view-container">
          <div className="card">
            <div className="alert-box error"><p>{editorError}</p></div>
            <button type="button" className="btn" onClick={exitEditor}>
              Back to all agents
            </button>
          </div>
        </div>
      )
    }
    if (!editingAgent) {
      return (
        <div className="editor-loading">
          <p className="hint">Loading agent…</p>
        </div>
      )
    }
    return (
      <EditorShell
        key={editingAgent.id}
        agent={editingAgent}
        models={models}
        tools={tools}
        agents={agents}
        onExit={exitEditor}
        onAgentChanged={handleAgentChanged}
        theme={theme}
      />
    )
  }

  // ---------------------------------------------------------------------
  // List mode: agents + orchestrations overview
  // ---------------------------------------------------------------------
  return (
    <div className="view-container">
      <div className="card">
        <h2>Agent Definitions & Execution</h2>
        <p className="subtext">
          Single-node and multi-agent topologies compiled via LangChain create_agent() and StateGraph.
        </p>

        {error && <div className="alert-box error"><p>{error}</p></div>}

        <div className="agents-toolbar">
          <div className="agents-search">
            <Search size={14} aria-hidden="true" />
            <input
              type="text"
              placeholder="Search agents and orchestrations..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {search && (
              <button
                type="button"
                className="agents-search-clear"
                onClick={() => setSearch('')}
                aria-label="Clear search"
              >
                <X size={12} aria-hidden="true" />
              </button>
            )}
          </div>
          <button
            type="button"
            className="btn primary"
            onClick={() => setShowCreateForm(!showCreateForm)}
          >
            <Plus size={14} aria-hidden="true" />
            {showCreateForm ? 'Close form' : 'New Agent'}
          </button>
        </div>

        {showCreateForm && (
          <form onSubmit={handleCreateAgent} className="form-grid">
            <h3>Create New Agent</h3>
            <div className="form-row">
              <label>Agent Name</label>
              <input
                type="text"
                placeholder="e.g. general-research-agent"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>
            <div className="form-row">
              <label>Description</label>
              <input
                type="text"
                placeholder="e.g. Single-node inline researcher"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
            <div className="form-row">
              <label>Initial Model</label>
              <select
                value={selectedModelRevId}
                onChange={(e) => setSelectedModelRevId(e.target.value)}
              >
                <option value="">-- Select Model --</option>
                {models.map((m) => {
                  const rev = m.active_revision
                  return (
                    <option key={m.id} value={rev?.id || ''} disabled={!rev}>
                      {m.name} ({rev ? `${rev.provider}/${rev.model_name}` : 'No active revision'})
                    </option>
                  )
                })}
              </select>
            </div>
            <div className="form-row">
              <label>System Prompt</label>
              <textarea
                rows={3}
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                placeholder="## Role..."
                className="prompt-area"
              />
            </div>
            <button type="submit" disabled={creating} className="btn primary">
              {creating ? 'Creating...' : 'Create Agent'}
            </button>
          </form>
        )}

        {loading ? (
          <p>Loading agents...</p>
        ) : (
          <>
            <div className="agents-section">
              <div className="agents-section-head">
                <Bot size={15} aria-hidden="true" />
                <h3>Agents</h3>
                <span className="agents-section-count">{singleAgents.length}</span>
              </div>
              {singleAgents.length === 0 ? (
                <p className="hint">{search ? 'No matching agents.' : 'No agents created yet.'}</p>
              ) : (
                <div className="agents-grid">
                  {singleAgents.map(renderAgentCard)}
                </div>
              )}
            </div>

            <div className="agents-section">
              <div className="agents-section-head">
                <Network size={15} aria-hidden="true" />
                <h3>Orchestrations</h3>
                <span className="agents-section-count">{orchestrations.length}</span>
              </div>
              {orchestrations.length === 0 ? (
                <p className="hint">{search ? 'No matching orchestrations.' : 'No orchestrations yet. Add nodes in the topology editor to build one.'}</p>
              ) : (
                <div className="agents-grid">
                  {orchestrations.map(renderAgentCard)}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
