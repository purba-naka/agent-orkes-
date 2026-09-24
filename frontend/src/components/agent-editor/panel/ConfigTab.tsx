// ---------------------------------------------------------------------------
// Config tab — accordion form for the selected node.
//
// MOVED from AgentInspector.tsx (do not rewrite from scratch — the merge
// semantics were already correct). What changed: it edits the SELECTED node
// instead of always the root node, so context_policy / middleware_policy
// now patch node-level fields (matching the document schema, where these
// live inside each node).
//
// Backend allowlists (validation.py) — keys outside these lists are
// rejected at publish:
//   context_policy:     memory, knowledge_top_k, upstream, max_chars, max_item_chars
//   middleware_policy:  summarization, context_editing, pii, tool_approval,
//                       tool_selection, model_call_limit, tool_call_limit, model_retry
// ---------------------------------------------------------------------------

import { createContext, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ChevronDown, Cpu, Database, Flag, Gauge, PenLine, ScrollText, Trash2, Wrench, X } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ModelItem, ToolItem } from '../../../api'
import { parseFieldPathMapping } from './EdgeEditor'

type SummarizationUnit = 'fraction' | 'tokens'
type PiiMode = 'disabled' | 'redact' | 'mask' | 'hash' | 'block'

const DEFAULT_CONTEXT_POLICY = {
  memory: false,
  knowledge_top_k: 0,
  upstream: [] as string[],
  max_chars: 12000,
}

const DEFAULT_MIDDLEWARE_POLICY = {
  model_call_limit: { run_limit: 12, thread_limit: 40 },
  tool_call_limit: { run_limit: 24, thread_limit: 80 },
  model_retry: { max_retries: 2 },
  summarization: {
    enabled: false,
    trigger: { type: 'fraction' as SummarizationUnit, value: 0.7 },
    keep: { type: 'fraction' as SummarizationUnit, value: 0.3 },
  },
  context_editing: { enabled: false },
  pii: {
    enabled: false,
    strategy: 'redact' as Exclude<PiiMode, 'disabled'>,
    apply_to_input: true,
    apply_to_output: true,
    apply_to_tool_results: true,
  },
  tool_selection: { enabled: false, max_tools: 8 },
}

function positiveInteger(value: string, fallback: number, minimum = 1) {
  const parsed = Number.parseInt(value, 10)
  return Number.isFinite(parsed) ? Math.max(minimum, parsed) : fallback
}

function numericValue(value: string, fallback: number, minimum: number, maximum?: number) {
  const parsed = Number.parseFloat(value)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(maximum ?? parsed, Math.max(minimum, parsed))
}

// ---------------------------------------------------------------------------
// Shared selected-node access
// ---------------------------------------------------------------------------

interface ConfigContextValue {
  node: Record<string, any>
  document: Record<string, any>
  models: ModelItem[]
  tools: ToolItem[]
  /** replaces the selected node with a patched copy */
  updateNode: (patch: Record<string, any>) => void
}

const ConfigContext = createContext<ConfigContextValue | null>(null)

function useConfig() {
  const value = useContext(ConfigContext)
  if (!value) throw new Error('useConfig must be used inside ConfigTab')
  return value
}

function patchNode(doc: Record<string, any>, nodeId: string, nodePatch: Record<string, any>) {
  return {
    ...doc,
    nodes: (doc.nodes || []).map((n: any) => (n.id === nodeId ? { ...n, ...nodePatch } : n)),
  }
}

// ---------------------------------------------------------------------------
// Section chrome (collapsible) — moved from AgentInspector
// ---------------------------------------------------------------------------

