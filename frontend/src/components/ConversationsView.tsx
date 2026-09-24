import React, { useEffect, useRef, useState } from 'react'
import { ArrowUp, Bot, RefreshCw, ShieldAlert } from 'lucide-react'
import type { AgentItem, ConversationDetail, RunInterrupt } from '../api'
import { api } from '../api'

interface ConversationsViewProps {
  selectedConversationId: string | null
  onSelectConversation: (id: string) => void
  onConversationsChanged: () => void
}

export function ConversationsView({
  selectedConversationId,
  onSelectConversation,
  onConversationsChanged,
}: ConversationsViewProps) {
  const [agents, setAgents] = useState<AgentItem[]>([])
  const [error, setError] = useState<string | null>(null)

  // Selection & detail
  const [activeConv, setActiveConv] = useState<ConversationDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  // Creation form
  const [createAgentId, setCreateAgentId] = useState('')
  const [creating, setCreating] = useState(false)

  // Upgrade form
  const [showUpgradeModal, setShowUpgradeModal] = useState(false)
  const [upgradeSummary, setUpgradeSummary] = useState('')
  const [upgrading, setUpgrading] = useState(false)

  // Rebuilding
  const [rebuilding, setRebuilding] = useState(false)

  // Chat message input & streaming
  const [inputText, setInputText] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [streamContent, setStreamContent] = useState('')
  const [pendingRunId, setPendingRunId] = useState<string | null>(null)
  const [pendingInterrupt, setPendingInterrupt] = useState<RunInterrupt | null>(null)

  const threadRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)

  async function loadData() {
    try {
      const agentList = await api.listAgents()
      setAgents(agentList.filter((a) => a.active_revision_id !== null))
      if (agentList.length > 0 && !createAgentId) {
        const published = agentList.find((a) => a.active_revision_id)
        if (published) setCreateAgentId(published.id)
      }
      setError(null)
    } catch (err: any) {
      setError(err.message)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  // Load the conversation detail whenever the selection changes (the list
  // lives in the app sidebar now).
  useEffect(() => {
    let cancelled = false
    async function loadDetail() {
      if (!selectedConversationId) {
        setActiveConv(null)
        setStreamContent('')
        return
      }
      try {
        setDetailLoading(true)
        setStreamContent('')
        const detail = await api.getConversation(selectedConversationId)
        if (!cancelled) setActiveConv(detail)
      } catch (err: any) {
        if (!cancelled) alert(`Failed to load conversation: ${err.message}`)
      } finally {
        if (!cancelled) setDetailLoading(false)
      }
    }
    loadDetail()
    return () => {
      cancelled = true
    }
  }, [selectedConversationId])

  // Auto-scroll thread to bottom when messages or stream content change
  useEffect(() => {
    const thread = threadRef.current
    if (thread) {
      thread.scrollTop = thread.scrollHeight
    }
  }, [activeConv?.messages, streamContent, pendingInterrupt])

  // Auto-grow composer textarea
  useEffect(() => {
    const el = composerRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [inputText])

  async function reloadConversation(conversationId = selectedConversationId) {
    if (!conversationId) return
    try {
      const detail = await api.getConversation(conversationId)
      setActiveConv(detail)
    } catch (err: any) {
      alert(`Failed to load conversation: ${err.message}`)
    }
  }

  // First message from the empty state: create the conversation, then stream
  // the message into it. Title is derived from that first message.
  async function handleStartConversation(e: React.FormEvent) {
    e.preventDefault()
    const userText = inputText.trim()
    if (!createAgentId || !userText || creating) return
    let newId: string
    try {
      setCreating(true)
      const newConv = await api.createConversation(createAgentId, userText.slice(0, 60))
      newId = newConv.id
    } catch (err: any) {
      alert(`Error creating conversation: ${err.message}`)
      return
    } finally {
      setCreating(false)
    }
    onConversationsChanged()
    onSelectConversation(newId)
    setInputText('')
    await streamMessage(newId, userText)
  }

  async function handleSendMessage(e: React.FormEvent) {
    e.preventDefault()
    if (!selectedConversationId || !inputText.trim() || streaming) return
    const userText = inputText.trim()
    setInputText('')
    await streamMessage(selectedConversationId, userText)
  }

  async function streamMessage(conversationId: string, userText: string) {
    setStreaming(true)
    setStreamContent('')

    try {
      let accumulated = ''
      await api.sendConversationMessageStream(
        conversationId,
        userText,
        (event, data) => {
          if (event === 'native' && data?.chunk) {
            // Check for chunk text or message chunk
            const payload = data.chunk
            if (data.mode === 'messages') {
              const msg = Array.isArray(payload) ? payload[0] : payload
              if (msg?.content && typeof msg.content === 'string') {
                accumulated = msg.content
                setStreamContent(accumulated)
              }
            } else if (data.mode === 'updates' && payload?.model?.messages) {
              const msgs = payload.model.messages
              const last = msgs[msgs.length - 1]
              if (last?.content && typeof last.content === 'string') {
                accumulated = last.content
                setStreamContent(accumulated)
              }
            }
          } else if (event === 'open') {
            setPendingRunId(data.run_id)
          } else if (event === 'close') {
            if (data?.output?.content) setStreamContent(data.output.content)
            if (data.status === 'interrupted') {
              setPendingRunId((currentRunId) => {
                if (currentRunId) {
                  void api.getRun(currentRunId).then((run) => {
                    setPendingInterrupt(run.interrupts.find((item) => item.status === 'pending') || null)
                  })
                }
                return currentRunId
              })
            }
          }
        }
      )
    } catch (err: any) {
      alert(`Failed to send message: ${err.message}`)
    } finally {
      setStreaming(false)
      setStreamContent('')
      // Refresh projected messages and the sidebar history entry
      onConversationsChanged()
      await reloadConversation(conversationId)
    }
  }

  async function handleInterruptDecision(action: string) {
    if (!pendingRunId || !pendingInterrupt) return
    const reason = action === 'reject' || action === 'abort' ? window.prompt('Reason (optional)') || undefined : undefined
    let extra: Record<string, any> = {}
    if (action === 'edit' || action === 'revise') {
      const raw = window.prompt(action === 'edit' ? 'Edited tool input JSON' : 'Revised node output JSON')
      if (!raw) return
      try {
        extra = { [action === 'edit' ? 'input' : 'output']: JSON.parse(raw) }
      } catch {
        alert('Decision payload must be valid JSON')
        return
      }
    }
    setStreaming(true)
    try {
      await api.resumeRunStream(
        pendingRunId,
        { interrupt_id: pendingInterrupt.interrupt_id, action, reason, ...extra },
        (event, data) => {
          if (event === 'close' && data.status !== 'interrupted') {
            setPendingInterrupt(null)
            if (data.output?.content) setStreamContent(data.output.content)
          }
        }
      )
      await reloadConversation()
    } finally {
      setStreaming(false)
    }
  }

  async function handleUpgrade(e: React.FormEvent) {
    e.preventDefault()
    if (!selectedConversationId) return
    try {
      setUpgrading(true)
      const upgraded = await api.upgradeConversation(selectedConversationId, upgradeSummary.trim() || undefined)
      setShowUpgradeModal(false)
      setUpgradeSummary('')
      onConversationsChanged()
      onSelectConversation(upgraded.id)
    } catch (err: any) {
      alert(`Error upgrading conversation: ${err.message}`)
    } finally {
      setUpgrading(false)
    }
  }

  async function handleRebuildProjections() {
    if (!selectedConversationId) return
    try {
      setRebuilding(true)
      const res = await api.rebuildConversationProjections(selectedConversationId)
      alert(`Projections rebuilt from checkpoints! Rebuilt message count: ${res.rebuilt_count}`)
      await reloadConversation()
    } catch (err: any) {
      alert(`Error rebuilding projections: ${err.message}`)
    } finally {
      setRebuilding(false)
    }
  }

  function renderMessageContent(content: any) {
    if (typeof content === 'string') return content
    if (Array.isArray(content)) {
      return content.map((part, idx) => {
        if (typeof part === 'string') return <span key={idx}>{part}</span>
        if (part?.type === 'text') return <span key={idx}>{part.text}</span>
        return <pre key={idx}>{JSON.stringify(part, null, 2)}</pre>
      })
    }
    return <pre>{JSON.stringify(content, null, 2)}</pre>
  }

  return (
    <div className="chat-layout">
      {/* Chat history lives in the app sidebar now; this view is the thread. */}
      <section className="chat-main">
        {error && <div className="alert-box error">{error}</div>}

        {!selectedConversationId ? (
          <div className="chat-empty chat-empty-start">
            <h2>What can your agents help with?</h2>
            {agents.length === 0 ? (
              <p className="subtext">
                No published agents yet. Publish a revision in the Agents tab to start chatting.
              </p>
            ) : (
              <div className="composer composer-static">
                <form className="composer-form" onSubmit={handleStartConversation}>
                  <textarea
                    ref={composerRef}
                    className="composer-input"
                    rows={1}
                    placeholder="Ask anything"
                    value={inputText}
                    onChange={(e) => setInputText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault()
                        e.currentTarget.form?.requestSubmit()
                      }
                    }}
                    disabled={creating}
                    aria-label="Message"
                  />
                  <div className="composer-actions">
                    <div className="composer-picker">
                      <label htmlFor="new-conversation-agent">Agent</label>
                      <select
                        id="new-conversation-agent"
                        value={createAgentId}
                        onChange={(e) => setCreateAgentId(e.target.value)}
                        disabled={creating}
                        required
                      >
                        {agents.map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.name} (Rev #{a.active_revision?.revision_number || 1})
                          </option>
                        ))}
                      </select>
                    </div>
                    <button
                      type="submit"
                      className="composer-send"
                      disabled={creating || !createAgentId || !inputText.trim()}
                      aria-label="Send message"
                    >
                      <ArrowUp aria-hidden="true" />
                    </button>
                  </div>
                </form>
                <div className="composer-hint">
                  {creating ? 'Starting conversation…' : 'Enter to send, Shift+Enter for a new line'}
                </div>
              </div>
            )}
          </div>
        ) : detailLoading && !activeConv ? (
          <div className="chat-empty">
            <div className="thinking-indicator" aria-label="Loading conversation">
              <span />
              <span />
              <span />
            </div>
            <p className="subtext">Loading conversation…</p>
          </div>
        ) : activeConv ? (
          <>
            <div className="chat-header">
              <div>
                <h2>{activeConv.title}</h2>
                <div className="chat-header-badges">
                  <span className="badge accent">Agent: {activeConv.agent_name || 'Agent'}</span>
                  <span className="badge">Pinned to Rev #{activeConv.revision_number}</span>
                  {activeConv.upgrade_summary && (
                    <span className="badge" title={activeConv.upgrade_summary}>
                      Upgraded from Parent
                    </span>
                  )}
                </div>
              </div>

              <div className="button-row">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={handleRebuildProjections}
                  disabled={rebuilding}
                  title="Rebuild message projections directly from LangGraph checkpoints"
                >
                  <RefreshCw aria-hidden="true" />
                  {rebuilding ? 'Rebuilding…' : 'Rebuild Projections'}
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => setShowUpgradeModal(true)}
                >
                  Upgrade Revision
                </button>
              </div>
            </div>

            {/* Upgrade modal */}
            {showUpgradeModal && (
              <div className="modal-overlay" onClick={() => setShowUpgradeModal(false)}>
                <div className="modal-card" onClick={(e) => e.stopPropagation()}>
                  <h3>Upgrade Conversation to Active Revision</h3>
                  <p className="subtext">
                    This creates a new child conversation pinned to the agent&apos;s latest published
                    revision. Checkpoints are not migrated; conversation state starts fresh with an
                    optional summary.
                  </p>
                  <form onSubmit={handleUpgrade} className="form-grid">
                    <div className="form-row">
                      <label htmlFor="upgrade-summary">Upgrade Summary (Optional)</label>
                      <textarea
                        id="upgrade-summary"
                        rows={2}
                        placeholder="e.g. Summary of previous discussion..."
                        value={upgradeSummary}
                        onChange={(e) => setUpgradeSummary(e.target.value)}
                      />
                    </div>
                    <div className="modal-actions">
                      <button
                        type="button"
                        className="btn btn-secondary"
                        onClick={() => setShowUpgradeModal(false)}
                      >
                        Cancel
                      </button>
                      <button type="submit" className="btn btn-primary" disabled={upgrading}>
                        {upgrading ? 'Upgrading…' : 'Confirm Upgrade'}
                      </button>
                    </div>
                  </form>
                </div>
              </div>
            )}

            {/* Thread */}
            <div className="chat-thread" ref={threadRef}>
              {pendingInterrupt && (
                <div className="interrupt-card">
                  <div className="interrupt-header">
                    <ShieldAlert aria-hidden="true" />
                    Approval required: {pendingInterrupt.kind}
                  </div>
                  <pre>{JSON.stringify(pendingInterrupt.payload.value, null, 2)}</pre>
                  <div className="button-row">
                    {pendingInterrupt.kind === 'tool_approval' ? (
                      <>
                        <button className="btn btn-primary" onClick={() => handleInterruptDecision('approve')}>Approve</button>
                        <button className="btn btn-secondary" onClick={() => handleInterruptDecision('edit')}>Edit</button>
                        <button className="btn btn-danger" onClick={() => handleInterruptDecision('reject')}>Reject</button>
                      </>
                    ) : (
                      <>
                        <button className="btn btn-primary" onClick={() => handleInterruptDecision('accept')}>Accept</button>
                        <button className="btn btn-secondary" onClick={() => handleInterruptDecision('revise')}>Revise</button>
                        <button className="btn btn-danger" onClick={() => handleInterruptDecision('abort')}>Abort</button>
                      </>
                    )}
                  </div>
                </div>
              )}

              {activeConv.messages.length === 0 && !streaming && (
                <div className="chat-empty">
                  <Bot aria-hidden="true" />
                  <h2>Start the conversation</h2>
                  <p className="subtext">Send a message below to begin.</p>
                </div>
              )}

              {activeConv.messages.map((m) => {
                const isUser = m.role === 'user'
                return (
                  <div key={m.id} className={`chat-message ${isUser ? 'user' : 'assistant'}`}>
                    {!isUser && (
                      <div className="message-avatar" aria-hidden="true">
                        <Bot />
                      </div>
                    )}
                    <div className="message-block">
                      <div className="message-meta">
                        <strong>{isUser ? 'You' : 'Assistant'}</strong>
                        <span>{new Date(m.created_at).toLocaleTimeString()}</span>
                      </div>
                      <div className="message-body">{renderMessageContent(m.content)}</div>
                    </div>
                  </div>
                )
              })}

              {/* Streaming assistant message */}
              {streaming && (
                <div className="chat-message assistant">
                  <div className="message-avatar" aria-hidden="true">
                    <Bot />
                  </div>
                  <div className="message-block">
                    <div className="message-meta">
                      <strong>Assistant</strong>
                      <span>streaming…</span>
                    </div>
                    <div className="message-body">
                      {streamContent ? (
                        <>
                          {streamContent}
                          <span className="streaming-caret" aria-hidden="true" />
                        </>
                      ) : (
                        <span className="thinking-indicator" aria-label="Assistant is thinking">
                          <span />
                          <span />
                          <span />
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Composer */}
            <div className="composer">
              <form
                className="composer-form"
                onSubmit={handleSendMessage}
              >
                <textarea
                  ref={composerRef}
                  className="composer-input"
                  rows={1}
                  placeholder={streaming ? 'Waiting for response…' : 'Type your message…'}
                  value={inputText}
                  onChange={(e) => setInputText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      e.currentTarget.form?.requestSubmit()
                    }
                  }}
                  disabled={streaming}
                  aria-label="Message"
                />
                <button
                  type="submit"
                  className="composer-send"
                  disabled={streaming || !inputText.trim()}
                  aria-label="Send message"
                >
                  <ArrowUp aria-hidden="true" />
                </button>
              </form>
              <div className="composer-hint">
                Enter to send • Shift+Enter for a new line
              </div>
            </div>
          </>
        ) : null}
      </section>
    </div>
  )
}
