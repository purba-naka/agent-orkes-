// ---------------------------------------------------------------------------
// Test tab — runs the agent's ACTIVE revision via the SSE run stream.
// MOVED from the AgentsView run panel (logic preserved, not rewritten).
//
// An agent without an active_revision_id cannot run: show a message pointing
// at the Publish button instead of silently disabling the run button.
// ---------------------------------------------------------------------------

import { useState } from 'react'
import type { AgentItem, RunInterrupt } from '../../../api'
import { api } from '../../../api'

export interface TestTabProps {
  agent: AgentItem
}

export function TestTab({ agent }: TestTabProps) {
  const [runPrompt, setRunPrompt] = useState(
    'Explain what an AI agent is in two concise sentences.'
  )
  const [runStatus, setRunStatus] = useState<
    'idle' | 'running' | 'interrupted' | 'completed' | 'failed'
  >('idle')
  const [runLogs, setRunLogs] = useState<string[]>([])
  const [runOutput, setRunOutput] = useState<string>('')
  const [runId, setRunId] = useState<string | null>(null)
  const [pendingInterrupt, setPendingInterrupt] = useState<RunInterrupt | null>(null)

  async function handleRun() {
    setRunStatus('running')
    setRunLogs([])
    setRunOutput('')

    try {
      await api.runAgentStream(agent.id, { prompt: runPrompt }, (event, data) => {
        if (event === 'open') {
          setRunId(data.run_id)
          setRunLogs((prev) => [...prev, `[OPEN] Run ID: ${data.run_id} | Thread: ${data.thread_id}`])
        } else if (event === 'native') {
          const mode = data.mode
          if (mode === 'messages') {
            const chunk = data.chunk
            const content = Array.isArray(chunk) ? chunk[0]?.content : chunk?.content
            if (content) {
              setRunLogs((prev) => [...prev, `[TOKEN] ${content}`])
            }
          } else if (mode === 'updates') {
            setRunLogs((prev) => [...prev, `[NODE] ${JSON.stringify(data.chunk)}`])
          }
        } else if (event === 'close') {
          setRunLogs((prev) => [
            ...prev,
            `[CLOSE] Status: ${data.status} | Result: ${data.result_name || 'none'}`,
          ])
          if (data.output?.content) {
            setRunOutput(data.output.content)
          }
          if (data.status === 'interrupted') {
            setRunStatus('interrupted')
            if (runId) {
              void api.getRun(runId).then((run) => {
                setPendingInterrupt(run.interrupts.find((item) => item.status === 'pending') || null)
              })
            }
          } else {
            setRunStatus(data.status === 'completed' ? 'completed' : 'failed')
          }
        } else if (event === 'error') {
          setRunLogs((prev) => [...prev, `[ERROR] ${data.code}: ${data.message}`])
          setRunStatus('failed')
        }
      })
    } catch (err: any) {
      setRunLogs((prev) => [...prev, `[CLIENT ERROR] ${err.message}`])
      setRunStatus('failed')
    }
  }

  async function handleInterruptDecision(action: string) {
    if (!runId || !pendingInterrupt) return
    const reason =
      action === 'reject' || action === 'abort'
        ? window.prompt('Reason (optional)') || undefined
        : undefined
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
    setRunStatus('running')
    await api.resumeRunStream(
      runId,
      { interrupt_id: pendingInterrupt.interrupt_id, action, reason, ...extra },
      (event, data) => {
        if (event === 'native') setRunLogs((prev) => [...prev, `[RESUME] ${JSON.stringify(data.chunk)}`])
        if (event === 'close') {
          setRunStatus(data.status)
          if (data.output?.content) setRunOutput(data.output.content)
          setPendingInterrupt(null)
        }
      }
    )
  }

  if (!agent.active_revision_id) {
    return (
      <div className="test-tab">
        <div className="alert-box">
          <p className="hint">
            This agent has no published revision yet. Click <strong>Publish</strong> in
            the top bar first — runs execute the published revision, not the draft.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="test-tab">
      <div className="test-tab-header">
        <span className={`status-badge ${runStatus}`}>Status: {runStatus}</span>
      </div>

      <div className="run-inputs">
        <input
          type="text"
          value={runPrompt}
          onChange={(e) => setRunPrompt(e.target.value)}
          placeholder="Enter user message/prompt to run..."
          disabled={runStatus === 'running'}
        />
        <button
          onClick={handleRun}
          disabled={runStatus === 'running'}
          className="btn primary"
        >
          {runStatus === 'running' ? 'Running...' : 'Execute Run'}
        </button>
      </div>

      {pendingInterrupt && (
        <div className="alert-box">
          <p><strong>Approval required:</strong> {pendingInterrupt.kind}</p>
          <pre>{JSON.stringify(pendingInterrupt.payload.value, null, 2)}</pre>
          <div className="button-row">
            {pendingInterrupt.kind === 'tool_approval' ? (
              <>
                <button className="btn primary" onClick={() => handleInterruptDecision('approve')}>Approve</button>
                <button className="btn" onClick={() => handleInterruptDecision('edit')}>Edit input</button>
                <button className="btn danger" onClick={() => handleInterruptDecision('reject')}>Reject</button>
              </>
            ) : (
              <>
                <button className="btn primary" onClick={() => handleInterruptDecision('accept')}>Accept</button>
                <button className="btn" onClick={() => handleInterruptDecision('revise')}>Revise output</button>
                <button className="btn danger" onClick={() => handleInterruptDecision('abort')}>Abort</button>
              </>
            )}
          </div>
        </div>
      )}

      {runOutput && (
        <div className="final-output-card">
          <h5>Final Result</h5>
          <p>{runOutput}</p>
        </div>
      )}

      <div className="terminal-box">
        <div className="terminal-header">
          <span>Stream Chunks & Events</span>
          <button onClick={() => setRunLogs([])} className="btn small">
            Clear
          </button>
        </div>
        <pre className="terminal-content">
          {runLogs.length === 0 ? (
            <span className="hint">Waiting for run execution...</span>
          ) : (
            runLogs.join('\n')
          )}
        </pre>
      </div>
    </div>
  )
}
