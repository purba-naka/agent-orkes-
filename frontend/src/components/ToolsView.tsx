import React, { useEffect, useState } from 'react'
import type {
  Credential,
  KnowledgeBase,
  ModelItem,
  ToolItem,
  ToolKind,
  ToolRevision,
  ToolRevisionCreate,
  ToolRiskLevel,
} from '../api'
import { api } from '../api'

type ToolFormState = {
  name: string
  kind: ToolKind
  description: string
  inputSchema: string
  outputSchema: string
  riskLevel: ToolRiskLevel
  isMutating: boolean
  maxAttempts: string
  implementationKey: string
  implementationVersion: string
  method: string
  url: string
  serverUrl: string
  remoteToolName: string
  credentialId: string
  credentialHeader: string
  timeoutSeconds: string
  idempotencyHeader: string
  headers: string
  knowledgeBaseId: string
  embeddingModelRevisionId: string
  topK: string
}

const OBJECT_SCHEMA = '{\n  "type": "object",\n  "properties": {}\n}'

function emptyForm(): ToolFormState {
  return {
    name: '',
    kind: 'code',
    description: '',
    inputSchema: OBJECT_SCHEMA,
    outputSchema: OBJECT_SCHEMA,
    riskLevel: 'low',
    isMutating: false,
    maxAttempts: '3',
    implementationKey: '',
    implementationVersion: '1',
    method: 'GET',
    url: '',
    serverUrl: '',
    remoteToolName: '',
    credentialId: '',
    credentialHeader: 'Authorization',
    timeoutSeconds: '30',
    idempotencyHeader: '',
    headers: '{}',
    knowledgeBaseId: '',
    embeddingModelRevisionId: '',
    topK: '5',
  }
}

function prettyJson(value: Record<string, unknown>) {
  return JSON.stringify(value, null, 2)
}

