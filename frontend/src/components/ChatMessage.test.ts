import { describe, expect, it } from 'vitest'
import { buildTurns } from './ChatMessage'

const at = '2026-09-24T00:00:00Z'

describe('buildTurns', () => {
  it('folds assistant + tool messages into one turn with results attached to calls', () => {
    const turns = buildTurns([
      { id: 'u1', role: 'user', created_at: at, content: [{ type: 'text', text: 'cek notion' }] },
      {
        id: 'a1',
        role: 'assistant',
        created_at: at,
        content: [
          { type: 'reasoning', reasoning: 'list pages first' },
          { type: 'text', text: 'Sebentar.' },
          { type: 'tool_call', id: 'c1', name: 'notion_notion-search', args: { query: 'x' } },
        ],
      },
      { id: 't1', role: 'tool', created_at: at, content: [{ type: 'tool_result', tool_call_id: 'c1', status: 'success', content: '{"results":[]}' }] },
      { id: 'a2', role: 'assistant', created_at: at, content: [{ type: 'text', text: '## Hasil' }] },
    ])

    expect(turns.map((t) => t.role)).toEqual(['user', 'assistant'])
    expect(turns[1].parts).toEqual([
      { kind: 'reasoning', text: 'list pages first' },
      { kind: 'text', text: 'Sebentar.' },
      { kind: 'tool', id: 'c1', name: 'notion_notion-search', args: { query: 'x' }, result: '{"results":[]}', status: 'success' },
      { kind: 'text', text: '## Hasil' },
    ])
  })

  it('keeps legacy plain-text tool messages from creating a stray text part', () => {
    const turns = buildTurns([
      { id: 'a1', role: 'assistant', created_at: at, content: [{ type: 'tool_call', id: 'c1', name: 's_x', args: {} }] },
      { id: 't1', role: 'tool', created_at: at, content: [{ type: 'tool_result', tool_call_id: 'c1', content: 'ok' }] },
    ])
    expect(turns).toHaveLength(1)
    expect(turns[0].parts).toHaveLength(1)
  })
})
