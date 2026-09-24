import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Brain, CheckCircle2, ChevronRight, CircleAlert, Loader2, Wrench } from 'lucide-react'

// One rendered "step" of an assistant turn. Built from persisted message
// content blocks and, while streaming, from live SSE chunks.
export type ChatPart =
  | { kind: 'text'; text: string }
  | { kind: 'reasoning'; text: string }
  | { kind: 'tool'; id: string; name: string; args: unknown; result?: string; status?: 'success' | 'error' }

/** `notion_notion-search` -> `notion-search`; server prefix is noise in the UI. */
function toolLabel(name: string): string {
  const i = name.indexOf('_')
  return i > 0 ? name.slice(i + 1) : name
}

function pretty(value: unknown): string {
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2)
    } catch {
      return value
    }
  }
  return JSON.stringify(value, null, 2)
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" />,
          table: ({ node: _node, ...props }) => (
            <div className="md-table-wrap">
              <table {...props} />
            </div>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}

function ReasoningStep({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <div className={`step ${open ? 'open' : ''}`}>
      <button type="button" className="step-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <Brain aria-hidden="true" />
        <span>{live ? 'Thinking' : 'Thought process'}</span>
        <ChevronRight className="step-chevron" aria-hidden="true" />
      </button>
      {open && <div className="step-body step-reasoning">{text}</div>}
    </div>
  )
}

function ToolStep({ part }: { part: Extract<ChatPart, { kind: 'tool' }> }) {
  const [open, setOpen] = useState(false)
  const pending = part.result === undefined
  const failed = part.status === 'error' || part.result?.startsWith('Tool execution failed')
  const Icon = pending ? Loader2 : failed ? CircleAlert : CheckCircle2
  return (
    <div className={`step ${open ? 'open' : ''}`}>
      <button type="button" className="step-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <Wrench aria-hidden="true" />
        <span>
          {pending ? 'Calling' : 'Called'} <code>{toolLabel(part.name)}</code>
        </span>
        <Icon className={`step-status ${pending ? 'spin' : failed ? 'error' : 'ok'}`} aria-label={pending ? 'Running' : failed ? 'Failed' : 'Done'} />
        <ChevronRight className="step-chevron" aria-hidden="true" />
      </button>
      {open && (
        <div className="step-body">
          <div className="step-label">Input</div>
          <pre>{pretty(part.args)}</pre>
          {!pending && (
            <>
              <div className="step-label">Result</div>
              <pre>{pretty(part.result)}</pre>
            </>
          )}
        </div>
      )}
    </div>
  )
}

export function AssistantParts({ parts, live = false }: { parts: ChatPart[]; live?: boolean }) {
  return (
    <>
      {parts.map((part, i) => {
        if (part.kind === 'reasoning') {
          return <ReasoningStep key={i} text={part.text} live={live && i === parts.length - 1} />
        }
        if (part.kind === 'tool') return <ToolStep key={part.id || i} part={part} />
        return part.text.trim() ? <Markdown key={i} text={part.text} /> : null
      })}
    </>
  )
}

/**
 * Folds persisted messages into display turns: consecutive assistant and
 * tool messages form one assistant turn, with tool results attached to the
 * call that produced them.
 */
export interface ChatTurn {
  id: string
  role: 'user' | 'assistant'
  createdAt: string
  parts: ChatPart[]
}

export function buildTurns(messages: { id: string; role: string; content: any; created_at: string }[]): ChatTurn[] {
  const turns: ChatTurn[] = []
  for (const m of messages) {
    const blocks: any[] = Array.isArray(m.content)
      ? m.content
      : [{ type: 'text', text: typeof m.content === 'string' ? m.content : JSON.stringify(m.content) }]

    if (m.role === 'user') {
      const text = blocks.map((b) => (typeof b === 'string' ? b : b?.text ?? '')).join('')
      turns.push({ id: m.id, role: 'user', createdAt: m.created_at, parts: [{ kind: 'text', text }] })
      continue
    }
    if (m.role === 'system') continue

    let turn = turns[turns.length - 1]
    if (!turn || turn.role !== 'assistant') {
      turn = { id: m.id, role: 'assistant', createdAt: m.created_at, parts: [] }
      turns.push(turn)
    }

    for (const b of blocks) {
      if (typeof b === 'string') turn.parts.push({ kind: 'text', text: b })
      else if (b?.type === 'text') turn.parts.push({ kind: 'text', text: b.text ?? '' })
      else if (b?.type === 'reasoning') turn.parts.push({ kind: 'reasoning', text: b.reasoning ?? '' })
      else if (b?.type === 'tool_call') turn.parts.push({ kind: 'tool', id: b.id, name: b.name, args: b.args })
      else if (b?.type === 'tool_result') {
        const call = turn.parts.find((p) => p.kind === 'tool' && p.id === b.tool_call_id)
        if (call && call.kind === 'tool') {
          call.result = b.content
          call.status = b.status
        }
      }
    }
  }
  return turns
}