function parseObject(value: string, label: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value)
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON object`)
  }
  return parsed as Record<string, unknown>
}

function configurationFromForm(form: ToolFormState): Record<string, unknown> {
  if (form.kind === 'code') {
    if (!form.implementationKey.trim() || !form.implementationVersion.trim()) {
      throw new Error('Code tools require an implementation key and version')
    }
    return {
      implementation_key: form.implementationKey.trim(),
      implementation_version: form.implementationVersion.trim(),
    }
  }

  if (form.kind === 'retrieval') {
    const topK = Number.parseInt(form.topK, 10)
    if (!form.knowledgeBaseId || !form.embeddingModelRevisionId) {
      throw new Error('Retrieval tools require a knowledge base and embedding model revision')
    }
    if (!Number.isInteger(topK) || topK < 1 || topK > 10) {
      throw new Error('Retrieval top K must be between 1 and 10')
    }
    return {
      knowledge_base_id: form.knowledgeBaseId,
      embedding_model_revision_id: form.embeddingModelRevisionId,
      top_k: topK,
    }
  }

  const timeoutSeconds = Number.parseInt(form.timeoutSeconds, 10)

  if (form.kind === 'http') {
    if (!form.url.trim()) throw new Error('HTTP tools require a URL template')
    return {
      method: form.method,
      url: form.url.trim(),
      headers: parseObject(form.headers, 'Headers'),
      credential_headers: form.credentialId
        ? { [form.credentialHeader.trim() || 'Authorization']: form.credentialId }
        : {},
      timeout_seconds: timeoutSeconds,
      idempotency_header: form.idempotencyHeader.trim() || null,
    }
  }

  if (!form.serverUrl.trim() || !form.remoteToolName.trim()) {
    throw new Error('MCP tools require a server URL and remote tool name')
  }
  return {
    server_url: form.serverUrl.trim(),
    remote_tool_name: form.remoteToolName.trim(),
    credential_headers: form.credentialId
      ? { [form.credentialHeader.trim() || 'Authorization']: form.credentialId }
      : {},
    timeout_seconds: timeoutSeconds,
    idempotency_header: form.idempotencyHeader.trim() || null,
    transport: 'streamable_http',
  }
}

function formFromRevision(tool: ToolItem, revision: ToolRevision): ToolFormState {
  const config = revision.configuration || {}
  return {
    ...emptyForm(),
    name: tool.name,
    kind: revision.kind,
    description: revision.description,
    inputSchema: prettyJson(revision.input_schema),
    outputSchema: prettyJson(revision.output_schema),
    riskLevel: revision.risk_level,
    isMutating: revision.is_mutating,
    maxAttempts: String(revision.max_attempts),
    implementationKey: String(config.implementation_key || ''),
    implementationVersion: String(config.implementation_version || '1'),
    method: String(config.method || 'GET'),
    url: String(config.url || config.url_template || ''),
    serverUrl: String(config.server_url || ''),
    remoteToolName: String(config.tool_name || config.remote_tool_name || ''),
    credentialId: String(Object.values((config.credential_headers as Record<string, unknown>) || {})[0] || ''),
    credentialHeader: String(Object.keys((config.credential_headers as Record<string, unknown>) || {})[0] || 'Authorization'),
    timeoutSeconds: String(config.timeout_seconds || 30),
    idempotencyHeader: String(config.idempotency_header || ''),
    headers: prettyJson((config.headers as Record<string, unknown>) || {}),
    knowledgeBaseId: String(config.knowledge_base_id || ''),
    embeddingModelRevisionId: String(config.embedding_model_revision_id || ''),
    topK: String(config.top_k || 5),
  }
}

interface ToolFormProps {
  form: ToolFormState
  credentials: Credential[]
  knowledgeBases: KnowledgeBase[]
  models: ModelItem[]
  submitLabel: string
  submitting: boolean
  onChange: (next: ToolFormState) => void
  onSubmit: (event: React.FormEvent) => void
  onCancel?: () => void
  showName: boolean
}

function ToolForm({
  form,
  credentials,
  knowledgeBases,
  models,
  submitLabel,
  submitting,
  onChange,
  onSubmit,
  onCancel,
  showName,
}: ToolFormProps) {
  const set = <K extends keyof ToolFormState>(key: K, value: ToolFormState[K]) => {
    onChange({ ...form, [key]: value })
  }

  return (
    <form onSubmit={onSubmit} className="form-grid tool-form">
      {showName ? (
        <div className="form-row">
          <label htmlFor="tool-name">Tool name</label>
          <input
            id="tool-name"
            value={form.name}
            onChange={(event) => set('name', event.target.value)}
            placeholder="e.g. send-email"
            required
          />
        </div>
      ) : null}

      <div className="tool-form-columns">
        <div className="form-row">
          <label htmlFor={`${submitLabel}-kind`}>Kind</label>
          <select
            id={`${submitLabel}-kind`}
            value={form.kind}
            onChange={(event) => set('kind', event.target.value as ToolKind)}
          >
            <option value="code">Code registry</option>
            <option value="http">HTTP JSON</option>
            <option value="mcp">MCP (Streamable HTTP)</option>
            <option value="retrieval">Retrieval</option>
          </select>
        </div>
        <div className="form-row">
          <label htmlFor={`${submitLabel}-risk`}>Risk level</label>
          <select
            id={`${submitLabel}-risk`}
            value={form.riskLevel}
            onChange={(event) => set('riskLevel', event.target.value as ToolRiskLevel)}
          >
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </div>
      </div>

      <div className="form-row">
        <label htmlFor={`${submitLabel}-description`}>Description</label>
        <textarea
          id={`${submitLabel}-description`}
          rows={2}
          value={form.description}
          onChange={(event) => set('description', event.target.value)}
          placeholder="What this tool does and when it should be used"
          className="prompt-area"
          required
        />
      </div>

      <div className="tool-config-section">
        <h4>Configuration</h4>
        {form.kind === 'code' ? (
          <div className="tool-form-columns">
            <div className="form-row">
              <label htmlFor={`${submitLabel}-implementation-key`}>Implementation key</label>
              <input
                id={`${submitLabel}-implementation-key`}
                value={form.implementationKey}
                onChange={(event) => set('implementationKey', event.target.value)}
                placeholder="clock.now"
                required
              />
            </div>
            <div className="form-row">
              <label htmlFor={`${submitLabel}-implementation-version`}>Implementation version</label>
              <input
                id={`${submitLabel}-implementation-version`}
                value={form.implementationVersion}
                onChange={(event) => set('implementationVersion', event.target.value)}
                placeholder="1"
                required
              />
            </div>
          </div>
        ) : form.kind === 'http' ? (
          <>
            <div className="tool-form-columns tool-form-columns-narrow">
              <div className="form-row">
                <label htmlFor={`${submitLabel}-method`}>Method</label>
                <select id={`${submitLabel}-method`} value={form.method} onChange={(event) => set('method', event.target.value)}>
                  {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((method) => <option key={method}>{method}</option>)}
                </select>
              </div>
              <div className="form-row">
                <label htmlFor={`${submitLabel}-url`}>URL template</label>
                <input
                  id={`${submitLabel}-url`}
                  type="url"
                  value={form.url}
                  onChange={(event) => set('url', event.target.value)}
                  placeholder="https://api.example.com/items/{id}"
                  required
                />
              </div>
            </div>
            <div className="tool-form-columns">
              <div className="form-row">
                <label htmlFor={`${submitLabel}-headers`}>Static headers (JSON)</label>
                <textarea id={`${submitLabel}-headers`} rows={3} value={form.headers} onChange={(event) => set('headers', event.target.value)} className="schema-editor" />
              </div>
              <div className="form-row">
                <label htmlFor={`${submitLabel}-idempotency`}>Idempotency header (optional)</label>
                <input id={`${submitLabel}-idempotency`} value={form.idempotencyHeader} onChange={(event) => set('idempotencyHeader', event.target.value)} placeholder="Idempotency-Key" />
                <span className="hint">Recommended for mutating tools with retries.</span>
              </div>
            </div>
          </>
        ) : form.kind === 'mcp' ? (
          <div className="tool-form-columns">
            <div className="form-row">
              <label htmlFor={`${submitLabel}-server-url`}>MCP server URL</label>
              <input id={`${submitLabel}-server-url`} type="url" value={form.serverUrl} onChange={(event) => set('serverUrl', event.target.value)} placeholder="https://mcp.example.com/mcp" required />
            </div>
            <div className="form-row">
              <label htmlFor={`${submitLabel}-remote-name`}>Remote tool name</label>
              <input id={`${submitLabel}-remote-name`} value={form.remoteToolName} onChange={(event) => set('remoteToolName', event.target.value)} placeholder="lookup_customer" required />
            </div>
          </div>
        ) : (
          <div className="tool-form-columns">
            <div className="form-row">
              <label htmlFor={`${submitLabel}-knowledge-base`}>Knowledge base</label>
              <select id={`${submitLabel}-knowledge-base`} value={form.knowledgeBaseId} onChange={(event) => set('knowledgeBaseId', event.target.value)} required>
                <option value="">Select a knowledge base</option>
                {knowledgeBases.map((knowledgeBase) => <option key={knowledgeBase.id} value={knowledgeBase.id}>{knowledgeBase.name}</option>)}
              </select>
            </div>
            <div className="form-row">
              <label htmlFor={`${submitLabel}-embedding-model`}>Embedding model revision</label>
              <select id={`${submitLabel}-embedding-model`} value={form.embeddingModelRevisionId} onChange={(event) => set('embeddingModelRevisionId', event.target.value)} required>
                <option value="">Select a model revision</option>
                {models.flatMap((model) => model.revisions.map((revision) => (
                  <option key={revision.id} value={revision.id} disabled={!revision.is_enabled}>{model.name} · Rev #{revision.revision_number} · {revision.model_name}</option>
                )))}
              </select>
            </div>
            <div className="form-row compact-field">
              <label htmlFor={`${submitLabel}-top-k`}>Top K</label>
              <input id={`${submitLabel}-top-k`} type="number" min="1" max="10" value={form.topK} onChange={(event) => set('topK', event.target.value)} required />
              <span className="hint">Return between 1 and 10 relevant chunks.</span>
            </div>
          </div>
        )}

        {form.kind === 'http' || form.kind === 'mcp' ? (
          <div className="tool-form-columns">
            <div className="form-row">
              <label htmlFor={`${submitLabel}-credential`}>Credential</label>
              <select id={`${submitLabel}-credential`} value={form.credentialId} onChange={(event) => set('credentialId', event.target.value)}>
                <option value="">None</option>
                {credentials.map((credential) => (
                  <option key={credential.id} value={credential.id}>{credential.name} (••••{credential.last_four})</option>
                ))}
              </select>
            </div>
            <div className="form-row">
              <label htmlFor={`${submitLabel}-credential-header`}>Credential header</label>
              <input id={`${submitLabel}-credential-header`} value={form.credentialHeader} onChange={(event) => set('credentialHeader', event.target.value)} placeholder="Authorization" disabled={!form.credentialId} />
            </div>
            <div className="form-row">
              <label htmlFor={`${submitLabel}-timeout`}>Timeout (seconds)</label>
              <input id={`${submitLabel}-timeout`} type="number" min="1" max="300" value={form.timeoutSeconds} onChange={(event) => set('timeoutSeconds', event.target.value)} required />
            </div>

          </div>
        ) : null}
      </div>

      <div className="tool-form-columns">
        <div className="form-row">
          <label htmlFor={`${submitLabel}-input-schema`}>Input schema</label>
          <textarea id={`${submitLabel}-input-schema`} rows={9} value={form.inputSchema} onChange={(event) => set('inputSchema', event.target.value)} className="schema-editor" spellCheck={false} />
        </div>
        <div className="form-row">
          <label htmlFor={`${submitLabel}-output-schema`}>Output schema</label>
          <textarea id={`${submitLabel}-output-schema`} rows={9} value={form.outputSchema} onChange={(event) => set('outputSchema', event.target.value)} className="schema-editor" spellCheck={false} />
        </div>
      </div>

      <div className="tool-policy-row">
        <label className="checkbox-field">
          <input type="checkbox" checked={form.isMutating} onChange={(event) => set('isMutating', event.target.checked)} />
          <span><strong>Mutating</strong><small>Changes external state</small></span>
        </label>
        <div className="form-row compact-field">
          <label htmlFor={`${submitLabel}-attempts`}>Maximum attempts</label>
          <input id={`${submitLabel}-attempts`} type="number" min="1" max="10" value={form.maxAttempts} onChange={(event) => set('maxAttempts', event.target.value)} required />
        </div>
        {form.isMutating && Number(form.maxAttempts) > 1 && !form.idempotencyHeader.trim() && (form.kind === 'http' || form.kind === 'mcp') ? (
          <p className="policy-warning">Mutating network tools need an idempotency header when maximum attempts is greater than one.</p>
        ) : null}
      </div>

      <div className="action-buttons">
        <button type="submit" disabled={submitting} className="btn primary">
          {submitting ? 'Saving...' : submitLabel}
        </button>
        {onCancel ? <button type="button" onClick={onCancel} className="btn">Cancel</button> : null}
      </div>
    </form>
  )
}

function createPayload(form: ToolFormState): ToolRevisionCreate {
  const maxAttempts = Number.parseInt(form.maxAttempts, 10)
  if (!Number.isInteger(maxAttempts) || maxAttempts < 1) throw new Error('Maximum attempts must be at least 1')
  return {
    kind: form.kind,
    description: form.description.trim(),
    input_schema: parseObject(form.inputSchema, 'Input schema'),
    output_schema: parseObject(form.outputSchema, 'Output schema'),
    configuration: configurationFromForm(form),
    risk_level: form.riskLevel,
    is_mutating: form.isMutating,
    max_attempts: maxAttempts,
  }
}

export function ToolsView() {
  const [tools, setTools] = useState<ToolItem[]>([])
  const [credentials, setCredentials] = useState<Credential[]>([])
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([])
  const [models, setModels] = useState<ModelItem[]>([])
  const [form, setForm] = useState<ToolFormState>(emptyForm)
  const [revisionTool, setRevisionTool] = useState<ToolItem | null>(null)
  const [revisionForm, setRevisionForm] = useState<ToolFormState>(emptyForm)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)

  async function loadData() {
    try {
      const [toolList, credentialList, knowledgeBaseList, modelList] = await Promise.all([
        api.listTools(),
        api.listCredentials(),
        api.listKnowledgeBases(),
        api.listModels(),
      ])
      setTools(toolList)
      setCredentials(credentialList.filter((credential) => credential.is_enabled))
      setKnowledgeBases(knowledgeBaseList)
      setModels(modelList)
      setError(null)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Failed to load tool catalog')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault()
    if (!form.name.trim()) return
    try {
      setSubmitting(true)
      setFormError(null)
      await api.createTool({ name: form.name.trim(), revision: createPayload(form) })
      setForm(emptyForm())
      await loadData()
    } catch (submitError) {
      setFormError(submitError instanceof Error ? submitError.message : 'Failed to create tool')
    } finally {
      setSubmitting(false)
    }
  }

  function openRevision(tool: ToolItem) {
    const active = tool.active_revision || tool.revisions?.find((revision) => revision.id === tool.active_revision_id)
    setRevisionTool(tool)
    setRevisionForm(active ? formFromRevision(tool, active) : { ...emptyForm(), name: tool.name })
    setFormError(null)
  }

  async function handleCreateRevision(event: React.FormEvent) {
    event.preventDefault()
    if (!revisionTool) return
    try {
      setSubmitting(true)
      setFormError(null)
      await api.createToolRevision(revisionTool.id, createPayload(revisionForm))
      setRevisionTool(null)
      await loadData()
    } catch (submitError) {
      setFormError(submitError instanceof Error ? submitError.message : 'Failed to create revision')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="view-container tools-view">
      <div className="card">
        <div className="catalog-heading">
          <div>
            <h2>Tool Catalog</h2>
            <p className="subtext">Register deterministic integrations and pin immutable configuration revisions.</p>
          </div>
          <span className="catalog-count">{tools.length} {tools.length === 1 ? 'tool' : 'tools'}</span>
        </div>

        {error ? <div className="alert-box error"><p>{error}</p></div> : null}
        {formError ? <div className="alert-box error"><p>{formError}</p></div> : null}

        <details className="create-tool-panel" open={tools.length === 0}>
          <summary>Register new tool</summary>
          <ToolForm form={form} credentials={credentials} knowledgeBases={knowledgeBases} models={models} submitLabel="Register tool" submitting={submitting} onChange={setForm} onSubmit={handleCreate} showName />
        </details>

        <div className="catalog-divider" />
        <h3>Registered tools</h3>
        {loading ? (
          <p className="hint">Loading tool catalog...</p>
        ) : tools.length === 0 ? (
          <div className="empty-state">
            <strong>No tools registered</strong>
            <span>Add a code, HTTP, MCP, or retrieval tool to make it available to agent graphs.</span>
          </div>
        ) : (
          <div className="tool-list">
            {tools.map((tool) => {
              const active = tool.active_revision || tool.revisions?.find((revision) => revision.id === tool.active_revision_id)
              return (
                <article key={tool.id} className="tool-row">
                  <div className="tool-row-main">
                    <div className="tool-title-line">
                      <h4>{tool.name}</h4>
                      <span className={`risk-badge risk-${active?.risk_level || 'low'}`}>{active?.risk_level || 'unknown'} risk</span>
                      {active?.is_mutating ? <span className="mutating-badge">Mutating</span> : null}
                    </div>
                    <p>{active?.description || 'No active revision description.'}</p>
                    <div className="tool-metadata">
                      <span>{active?.kind || 'unconfigured'}</span>
                      <span>Revision #{active?.revision_number || 0}</span>
                      <span>{active?.max_attempts || 1} max {active?.max_attempts === 1 ? 'attempt' : 'attempts'}</span>
                      <span>{tool.revisions?.length || 0} total revisions</span>
                    </div>
                  </div>
                  <button type="button" onClick={() => openRevision(tool)} className="btn small primary">New revision</button>
                </article>
              )
            })}
          </div>
        )}

        {revisionTool ? (
          <section className="revision-editor" aria-labelledby="revision-editor-title">
            <div className="revision-editor-heading">
              <div>
                <span className="eyebrow">Immutable revision</span>
                <h3 id="revision-editor-title">Configure {revisionTool.name}</h3>
              </div>
              <button type="button" className="btn small" onClick={() => setRevisionTool(null)}>Close</button>
            </div>
            <ToolForm
              form={revisionForm}
              credentials={credentials}
              knowledgeBases={knowledgeBases}
              models={models}
              submitLabel="Create revision"
              submitting={submitting}
              onChange={setRevisionForm}
              onSubmit={handleCreateRevision}
              onCancel={() => setRevisionTool(null)}
              showName={false}
            />
          </section>
        ) : null}
      </div>
    </div>
  )
}
