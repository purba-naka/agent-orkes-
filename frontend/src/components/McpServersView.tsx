import { useEffect, useState } from 'react'
import type { McpConnection, McpRemoteTool } from '../api'
import { api } from '../api'

interface ConnectionTools {
  tools: McpRemoteTool[]
  error: string | null
  loading: boolean
}

export function McpServersView() {
  const [connections, setConnections] = useState<McpConnection[]>([])
  const [connectionName, setConnectionName] = useState('')
  const [connectionUrl, setConnectionUrl] = useState('')
  const [oauthNotice, setOauthNotice] = useState<string | null>(null)
  const [toolsByConnection, setToolsByConnection] = useState<Record<string, ConnectionTools>>({})
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)

  async function loadData() {
    try {
      setConnections(await api.listMcpConnections())
      setError(null)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to load MCP servers')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const result = params.get('mcp_oauth')
    if (result) {
      setOauthNotice(result === 'connected' ? 'MCP server connected.' : `MCP authorization failed: ${params.get('reason') || 'unknown error'}`)
      window.history.replaceState(null, '', window.location.pathname)
    }
    loadData()
  }, [])

  async function handleConnect(event: React.FormEvent) {
    event.preventDefault()
    try {
      setSubmitting(true)
      setFormError(null)
      const { authorization_url } = await api.createMcpConnection({
        name: connectionName.trim(),
        server_url: connectionUrl.trim(),
      })
      window.location.assign(authorization_url)
    } catch (connectError) {
      setFormError(connectError instanceof Error ? connectError.message : 'Failed to connect')
      setSubmitting(false)
    }
  }

  async function handleReauthorize(id: string) {
    try {
      const { authorization_url } = await api.authorizeMcpConnection(id)
      window.location.assign(authorization_url)
    } catch (authError) {
      setFormError(authError instanceof Error ? authError.message : 'Failed to authorize')
    }
  }

  async function handleDeleteConnection(id: string) {
    if (!window.confirm('Remove this MCP server? Tools using it will stop working.')) return
    try {
      await api.deleteMcpConnection(id)
      setToolsByConnection((prev) => {
        const next = { ...prev }
        delete next[id]
        return next
      })
      await loadData()
    } catch (deleteError) {
      setFormError(deleteError instanceof Error ? deleteError.message : 'Failed to delete')
    }
  }

  function toggleTools(connection: McpConnection) {
    const existing = toolsByConnection[connection.id]
    if (existing) {
      setToolsByConnection((prev) => {
        const next = { ...prev }
        delete next[connection.id]
        return next
      })
      return
    }
    if (connection.status !== 'connected') return
    setToolsByConnection((prev) => ({ ...prev, [connection.id]: { tools: [], error: null, loading: true } }))
    api.listMcpTools(connection.id)
      .then((tools) => {
        setToolsByConnection((prev) => ({ ...prev, [connection.id]: { tools, error: null, loading: false } }))
      })
      .catch((listError) => {
        setToolsByConnection((prev) => ({
          ...prev,
          [connection.id]: { tools: [], error: listError instanceof Error ? listError.message : 'Failed to list tools', loading: false },
        }))
      })
  }

  return (
    <div className="view-container tools-view">
      <div className="card">
        <div className="catalog-heading">
          <div>
            <h2>MCP Servers</h2>
            <p className="subtext">Register remote MCP servers with OAuth, then browse the tools they expose.</p>
          </div>
          <span className="catalog-count">{connections.length} {connections.length === 1 ? 'server' : 'servers'}</span>
        </div>

        {error ? <div className="alert-box error"><p>{error}</p></div> : null}
        {formError ? <div className="alert-box error"><p>{formError}</p></div> : null}
        {oauthNotice ? <div className="alert-box" role="status"><p>{oauthNotice}</p></div> : null}

        <details className="create-tool-panel" open={connections.length === 0}>
          <summary>Register MCP server</summary>
          <form onSubmit={handleConnect} className="form-grid tool-form">
            <div className="tool-form-columns">
              <div className="form-row">
                <label htmlFor="mcp-connection-name">Server name</label>
                <input id="mcp-connection-name" value={connectionName} onChange={(event) => setConnectionName(event.target.value)} placeholder="notion" required />
              </div>
              <div className="form-row">
                <label htmlFor="mcp-connection-url">MCP server URL</label>
                <input id="mcp-connection-url" type="url" value={connectionUrl} onChange={(event) => setConnectionUrl(event.target.value)} placeholder="https://mcp.notion.com/mcp" required />
              </div>
            </div>
            <div className="action-buttons">
              <button type="submit" className="btn primary" disabled={submitting}>Connect with OAuth</button>
            </div>
          </form>
        </details>

        <div className="catalog-divider" />
        <h3>Registered servers</h3>
        {loading ? (
          <p className="hint">Loading MCP servers...</p>
        ) : connections.length === 0 ? (
          <div className="empty-state">
            <strong>No MCP servers registered</strong>
            <span>Register a server above to discover its tools for use in agent graphs.</span>
          </div>
        ) : (
          <div className="tool-list">
            {connections.map((connection) => {
              const connectionTools = toolsByConnection[connection.id]
              return (
                <article key={connection.id} className="tool-row">
                  <div className="tool-row-main">
                    <div className="tool-title-line">
                      <h4>{connection.name}</h4>
                      <span className={`risk-badge risk-${connection.status === 'connected' ? 'low' : 'high'}`}>{connection.status}</span>
                    </div>
                    <p>{connection.server_url}</p>
                    {connectionTools ? (
                      connectionTools.loading ? (
                        <p className="hint">Loading tools...</p>
                      ) : connectionTools.error ? (
                        <div className="alert-box error"><p>{connectionTools.error}</p></div>
                      ) : connectionTools.tools.length === 0 ? (
                        <p className="hint">This server exposes no tools.</p>
                      ) : (
                        <div className="tool-metadata">
                          {connectionTools.tools.map((tool) => (
                            <span key={tool.name} title={tool.description || undefined}>{tool.title || tool.name}</span>
                          ))}
                        </div>
                      )
                    ) : null}
                  </div>
                  <div className="action-buttons">
                    {connection.status === 'connected' ? (
                      <button type="button" className="btn small" onClick={() => toggleTools(connection)} aria-expanded={Boolean(connectionTools)}>
                        {connectionTools ? 'Hide tools' : 'Show tools'}
                      </button>
                    ) : (
                      <button type="button" className="btn small primary" onClick={() => handleReauthorize(connection.id)}>Authorize</button>
                    )}
                    <button type="button" className="btn small" onClick={() => handleDeleteConnection(connection.id)}>Remove</button>
                  </div>
                </article>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
