import { useEffect, useState } from 'react'
import type { McpAuthMode, McpConnection, McpRemoteTool, McpTransport } from '../api'
import { api } from '../api'

interface ConnectionTools {
  tools: McpRemoteTool[]
  error: string | null
  loading: boolean
}

const TRANSPORT_LABELS: Record<McpTransport, string> = {
  streamable_http: 'Streamable HTTP',
  sse: 'Legacy HTTP+SSE',
  stdio: 'Local stdio',
}

function parseArgs(value: string): string[] {
  return value
    .split(/\s+/)
    .map((part) => part.trim())
    .filter(Boolean)
}

function parseEnv(value: string): Record<string, string> {
  const env: Record<string, string> = {}
  for (const line of value.split(/\r?\n/)) {
    const trimmed = line.trim()
    if (!trimmed) continue
    const eq = trimmed.indexOf('=')
    if (eq <= 0) continue
    const key = trimmed.slice(0, eq).trim()
    const val = trimmed.slice(eq + 1)
    env[key] = val
  }
  return env
}

function connectionTarget(connection: McpConnection): string {
  if (connection.server_url) return connection.server_url
  if (connection.command) return [connection.command, ...(connection.args || [])].join(' ')
  return '—'
}

export function McpServersView() {
  const [connections, setConnections] = useState<McpConnection[]>([])
  const [connectionName, setConnectionName] = useState('')
  const [transport, setTransport] = useState<McpTransport>('streamable_http')
  const [authMode, setAuthMode] = useState<McpAuthMode>('oauth')
  const [connectionUrl, setConnectionUrl] = useState('')
  const [stdioCommand, setStdioCommand] = useState('')
  const [stdioArgs, setStdioArgs] = useState('')
  const [stdioEnv, setStdioEnv] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const [toolsByConnection, setToolsByConnection] = useState<Record<string, ConnectionTools>>({})
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)

  const isStdio = transport === 'stdio'

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
      setNotice(result === 'connected' ? 'MCP server connected.' : `MCP authorization failed: ${params.get('reason') || 'unknown error'}`)
      window.history.replaceState(null, '', window.location.pathname)
    }
    loadData()
  }, [])

  function handleTransportChange(next: McpTransport) {
    setTransport(next)
    // OAuth is only supported with streamable HTTP; stdio never uses it.
    if (next !== 'streamable_http') setAuthMode('none')
  }

  async function handleConnect(event: React.FormEvent) {
    event.preventDefault()
    try {
      setSubmitting(true)
      setFormError(null)
      setNotice(null)
      const { authorization_url } = await api.createMcpConnection({
        name: connectionName.trim(),
        transport,
        auth: authMode,
        ...(isStdio
          ? { command: stdioCommand.trim(), args: parseArgs(stdioArgs), env: parseEnv(stdioEnv) }
          : { server_url: connectionUrl.trim() }),
      })
      if (authorization_url) {
        window.location.assign(authorization_url)
        return
      }
      setNotice('MCP server connected.')
      setConnectionName('')
      setConnectionUrl('')
      setStdioCommand('')
      setStdioArgs('')
      setStdioEnv('')
      await loadData()
    } catch (connectError) {
      setFormError(connectError instanceof Error ? connectError.message : 'Failed to connect')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleReauthorize(id: string) {
    try {
      const { authorization_url } = await api.authorizeMcpConnection(id)
      if (authorization_url) window.location.assign(authorization_url)
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
            <p className="subtext">Register MCP servers over streamable HTTP (with or without OAuth), legacy HTTP+SSE, or a local stdio command, then browse the tools they expose.</p>
          </div>
          <span className="catalog-count">{connections.length} {connections.length === 1 ? 'server' : 'servers'}</span>
        </div>

        {error ? <div className="alert-box error"><p>{error}</p></div> : null}
        {formError ? <div className="alert-box error"><p>{formError}</p></div> : null}
        {notice ? <div className="alert-box" role="status"><p>{notice}</p></div> : null}

        <details className="create-tool-panel" open={connections.length === 0}>
          <summary>Register MCP server</summary>
          <form onSubmit={handleConnect} className="form-grid tool-form">
            <div className="tool-form-columns">
              <div className="form-row">
                <label htmlFor="mcp-connection-name">Server name</label>
                <input id="mcp-connection-name" value={connectionName} onChange={(event) => setConnectionName(event.target.value)} placeholder="notion" required />
              </div>
              <div className="form-row">
                <label htmlFor="mcp-connection-transport">Transport</label>
                <select id="mcp-connection-transport" value={transport} onChange={(event) => handleTransportChange(event.target.value as McpTransport)}>
                  {Object.entries(TRANSPORT_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </div>
            </div>
            {!isStdio ? (
              <div className="tool-form-columns">
                <div className="form-row">
                  <label htmlFor="mcp-connection-url">MCP server URL</label>
                  <input id="mcp-connection-url" type="url" value={connectionUrl} onChange={(event) => setConnectionUrl(event.target.value)} placeholder="https://mcp.notion.com/mcp" required />
                </div>
                <div className="form-row">
                  <label htmlFor="mcp-connection-auth">Authentication</label>
                  <select id="mcp-connection-auth" value={authMode} onChange={(event) => setAuthMode(event.target.value as McpAuthMode)} disabled={transport !== 'streamable_http'}>
                    <option value="oauth">OAuth 2.0</option>
                    <option value="none">None</option>
                  </select>
                  {transport !== 'streamable_http' ? <span className="hint">OAuth is only available for streamable HTTP; this transport connects without authentication.</span> : null}
                </div>
              </div>
            ) : (
              <div className="tool-form-columns">
                <div className="form-row">
                  <label htmlFor="mcp-connection-command">Command</label>
                  <input id="mcp-connection-command" value={stdioCommand} onChange={(event) => setStdioCommand(event.target.value)} placeholder="npx" required />
                  <span className="hint">Executable allowed by the server's stdio command allowlist.</span>
                </div>
                <div className="form-row">
                  <label htmlFor="mcp-connection-args">Arguments (space-separated)</label>
                  <input id="mcp-connection-args" value={stdioArgs} onChange={(event) => setStdioArgs(event.target.value)} placeholder="-y @modelcontextprotocol/server-filesystem ." />
                </div>
                <div className="form-row">
                  <label htmlFor="mcp-connection-env">Environment variables (one KEY=value per line)</label>
                  <textarea id="mcp-connection-env" rows={3} value={stdioEnv} onChange={(event) => setStdioEnv(event.target.value)} placeholder="GITHUB_TOKEN=ghp_..." className="schema-editor" />
                  <span className="hint">Values are encrypted at rest and never displayed again.</span>
                </div>
              </div>
            )}
            <div className="action-buttons">
              <button type="submit" className="btn primary" disabled={submitting}>
                {isStdio || authMode === 'none' ? 'Add MCP server' : 'Connect with OAuth'}
              </button>
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
                      <span className="risk-badge risk-low">{TRANSPORT_LABELS[connection.transport] || connection.transport}</span>
                    </div>
                    <p>{connectionTarget(connection)}</p>
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
                    ) : connection.auth === 'oauth' ? (
                      <button type="button" className="btn small primary" onClick={() => handleReauthorize(connection.id)}>Authorize</button>
                    ) : null}
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
