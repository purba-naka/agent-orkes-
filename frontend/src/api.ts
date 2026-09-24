export interface Credential {
  id: string
  name: string
  kind: string
  last_four: string
  is_enabled: boolean
  created_at: string
  updated_at: string
}

export interface ModelRevision {
  id: string
  model_id: string
  revision_number: number
  provider: string
  model_name: string
  base_url?: string | null
  api_key_id?: string | null
  parameters: Record<string, any>
  routing?: Record<string, any> | null
  context_window?: number | null
  is_enabled: boolean
  created_at: string
}

export interface ModelItem {
  id: string
  name: string
  active_revision_id: string | null
  created_at: string
  updated_at: string
  active_revision?: ModelRevision | null
  revisions: ModelRevision[]
}

export interface ModelTestResult {
  status: 'connected' | 'failed'
  model_id: string
  revision_id: string | null
  revision_number: number | null
  latency_ms: number | null
  response_preview: string | null
  error: string | null
}

export type ToolKind = 'code' | 'http' | 'mcp' | 'retrieval'
export type ToolRiskLevel = 'low' | 'medium' | 'high'

export interface KnowledgeBase {
  id: string
  name: string
  embedding_model_revision_id: string
  created_at: string
}

export interface KnowledgeDocument {
  id: string
  knowledge_base_id: string
  title: string
  source_uri?: string | null
  metadata: Record<string, any>
  chunk_count?: number
  created_at: string
}

export interface KnowledgeDocumentCreate {
  title: string
  content: string
  source_uri?: string | null
  metadata?: Record<string, any>
}

export interface ToolRevision {
  id: string
  tool_id: string
  revision_number: number
  kind: ToolKind
  description: string
  input_schema: Record<string, any>
  output_schema: Record<string, any>
  configuration: Record<string, any>
  risk_level: ToolRiskLevel
  is_mutating: boolean
  max_attempts: number
  is_enabled: boolean
  created_at: string
}

export interface McpConnection {
  id: string
  name: string
  server_url: string
  status: 'pending' | 'connected' | 'needs_reauth'
  scope: string | null
  expires_at: string | null
  created_at: string
  updated_at: string
}

export interface McpRemoteTool {
  name: string
  title: string | null
  description: string | null
  input_schema: Record<string, any>
  output_schema: Record<string, any> | null
}

export interface McpAuthorization {
  connection: McpConnection
  authorization_url: string
}

export interface ToolItem {
  id: string
  name: string
  active_revision_id: string | null
  created_at: string
  updated_at: string
  active_revision?: ToolRevision | null
  revisions: ToolRevision[]
}

export interface ToolRevisionCreate {
  kind: ToolKind
  description: string
  input_schema: Record<string, any>
  output_schema: Record<string, any>
  configuration: Record<string, any>
  risk_level: ToolRiskLevel
  is_mutating: boolean
  max_attempts: number
}

export interface Diagnostic {
  code: string
  path: string
  message: string
  severity: 'error' | 'warning'
}

export interface AgentDraft {
  agent_id: string
  document: Record<string, any>
  validation: Diagnostic[]
  version: number
  updated_at: string
}

export interface AgentRevision {
  id: string
  agent_id: string
  revision_number: number
  document: Record<string, any>
  dependency_manifest: Record<string, any>
  content_hash: string
  created_at: string
}

export interface AgentItem {
  id: string
  name: string
  description: string
  active_revision_id: string | null
  created_at: string
  updated_at: string
  active_revision?: AgentRevision | null
  draft?: AgentDraft | null
}

export interface RunInterrupt {
  id: string
  run_id: string
  interrupt_id: string
  kind: 'tool_approval' | 'node_review'
  payload: Record<string, any>
  status: 'pending' | 'resolved'
  decision?: Record<string, any> | null
  created_at: string
  resolved_at?: string | null
}

export interface RunItem {
  id: string
  agent_revision_id: string
  mode: string
  status: string
  result_name?: string | null
  result?: Record<string, any> | null
  usage: Record<string, any>
  sanitized_error?: Record<string, any> | null
  started_at: string
  finished_at?: string | null
  interrupts: RunInterrupt[]
}

export interface MessageItem {
  id: string
  conversation_id: string
  run_id: string
  langgraph_message_id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: any
  sequence: number
  created_at: string
}