function Section({ icon: Icon, title, meta, defaultOpen, children }: {
  icon: LucideIcon
  title: string
  meta?: string
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen ?? false)
  return (
    <div className={`ai-section ${open ? 'open' : ''}`}>
      <button
        type="button"
        className="ai-section-header"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span className="ai-section-icon" aria-hidden="true"><Icon size={14} /></span>
        <span className="ai-section-title">{title}</span>
        {meta && <span className="ai-section-meta">{meta}</span>}
        <ChevronDown size={14} className="ai-chevron" aria-hidden="true" />
      </button>
      {open && <div className="ai-section-body">{children}</div>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Policy helpers — same merge semantics as the previous inspector
// ---------------------------------------------------------------------------

function usePolicies() {
  const { node, models, tools, updateNode } = useConfig()
  const document = { context_policy: node.context_policy, middleware_policy: node.middleware_policy }

  const contextPolicy = { ...DEFAULT_CONTEXT_POLICY, ...(document.context_policy || {}) }
  const middlewarePolicy = {
    ...DEFAULT_MIDDLEWARE_POLICY,
    ...(document.middleware_policy || {}),
    model_call_limit: {
      ...DEFAULT_MIDDLEWARE_POLICY.model_call_limit,
      ...(document.middleware_policy?.model_call_limit || {}),
    },
    tool_call_limit: {
      ...DEFAULT_MIDDLEWARE_POLICY.tool_call_limit,
      ...(document.middleware_policy?.tool_call_limit || {}),
    },
    model_retry: {
      ...DEFAULT_MIDDLEWARE_POLICY.model_retry,
      ...(document.middleware_policy?.model_retry || {}),
    },
    summarization: {
      ...DEFAULT_MIDDLEWARE_POLICY.summarization,
      ...(document.middleware_policy?.summarization || {}),
      trigger: {
        ...DEFAULT_MIDDLEWARE_POLICY.summarization.trigger,
        ...(document.middleware_policy?.summarization?.trigger || {}),
      },
      keep: {
        ...DEFAULT_MIDDLEWARE_POLICY.summarization.keep,
        ...(document.middleware_policy?.summarization?.keep || {}),
      },
    },
    context_editing: {
      ...DEFAULT_MIDDLEWARE_POLICY.context_editing,
      ...(document.middleware_policy?.context_editing || {}),
    },
    pii: {
      ...DEFAULT_MIDDLEWARE_POLICY.pii,
      ...(document.middleware_policy?.pii || {}),
    },
    tool_selection: {
      ...DEFAULT_MIDDLEWARE_POLICY.tool_selection,
      ...(document.middleware_policy?.tool_selection || {}),
    },
  }

  const nodes: Array<Record<string, any>> = useConfigNodes()
  const upstreamCandidates = nodes.filter((n) => n.id !== node.id)
  const modelRevisionId = node.agent?.model_revision_id
  const model = models.find((m) => m.revisions.some((r) => r.id === modelRevisionId))
  const modelRevision = model?.revisions.find((r) => r.id === modelRevisionId)
  const enabledToolCount = tools.filter((tool) => tool.active_revision?.is_enabled).length

  function updateContextPolicy(patch: Record<string, any>) {
    updateNode({ context_policy: { ...contextPolicy, ...patch } })
  }

  function updateMiddlewarePolicy(section: string, patch: Record<string, any>) {
    updateNode({
      middleware_policy: {
        ...(node.middleware_policy || {}),
        [section]: { ...(middlewarePolicy as Record<string, any>)[section], ...patch },
      },
    })
  }

  function updateSummarizationPart(part: 'trigger' | 'keep', patch: Record<string, any>) {
    updateMiddlewarePolicy('summarization', {
      [part]: { ...middlewarePolicy.summarization[part], ...patch },
    })
  }

  function toggleUpstreamNode(nodeId: string, checked: boolean) {
    const selected = new Set<string>(contextPolicy.upstream || [])
    if (checked) selected.add(nodeId)
    else selected.delete(nodeId)
    updateContextPolicy({
      upstream: upstreamCandidates.map((n) => n.id).filter((id) => selected.has(id)),
    })
  }

  return {
    upstreamCandidates,
    modelRevision,
    enabledToolCount,
    contextPolicy,
    middlewarePolicy,
    updateContextPolicy,
    updateMiddlewarePolicy,
    updateSummarizationPart,
    toggleUpstreamNode,
  }
}

function useConfigNodes(): Array<Record<string, any>> {
  const { document } = useConfig()
  return document.nodes || []
}

// ---------------------------------------------------------------------------
// Field sections
// ---------------------------------------------------------------------------

function PromptSection() {
  const { node, updateNode } = useConfig()
  if (node.kind === 'tool') return null
  return (
    <Section icon={PenLine} title="System prompt" meta={node.id} defaultOpen>
      <div className="form-row">
        <label htmlFor="cfg-system-prompt">Node instruction</label>
        <textarea
          id="cfg-system-prompt"
          className="prompt-area"
          rows={8}
          value={node.agent?.system_prompt || ''}
          onChange={(event) => updateNode({ agent: { ...node.agent, system_prompt: event.target.value } })}
        />
        <span className="hint">Markdown supported. Applies to this node only.</span>
      </div>
    </Section>
  )
}

function ModelSection() {
  const { node, models, updateNode } = useConfig()
  if (node.kind === 'tool') return null
  const revisionId = node.agent?.model_revision_id || ''
  const model = models.find((m) => m.revisions.some((r) => r.id === revisionId))
  const revision = model?.revisions.find((r) => r.id === revisionId)

  return (
    <Section icon={Cpu} title="Model assignment" meta={revision ? revision.provider : undefined}>
      <div className="form-row">
        <label htmlFor="cfg-model-revision">Active model revision</label>
        <select
          id="cfg-model-revision"
          value={revisionId}
          onChange={(event) => updateNode({ agent: { ...node.agent, model_revision_id: event.target.value || null } })}
        >
          <option value="">-- Select Model Revision --</option>
          {models.map((m) => {
            const rev = m.active_revision
            return (
              <option key={m.id} value={rev?.id || ''} disabled={!rev}>
                {m.name} ({rev ? `${rev.provider}/${rev.model_name}` : 'No active revision'})
              </option>
            )
          })}
        </select>
        {revision && (
          <div className="ai-model-meta">
            <span>{revision.provider} / {revision.model_name}</span>
            <span>
              {revision.context_window
                ? `${revision.context_window.toLocaleString()} token context window`
                : 'Context window unknown'}
            </span>
          </div>
        )}
      </div>
    </Section>
  )
}

// Tools are registered in the Tools tab; this only attaches them. Pinning is
// by revision id, so republishing a tool never silently changes this agent.
function CapabilitiesSection() {
  const { node, tools, updateNode } = useConfig()
  const [search, setSearch] = useState('')
  if (node.kind === 'tool') return null

  const selectedIds: string[] = node.agent?.tool_revision_ids || []

  // A revision is attachable only if it is the tool's active one and enabled —
  // publish validation rejects anything else (validation.py:891).
  const attachable = tools.flatMap((tool) =>
    tool.active_revision?.is_enabled ? [{ tool, revision: tool.active_revision }] : []
  )

  const attached = selectedIds.map((id) => {
    const tool = tools.find((t) => (t.revisions || []).some((r) => r.id === id))
    return { id, tool, revision: tool?.revisions.find((r) => r.id === id) }
  })

  const query = search.trim().toLowerCase()
  const visible = query
    ? attachable.filter((entry) => entry.tool.name.toLowerCase().includes(query))
    : attachable

  function setSelected(ids: string[]) {
    updateNode({ agent: { ...node.agent, tool_revision_ids: ids } })
  }

  function toggle(revisionId: string, checked: boolean) {
    if (checked) setSelected([...selectedIds, revisionId])
    else setSelected(selectedIds.filter((id) => id !== revisionId))
  }

  return (
    <Section
      icon={Wrench}
      title="Capabilities"
      meta={selectedIds.length > 0 ? `${selectedIds.length} tools` : undefined}
      defaultOpen
    >
      <p className="hint">Give this agent the tools it needs to work. Register new tools in the Tools tab.</p>

      {attached.length > 0 && (
        <div className="capability-chips">
          {attached.map(({ id, tool, revision }) => (
            <span key={id} className="capability-chip">
              {tool?.name || 'Unknown tool'}
              {revision && <small>Rev #{revision.revision_number}</small>}
              <button
                type="button"
                onClick={() => toggle(id, false)}
                aria-label={`Detach ${tool?.name || 'tool'}`}
              >
                <X size={11} aria-hidden="true" />
              </button>
            </span>
          ))}
        </div>
      )}

      {attachable.length === 0 ? (
        <p className="hint">No published tools yet. Create one in the Tools tab, then publish a revision.</p>
      ) : (
        <div className="form-row">
          <label htmlFor="cfg-tool-search">Available tools</label>
          <input
            id="cfg-tool-search"
            type="search"
            placeholder="Search tools…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          {visible.length === 0 ? (
            <p className="hint">No tools match that search.</p>
          ) : (
            <div className="checkbox-list ai-upstream-list">
              {visible.map(({ tool, revision }) => (
                <label className="checkbox-field" key={tool.id}>
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(revision.id)}
                    onChange={(event) => toggle(revision.id, event.target.checked)}
                  />
                  <span>
                    {tool.name}
                    <small>
                      {revision.kind} · Rev #{revision.revision_number} ·{' '}
                      {revision.risk_level} risk ·{' '}
                      {revision.is_mutating ? 'mutating' : 'read only'}
                    </small>
                  </span>
                </label>
              ))}
            </div>
          )}
        </div>
      )}
    </Section>
  )
}

function LimitsSection() {
  const { middlewarePolicy, updateMiddlewarePolicy } = usePolicies()
  return (
    <Section icon={Gauge} title="Execution limits" meta="run / thread">
      <p className="hint">Bound model and tool activity per run and across a conversation thread.</p>
      <div className="policy-input-grid">
        <div className="form-row">
          <label htmlFor="cfg-model-run-limit">Model calls / run</label>
          <input
            id="cfg-model-run-limit"
            type="number"
            min={1}
            value={middlewarePolicy.model_call_limit.run_limit}
            onChange={(event) => updateMiddlewarePolicy('model_call_limit', {
              run_limit: positiveInteger(event.target.value, 12),
            })}
          />
        </div>
        <div className="form-row">
          <label htmlFor="cfg-model-thread-limit">Model calls / thread</label>
          <input
            id="cfg-model-thread-limit"
            type="number"
            min={1}
            value={middlewarePolicy.model_call_limit.thread_limit}
            onChange={(event) => updateMiddlewarePolicy('model_call_limit', {
              thread_limit: positiveInteger(event.target.value, 40),
            })}
          />
        </div>
        <div className="form-row">
          <label htmlFor="cfg-tool-run-limit">Tool calls / run</label>
          <input
            id="cfg-tool-run-limit"
            type="number"
            min={1}
            value={middlewarePolicy.tool_call_limit.run_limit}
            onChange={(event) => updateMiddlewarePolicy('tool_call_limit', {
              run_limit: positiveInteger(event.target.value, 24),
            })}
          />
        </div>
        <div className="form-row">
          <label htmlFor="cfg-tool-thread-limit">Tool calls / thread</label>
          <input
            id="cfg-tool-thread-limit"
            type="number"
            min={1}
            value={middlewarePolicy.tool_call_limit.thread_limit}
            onChange={(event) => updateMiddlewarePolicy('tool_call_limit', {
              thread_limit: positiveInteger(event.target.value, 80),
            })}
          />
        </div>
        <div className="form-row">
          <label htmlFor="cfg-model-retries">Model retry attempts</label>
          <input
            id="cfg-model-retries"
            type="number"
            min={1}
            max={10}
            value={middlewarePolicy.model_retry.max_retries}
            onChange={(event) => updateMiddlewarePolicy('model_retry', {
              max_retries: positiveInteger(event.target.value, 2),
            })}
          />
          <span className="hint">Retries after the initial model request.</span>
        </div>
      </div>
    </Section>
  )
}

function ContextSection() {
  const {
    contextPolicy,
    updateContextPolicy,
    upstreamCandidates,
    toggleUpstreamNode,
  } = usePolicies()

  return (
    <Section icon={Database} title="Dynamic context" meta="retrieval & upstream">
      <div className="policy-input-grid">
        <div className="form-row">
          <label htmlFor="cfg-knowledge-top-k">Knowledge top_k</label>
          <input
            id="cfg-knowledge-top-k"
            type="number"
            min={0}
            max={100}
            value={contextPolicy.knowledge_top_k}
            onChange={(event) => updateContextPolicy({
              knowledge_top_k: positiveInteger(event.target.value, 0, 0),
            })}
          />
          <span className="hint">Use 0 when no retrieval source is configured.</span>
        </div>
        <div className="form-row">
          <label htmlFor="cfg-upstream-character-limit">Upstream character limit</label>
          <input
            id="cfg-upstream-character-limit"
            type="number"
            min={256}
            step={256}
            value={contextPolicy.max_chars}
            onChange={(event) => updateContextPolicy({
              max_chars: positiveInteger(event.target.value, 12000, 256),
            })}
          />
          <span className="hint">A strict serialization cap before upstream output enters the prompt.</span>
        </div>
      </div>

      <div className="upstream-selector">
        <span className="field-label">Allowed upstream nodes</span>
        {upstreamCandidates.length === 0 ? (
          <p className="hint">Add another graph node to make upstream context available.</p>
        ) : (
          <div className="checkbox-list ai-upstream-list">
            {upstreamCandidates.map((n) => (
              <label className="checkbox-field" key={n.id}>
                <input
                  type="checkbox"
                  checked={(contextPolicy.upstream || []).includes(n.id)}
                  onChange={(event) => toggleUpstreamNode(n.id, event.target.checked)}
                />
                <span>
                  {n.id}
                  <small>{n.kind === 'agent' ? `${n.agent?.mode || 'inline'} agent` : 'tool node'}</small>
                </span>
              </label>
            ))}
          </div>
        )}
      </div>
    </Section>
  )
}

function LongContextSection() {
  const {
    middlewarePolicy,
    updateMiddlewarePolicy,
    updateSummarizationPart,
    modelRevision,
    enabledToolCount,
  } = usePolicies()

  const piiMode: PiiMode = middlewarePolicy.pii.enabled
    ? middlewarePolicy.pii.strategy || 'redact'
    : 'disabled'
  const summaryTriggerIsRelative = middlewarePolicy.summarization.trigger.type === 'fraction'
  const missingSummaryContextWindow = middlewarePolicy.summarization.enabled
    && summaryTriggerIsRelative
    && !modelRevision?.context_window

  return (
    <Section icon={ScrollText} title="Long-context handling" meta="history & safety">
      <label className="checkbox-field policy-toggle">
        <input
          type="checkbox"
          checked={middlewarePolicy.summarization.enabled}
          onChange={(event) => updateMiddlewarePolicy('summarization', { enabled: event.target.checked })}
        />
        <span>
          Enable summarization
          <small>Summarize conversation history while preserving the full checkpoint.</small>
        </span>
      </label>

      {middlewarePolicy.summarization.enabled && (
        <div className="policy-input-grid policy-reveal">
          <div className="form-row">
            <label htmlFor="cfg-summary-trigger-type">Trigger type</label>
            <select
              id="cfg-summary-trigger-type"
              value={middlewarePolicy.summarization.trigger.type}
              onChange={(event) => {
                const type = event.target.value as SummarizationUnit
                updateSummarizationPart('trigger', { type, value: type === 'fraction' ? 0.7 : 8000 })
              }}
            >
              <option value="fraction">Relative to context window</option>
              <option value="tokens">Absolute approximate tokens</option>
            </select>
          </div>
          <div className="form-row">
            <label htmlFor="cfg-summary-trigger-value">
              Trigger {middlewarePolicy.summarization.trigger.type === 'fraction' ? '(fraction)' : '(tokens)'}
            </label>
            <input
              id="cfg-summary-trigger-value"
              type="number"
              min={middlewarePolicy.summarization.trigger.type === 'fraction' ? 0.05 : 1}
              max={middlewarePolicy.summarization.trigger.type === 'fraction' ? 1 : undefined}
              step={middlewarePolicy.summarization.trigger.type === 'fraction' ? 0.05 : 100}
              value={middlewarePolicy.summarization.trigger.value}
              onChange={(event) => updateSummarizationPart('trigger', {
                value: numericValue(
                  event.target.value,
                  middlewarePolicy.summarization.trigger.type === 'fraction' ? 0.7 : 8000,
                  middlewarePolicy.summarization.trigger.type === 'fraction' ? 0.05 : 1,
                  middlewarePolicy.summarization.trigger.type === 'fraction' ? 1 : undefined,
                ),
              })}
            />
          </div>
          <div className="form-row">
            <label htmlFor="cfg-summary-keep-type">Keep amount type</label>
            <select
              id="cfg-summary-keep-type"
              value={middlewarePolicy.summarization.keep.type}
              onChange={(event) => {
                const type = event.target.value as SummarizationUnit
                updateSummarizationPart('keep', { type, value: type === 'fraction' ? 0.3 : 3000 })
              }}
            >
              <option value="fraction">Relative to context window</option>
              <option value="tokens">Absolute approximate tokens</option>
            </select>
          </div>
          <div className="form-row">
            <label htmlFor="cfg-summary-keep-value">
              Keep {middlewarePolicy.summarization.keep.type === 'fraction' ? '(fraction)' : '(tokens)'}
            </label>
            <input
              id="cfg-summary-keep-value"
              type="number"
              min={middlewarePolicy.summarization.keep.type === 'fraction' ? 0.05 : 1}
              max={middlewarePolicy.summarization.keep.type === 'fraction' ? 1 : undefined}
              step={middlewarePolicy.summarization.keep.type === 'fraction' ? 0.05 : 100}
              value={middlewarePolicy.summarization.keep.value}
              onChange={(event) => updateSummarizationPart('keep', {
                value: numericValue(
                  event.target.value,
                  middlewarePolicy.summarization.keep.type === 'fraction' ? 0.3 : 3000,
                  middlewarePolicy.summarization.keep.type === 'fraction' ? 0.05 : 1,
                  middlewarePolicy.summarization.keep.type === 'fraction' ? 1 : undefined,
                ),
              })}
            />
          </div>
          <p className={`policy-note ${missingSummaryContextWindow ? 'policy-warning' : ''}`} role={missingSummaryContextWindow ? 'alert' : undefined}>
            {modelRevision?.context_window
              ? `Model context window: ${modelRevision.context_window.toLocaleString()} tokens.`
              : summaryTriggerIsRelative
                ? 'This model has no known context window; choose an absolute token trigger before publishing.'
                : 'Absolute token threshold is ready for a model without a known context window.'}
          </p>
        </div>
      )}

      <div className="capability-grid">
        <label className="checkbox-field policy-toggle">
          <input
            type="checkbox"
            checked={middlewarePolicy.context_editing.enabled}
            onChange={(event) => updateMiddlewarePolicy('context_editing', { enabled: event.target.checked })}
          />
          <span>
            Context editing
            <small>Trim stale tool output before model calls.</small>
          </span>
        </label>

        <div className="form-row">
          <label htmlFor="cfg-pii-mode">PII mode</label>
          <select
            id="cfg-pii-mode"
            value={piiMode}
            onChange={(event) => {
              const mode = event.target.value as PiiMode
              updateMiddlewarePolicy('pii', {
                enabled: mode !== 'disabled',
                ...(mode !== 'disabled' ? { strategy: mode } : {}),
              })
            }}
          >
            <option value="disabled">Disabled</option>
            <option value="redact">Redact</option>
            <option value="mask">Mask</option>
            <option value="hash">Hash</option>
            <option value="block">Block</option>
          </select>
          <span className="hint">Applies to input, output, and tool results.</span>
        </div>

        <label className="checkbox-field policy-toggle">
          <input
            type="checkbox"
            checked={middlewarePolicy.tool_selection.enabled}
            onChange={(event) => updateMiddlewarePolicy('tool_selection', { enabled: event.target.checked })}
          />
          <span>
            Large-tool-set selection
            <small>Ask the model to expose only the most relevant tools.</small>
          </span>
        </label>

        <div className="form-row">
          <label htmlFor="cfg-selected-tool-limit">Maximum selected tools</label>
          <input
            id="cfg-selected-tool-limit"
            type="number"
            min={1}
            max={100}
            disabled={!middlewarePolicy.tool_selection.enabled}
            value={middlewarePolicy.tool_selection.max_tools}
            onChange={(event) => updateMiddlewarePolicy('tool_selection', {
              max_tools: positiveInteger(event.target.value, 8),
            })}
          />
          <span className="hint">{enabledToolCount} enabled tool{enabledToolCount === 1 ? '' : 's'} available.</span>
        </div>
      </div>
    </Section>
  )
}

// ---------------------------------------------------------------------------
// Input / output mapping editor for ref nodes
// ---------------------------------------------------------------------------

function MappingSection() {
  const { node, document, updateNode } = useConfig()
  const [error, setError] = useState<string | null>(null)
  const inputMappingText = useMemo(
    () => JSON.stringify(node.agent?.input_mapping || {}, null, 2),
    [node.agent?.input_mapping]
  )
  if (node.agent?.mode !== 'ref') return null

  function commit(text: string, field: 'input_mapping' | 'output_mapping', required: boolean) {
    try {
      const parsed = parseFieldPathMapping(text, field === 'input_mapping' ? 'Input mapping' : 'Output mapping', required)
      updateNode({ agent: { ...node.agent, [field]: parsed } })
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Mapping must be valid JSON')
    }
  }

  return (
    <Section icon={ScrollText} title="Input mapping" meta="field paths">
      <div className="form-row">
        <label htmlFor="cfg-input-mapping">Input mapping (JSON field paths)</label>
        <textarea
          id="cfg-input-mapping"
          rows={5}
          spellCheck={false}
          className="schema-editor"
          defaultValue={inputMappingText}
          onBlur={(event) => commit(event.target.value, 'input_mapping', true)}
        />
        <span className="hint">Maps each child input field to a parent state path, e.g. ["input", "prompt"].</span>
      </div>
      <div className="form-row">
        <label htmlFor="cfg-output-mapping">Output mapping (optional)</label>
        <textarea
          id="cfg-output-mapping"
          rows={3}
          spellCheck={false}
          className="schema-editor"
          defaultValue={JSON.stringify(node.agent.output_mapping || {}, null, 2)}
          onBlur={(event) => commit(event.target.value, 'output_mapping', false)}
        />
      </div>
      {error && <div className="alert-box error" role="alert"><p>{error}</p></div>}
      {node.agent.result_name && (
        <p className="hint">Named result: <code>{node.agent.result_name}</code></p>
      )}
      {void document}
    </Section>
  )
}

// ---------------------------------------------------------------------------
// Tab root
// ---------------------------------------------------------------------------

export interface ConfigTabProps {
  document: Record<string, any>
  nodeId: string
  models: ModelItem[]
  tools: ToolItem[]
  onChange: (updatedDoc: Record<string, any>) => void
  onSetEntry: (nodeId: string) => void
  onDeleteNode: (nodeId: string) => void
}

export function ConfigTab({
  document,
  nodeId,
  models,
  tools,
  onChange,
  onSetEntry,
  onDeleteNode,
}: ConfigTabProps) {
  const node = useMemo(
    () => (document.nodes || []).find((n: any) => n.id === nodeId),
    [document.nodes, nodeId]
  )

  const contextValue = useMemo<ConfigContextValue>(() => ({
    node: node || {},
    document,
    models,
    tools,
    updateNode: (patch) => {
      if (!node) return
      onChange(patchNode(document, node.id, patch))
    },
  }), [node, document, models, tools, onChange])

  if (!node) {
    return <p className="hint">Node not found — it may have been deleted.</p>
  }

  const isEntry = document.entry_node_id === node.id
  const isTool = node.kind === 'tool'

  return (
    <ConfigContext.Provider value={contextValue}>
      <div className="config-tab">
        <div className="config-tab-nodebar">
          <div>
            <strong>{node.id}</strong>
            <span className="config-tab-nodekind">
              {isTool ? 'tool node (deterministic)' : `agent (${node.agent?.mode || 'inline'})`}
            </span>
          </div>
          <div className="config-tab-actions">
            {!isEntry && (
              <button type="button" className="btn small" onClick={() => onSetEntry(node.id)}>
                <Flag size={12} aria-hidden="true" />
                Set as entry
              </button>
            )}
            <button
              type="button"
              className="btn small danger"
              onClick={() => onDeleteNode(node.id)}
              disabled={(document.nodes || []).length <= 1}
            >
              <Trash2 size={12} aria-hidden="true" />
              Delete
            </button>
          </div>
        </div>

        {isTool ? (
          <ToolNodeInfo document={document} node={node} tools={tools} />
        ) : (
          <>
            <PromptSection />
            <ModelSection />
            <CapabilitiesSection />
            <MappingSection />
            <LimitsSection />
            <ContextSection />
            <LongContextSection />
          </>
        )}
      </div>
    </ConfigContext.Provider>
  )
}

function ToolNodeInfo({ document, node, tools }: {
  document: Record<string, any>
  node: Record<string, any>
  tools: ToolItem[]
}) {
  const tool = tools.find((t) => (t.revisions || []).some((r) => r.id === node.tool_revision_id))
  const revision = tool?.revisions.find((r) => r.id === node.tool_revision_id)
  return (
    <div className="tool-node-reference">
      <strong>{tool?.name || 'Unknown tool'}</strong>
      <span>Revision #{revision?.revision_number || 'unknown'}</span>
      <span className={`risk-badge risk-${revision?.risk_level || 'low'}`}>
        {revision?.risk_level || 'unknown'} risk
      </span>
      <span>{revision?.is_mutating ? 'Mutating' : 'Read only'}</span>
      <span>{revision?.max_attempts ?? node.retry?.max_attempts ?? 1} max attempts</span>
      {void document}
    </div>
  )
}