export interface ConversationItem {
  id: string
  agent_revision_id: string
  title: string
  parent_conversation_id?: string | null
  upgrade_summary?: string | null
  created_at: string
  updated_at: string
}

export interface ConversationDetail extends ConversationItem {
  agent_id?: string | null
  agent_name?: string | null
  revision_number?: number | null
  messages: MessageItem[]
  runs: RunItem[]
}

/**
 * Thrown when PUT /agents/{id}/draft is rejected with 409 draft_conflict.
 * Carries the server's current version + draft so the editor can offer an
 * explicit reload-or-overwrite choice. No automatic merging — merging two
 * graph topologies automatically can produce a document nobody intended.
 */
export class DraftConflictError extends Error {
  currentVersion: number
  currentDraft: Record<string, any>

  constructor(currentVersion: number, currentDraft: Record<string, any>) {
    super('Draft conflict')
    this.name = 'DraftConflictError'
    this.currentVersion = currentVersion
    this.currentDraft = currentDraft
  }
}

/**
 * Thrown when POST /agents/{id}/publish fails validation (422). Carries the
 * full diagnostics list so badges and the floating diagnostics panel can
 * render exactly what is wrong instead of a vanishing toast.
 */
export class PublishValidationError extends Error {
  diagnostics: Diagnostic[]

  constructor(diagnostics: Diagnostic[]) {
    super('Publish validation failed')
    this.name = 'PublishValidationError'
    this.diagnostics = diagnostics
  }
}

export const api = {
  async listCredentials(): Promise<Credential[]> {
    const res = await fetch('/api/v1/credentials')
    if (!res.ok) throw new Error('Failed to fetch credentials')
    return res.json()
  },

  async createCredential(data: { name: string; kind: string; secret: string }): Promise<Credential> {
    const res = await fetch('/api/v1/credentials', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to create credential')
    }
    return res.json()
  },

  async updateCredential(id: string, secret: string): Promise<Credential> {
    const res = await fetch(`/api/v1/credentials/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ secret }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to update credential')
    }
    return res.json()
  },

  async disableCredential(id: string): Promise<Credential> {
    const res = await fetch(`/api/v1/credentials/${id}/disable`, {
      method: 'POST',
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to disable credential')
    }
    return res.json()
  },

  async listModels(): Promise<ModelItem[]> {
    const res = await fetch('/api/v1/models')
    if (!res.ok) throw new Error('Failed to fetch models')
    return res.json()
  },

  async getModel(id: string): Promise<ModelItem> {
    const res = await fetch(`/api/v1/models/${id}`)
    if (!res.ok) throw new Error('Failed to fetch model')
    return res.json()
  },

  async createModel(data: {
    name: string
    revision: {
      provider: string
      model_name: string
      base_url?: string | null
      api_key_id?: string | null
      parameters?: Record<string, any>
    }
  }): Promise<ModelItem> {
    const res = await fetch('/api/v1/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to create model')
    }
    return res.json()
  },

  async createModelRevision(
    modelId: string,
    data: {
      provider: string
      model_name: string
      base_url?: string | null
      api_key_id?: string | null
      parameters?: Record<string, any>
    }
  ): Promise<ModelRevision> {
    const res = await fetch(`/api/v1/models/${modelId}/revisions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to create model revision')
    }
    return res.json()
  },

  async testModel(modelId: string): Promise<ModelTestResult> {
    const res = await fetch(`/api/v1/models/${modelId}/test`, {
      method: 'POST',
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to test model')
    }
    return res.json()
  },

  async listKnowledgeBases(): Promise<KnowledgeBase[]> {
    const res = await fetch('/api/v1/knowledge-bases')
    if (!res.ok) throw new Error('Failed to fetch knowledge bases')
    return res.json()
  },

  async createKnowledgeBase(data: {
    name: string
    embedding_model_revision_id: string
  }): Promise<KnowledgeBase> {
    const res = await fetch('/api/v1/knowledge-bases', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to create knowledge base')
    }
    return res.json()
  },

  async listKnowledgeDocuments(knowledgeBaseId: string): Promise<KnowledgeDocument[]> {
    const res = await fetch(`/api/v1/knowledge-bases/${knowledgeBaseId}/documents`)
    if (!res.ok) throw new Error('Failed to fetch documents')
    return res.json()
  },

  async createKnowledgeDocument(
    knowledgeBaseId: string,
    data: KnowledgeDocumentCreate
  ): Promise<KnowledgeDocument> {
    const res = await fetch(`/api/v1/knowledge-bases/${knowledgeBaseId}/documents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to ingest document')
    }
    return res.json()
  },

  async deleteKnowledgeDocument(knowledgeBaseId: string, documentId: string): Promise<void> {
    const res = await fetch(`/api/v1/knowledge-bases/${knowledgeBaseId}/documents/${documentId}`, {
      method: 'DELETE',
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to delete document')
    }
  },

  async listMcpConnections(): Promise<McpConnection[]> {
    const res = await fetch('/api/v1/mcp-connections')
    if (!res.ok) throw new Error('Failed to fetch MCP connections')
    return res.json()
  },

  async createMcpConnection(data: { name: string; server_url: string }): Promise<McpAuthorization> {
    const res = await fetch('/api/v1/mcp-connections', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to create MCP connection')
    }
    return res.json()
  },

  async listMcpTools(id: string): Promise<McpRemoteTool[]> {
    const res = await fetch(`/api/v1/mcp-connections/${id}/tools`)
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to list MCP tools')
    }
    return res.json()
  },

  async authorizeMcpConnection(id: string): Promise<McpAuthorization> {
    const res = await fetch(`/api/v1/mcp-connections/${id}/authorize`, { method: 'POST' })
    if (!res.ok) throw new Error('Failed to start MCP authorization')
    return res.json()
  },

  async deleteMcpConnection(id: string): Promise<void> {
    const res = await fetch(`/api/v1/mcp-connections/${id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error('Failed to delete MCP connection')
  },

  async listTools(): Promise<ToolItem[]> {
    const res = await fetch('/api/v1/tools')
    if (!res.ok) throw new Error('Failed to fetch tools')
    return res.json()
  },

  async getTool(id: string): Promise<ToolItem> {
    const res = await fetch(`/api/v1/tools/${id}`)
    if (!res.ok) throw new Error('Failed to fetch tool')
    return res.json()
  },

  async createTool(data: { name: string; revision: ToolRevisionCreate }): Promise<ToolItem> {
    const res = await fetch('/api/v1/tools', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to create tool')
    }
    return res.json()
  },

  async createToolRevision(toolId: string, data: ToolRevisionCreate): Promise<ToolRevision> {
    const res = await fetch(`/api/v1/tools/${toolId}/revisions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to create tool revision')
    }
    return res.json()
  },

  async listAgents(): Promise<AgentItem[]> {
    const res = await fetch('/api/v1/agents')
    if (!res.ok) throw new Error('Failed to fetch agents')
    return res.json()
  },

  async getAgent(id: string): Promise<AgentItem> {
    const res = await fetch(`/api/v1/agents/${id}`)
    if (!res.ok) throw new Error('Failed to fetch agent')
    return res.json()
  },

  async createAgent(data: {
    name: string
    description?: string
    system_prompt?: string
    model_revision_id?: string | null
  }): Promise<AgentItem> {
    const res = await fetch('/api/v1/agents', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed to create agent')
    }
    return res.json()
  },

  async updateAgentDraft(
    agentId: string,
    version: number,
    document: Record<string, any>
  ): Promise<AgentDraft> {
    const res = await fetch(`/api/v1/agents/${agentId}/draft`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ version, document }),
    })
    if (!res.ok) {
      if (res.status === 409) {
        const err = await res.json().catch(() => ({}))
        throw new DraftConflictError(
          err.detail?.current_version ?? 0,
          err.detail?.current_draft ?? null
        )
      }
      const err = await res.json().catch(() => ({}))
      throw new Error(
        typeof err.detail === 'object' ? JSON.stringify(err.detail) : err.detail || 'Draft conflict / save error'
      )
    }
    return res.json()
  },

  async listAgentRevisions(agentId: string): Promise<AgentRevision[]> {
    const res = await fetch(`/api/v1/agents/${agentId}/revisions`)
    if (!res.ok) throw new Error('Failed to fetch revisions')
    return res.json()
  },

  async publishAgent(agentId: string): Promise<AgentRevision> {
    const res = await fetch(`/api/v1/agents/${agentId}/publish`, {
      method: 'POST',
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      if (res.status === 422 && Array.isArray(err.detail?.diagnostics)) {
        throw new PublishValidationError(err.detail.diagnostics)
      }
      const msg = err.detail?.diagnostics
        ? err.detail.diagnostics.map((d: any) => `[${d.code}] ${d.message}`).join('; ')
        : err.detail || 'Publish failed'
      throw new Error(msg)
    }
    return res.json()
  },

  async runAgentStream(
    agentId: string,
    input: Record<string, any>,
    onEvent: (event: string, data: any) => void
  ): Promise<void> {
    const res = await fetch(`/api/v1/agents/${agentId}/runs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail || 'Run failed to start')
    }
    const reader = res.body?.getReader()
    if (!reader) throw new Error('No readable body in stream')

    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      let currentEvent = ''
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ') && currentEvent) {
          try {
            const parsed = JSON.parse(line.slice(6).trim())
            onEvent(currentEvent, parsed)
          } catch {
            // ignore malformed JSON chunk
          }
          currentEvent = ''
        }
      }
    }
  },

  async getRun(runId: string): Promise<RunItem> {
    const res = await fetch(`/api/v1/runs/${runId}`)
    if (!res.ok) throw new Error('Failed to fetch run')
    return res.json()
  },

  async resumeRunStream(
    runId: string,
    decision: Record<string, any>,
    onEvent: (event: string, data: any) => void
  ): Promise<void> {
    const res = await fetch(`/api/v1/runs/${runId}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(decision),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail?.message || err.detail || 'Resume failed')
    }
    const reader = res.body?.getReader()
    if (!reader) throw new Error('No readable body in stream')
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const events = buffer.split('\n\n')
      buffer = events.pop() || ''
      for (const block of events) {
        const event = block.split('\n').find((line) => line.startsWith('event: '))?.slice(7).trim()
        const data = block.split('\n').find((line) => line.startsWith('data: '))?.slice(6).trim()
        if (event && data) onEvent(event, JSON.parse(data))
      }
    }
  },

  async listConversations(agentId?: string): Promise<ConversationItem[]> {
    const url = agentId ? `/api/v1/conversations?agent_id=${agentId}` : '/api/v1/conversations'
    const res = await fetch(url)
    if (!res.ok) throw new Error('Failed to fetch conversations')
    return res.json()
  },

  async getConversation(id: string): Promise<ConversationDetail> {
    const res = await fetch(`/api/v1/conversations/${id}`)
    if (!res.ok) throw new Error('Failed to fetch conversation')
    return res.json()
  },

  async createConversation(agentId: string, title?: string): Promise<ConversationItem> {
    const res = await fetch('/api/v1/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: agentId, title: title || '' }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail?.error || err.detail || 'Failed to create conversation')
    }
    return res.json()
  },

  async upgradeConversation(id: string, summary?: string): Promise<ConversationItem> {
    const res = await fetch(`/api/v1/conversations/${id}/upgrade`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ summary: summary || null }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail?.error || err.detail || 'Failed to upgrade conversation')
    }
    return res.json()
  },

  async rebuildConversationProjections(id: string): Promise<{ conversation_id: string; rebuilt_count: number }> {
    const res = await fetch(`/api/v1/conversations/${id}/rebuild-projections`, {
      method: 'POST',
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail?.error || err.detail || 'Failed to rebuild projections')
    }
    return res.json()
  },

  async sendConversationMessageStream(
    conversationId: string,
    content: any,
    onEvent: (event: string, data: any) => void
  ): Promise<void> {
    const payload = typeof content === 'string' ? { text: content } : { content }
    const res = await fetch(`/api/v1/conversations/${conversationId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err.detail?.error || err.detail || 'Message send failed')
    }
    const reader = res.body?.getReader()
    if (!reader) throw new Error('No readable body in stream')

    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      let currentEvent = ''
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ') && currentEvent) {
          try {
            const parsed = JSON.parse(line.slice(6).trim())
            onEvent(currentEvent, parsed)
          } catch {
            // ignore malformed JSON chunk
          }
          currentEvent = ''
        }
      }
    }
  },
}
