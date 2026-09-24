// ---------------------------------------------------------------------------
// Vitest for the four pure graph modules.
//
// These functions draw the canvas: if one is wrong it does not crash, it
// silently draws the wrong graph. Hence the exhaustive case tables.
// ---------------------------------------------------------------------------

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { extractEdgePairs } from './edges'
import type { EdgePair } from './edges'
import { NODE_HEIGHT, NODE_WIDTH, deriveGroups, placeNewNodes, runLayout } from './layout'
import { clearPositions, computeSignature, loadPositions, savePositions } from './positions'

// ---------------------------------------------------------------------------
// extractEdgePairs
// ---------------------------------------------------------------------------

describe('extractEdgePairs', () => {
  const exits = ['success', 'failed']

  it('1. direct edge yields one pair', () => {
    const pairs = extractEdgePairs([{ kind: 'direct', source: 'a', target: 'b' }], exits)
    expect(pairs).toEqual([
      { edgeIndex: 0, source: 'a', target: 'b', label: 'direct', kind: 'direct' },
    ])
  })

  it('2. exit edge yields one pair targeting __exit_success', () => {
    const pairs = extractEdgePairs([{ kind: 'exit', source: 'a', result_name: 'success' }], exits)
    expect(pairs).toHaveLength(1)
    expect(pairs[0].target).toBe('__exit_success')
    expect(pairs[0].kind).toBe('exit')
  })

  it('3. join with three sources yields three pairs to the same target', () => {
    const pairs = extractEdgePairs(
      [{ kind: 'join', sources: ['a', 'b', 'c'], target: 'merge', join: 'all' }],
      exits
    )
    expect(pairs).toHaveLength(3)
    expect(pairs.every((p) => p.target === 'merge')).toBe(true)
    expect(pairs.map((p) => p.source)).toEqual(['a', 'b', 'c'])
  })

  it('4. semantic with two routes yields two pairs with field/value labels', () => {
    const pairs = extractEdgePairs(
      [
        {
          kind: 'semantic',
          source: ['classifier', 'category'],
          routes: { billing: 'billing_agent', tech: 'tech_agent' },
        },
      ],
      exits
    )
    expect(pairs).toHaveLength(2)
    expect(pairs.map((p) => p.target)).toEqual(['billing_agent', 'tech_agent'])
    expect(pairs.map((p) => p.label)).toEqual(['category="billing"', 'category="tech"'])
  })

  it('5. semantic with default yields an extra fallback pair', () => {
    const pairs = extractEdgePairs(
      [
        {
          kind: 'semantic',
          source: ['classifier', 'category'],
          routes: { billing: 'billing_agent' },
          default: 'fallback_agent',
        },
      ],
      exits
    )
    expect(pairs).toHaveLength(2)
    const fallback = pairs.find((p) => p.isFallback)
    expect(fallback).toBeDefined()
    expect(fallback!.target).toBe('fallback_agent')
    expect(fallback!.label).toBe('category=default')
  })

  it('6. semantic routing to a named exit yields __exit_<name>', () => {
    const pairs = extractEdgePairs(
      [
        {
          kind: 'semantic',
          source: ['classifier', 'category'],
          routes: { success: 'success' },
          default: 'failed',
        },
      ],
      exits
    )
    expect(pairs[0].target).toBe('__exit_success')
    expect(pairs[1].target).toBe('__exit_failed')
  })

  it('7. mechanical with then and else yields two pairs', () => {
    const pairs = extractEdgePairs(
      [
        {
          kind: 'mechanical',
          source: ['scorer', 'confidence'],
          operator: '>=',
          value: 0.8,
          then: 'auto_approve',
          else: 'human_review',
        },
      ],
      exits
    )
    expect(pairs).toHaveLength(2)
    expect(pairs[0].target).toBe('auto_approve')
    expect(pairs[0].label).toBe('confidence >= 0.8')
    expect(pairs[1].target).toBe('human_review')
    expect(pairs[1].isFallback).toBe(true)
  })

  it('8. mechanical without else yields one pair', () => {
    const pairs = extractEdgePairs(
      [
        {
          kind: 'mechanical',
          source: ['scorer', 'confidence'],
          operator: '>=',
          value: 0.8,
          then: 'auto_approve',
        },
      ],
      exits
    )
    expect(pairs).toHaveLength(1)
  })

  it('9. empty edges array yields empty pairs array', () => {
    expect(extractEdgePairs([], exits)).toEqual([])
  })

  it('10. join with empty sources does not crash', () => {
    expect(extractEdgePairs([{ kind: 'join', sources: [], target: 'merge', join: 'all' }], exits)).toEqual([])
  })

  it('11. semantic source [nodeId, fieldName] takes the correct nodeId — THE critical test', () => {
    const pairs = extractEdgePairs(
      [{ kind: 'semantic', source: ['classifier', 'category'], routes: { billing: 'b' } }],
      exits
    )
    // If `source` were (wrongly) treated as a string, source[0] would be "c".
    expect(pairs[0].source).toBe('classifier')
  })
})

// ---------------------------------------------------------------------------
// runLayout
// ---------------------------------------------------------------------------

describe('runLayout', () => {
  it('1. linear chain of three nodes — x increases, y equal (left to right)', () => {
    const { positions } = runLayout(
      ['a', 'b', 'c'],
      [
        { edgeIndex: 0, source: 'a', target: 'b', kind: 'direct' },
        { edgeIndex: 1, source: 'b', target: 'c', kind: 'direct' },
      ]
    )
    expect(positions.a.x).toBeLessThan(positions.b.x)
    expect(positions.b.x).toBeLessThan(positions.c.x)
    expect(positions.a.y).toBeCloseTo(positions.b.y, 5)
    expect(positions.b.y).toBeCloseTo(positions.c.y, 5)
  })

  it('2. fan-out — both children share x, differ in y', () => {
    const { positions } = runLayout(
      ['root', 'left', 'right'],
      [
        { edgeIndex: 0, source: 'root', target: 'left', kind: 'direct' },
        { edgeIndex: 1, source: 'root', target: 'right', kind: 'direct' },
      ]
    )
    expect(positions.left.x).toBeCloseTo(positions.right.x, 5)
    expect(positions.left.y).not.toBeCloseTo(positions.right.y, 1)
    expect(positions.root.x).toBeLessThan(positions.left.x)
  })

  it('3. isolated node still gets a position', () => {
    const { positions } = runLayout(['lonely'], [])
    expect(positions.lonely).toBeDefined()
    expect(typeof positions.lonely.x).toBe('number')
  })

  it('4. edge pointing at an unknown node is skipped, not fatal', () => {
    const { positions } = runLayout(
      ['a', 'b'],
      [
        { edgeIndex: 0, source: 'a', target: 'ghost', kind: 'direct' },
        { edgeIndex: 1, source: 'ghost2', target: 'b', kind: 'direct' },
      ]
    )
    expect(positions.a).toBeDefined()
    expect(positions.b).toBeDefined()
  })

  it('5. empty input yields empty object', () => {
    expect(runLayout([], []).positions).toEqual({})
  })

  it('6. returned positions are top-left corners, not centers', () => {
    // Single node, no edges: dagre places its center at (width/2, height/2)
    // relative to the origin, so the top-left corner should be (0, 0).
    const { positions } = runLayout(['solo'], [])
    expect(positions.solo.x).toBeCloseTo(0, 3)
    expect(positions.solo.y).toBeCloseTo(0, 3)
    expect(NODE_WIDTH).toBe(240)
    expect(NODE_HEIGHT).toBe(96)
  })
})

// ---------------------------------------------------------------------------
// placeNewNodes — regression cover for the "whole canvas jumps when a node is
// added" bug. Adding a node must leave every existing node exactly where it
// was, and must not stack new nodes on top of each other.
// ---------------------------------------------------------------------------

describe('placeNewNodes', () => {
  const known = { a: { x: 0, y: 0 }, b: { x: 300, y: 0 } }

  it('1. known positions are returned untouched', () => {
    const out = placeNewNodes(['a', 'b', 'c'], known)
    expect(out.a).toEqual(known.a)
    expect(out.b).toEqual(known.b)
  })

  it('2. a new node is placed clear of every existing node', () => {
    const out = placeNewNodes(['a', 'b', 'c'], known)
    const right = Math.max(known.a.x + NODE_WIDTH, known.b.x + NODE_WIDTH)
    expect(out.c.x).toBeGreaterThanOrEqual(right)
  })

  it('3. several new nodes do not stack on one another', () => {
    const out = placeNewNodes(['a', 'b', 'c', 'd', 'e'], known)
    const spots = [out.c, out.d, out.e].map((p) => `${p.x},${p.y}`)
    expect(new Set(spots).size).toBe(3)
  })

  it('4. with nothing known, the first node lands at the origin', () => {
    expect(placeNewNodes(['solo'], {})).toEqual({ solo: { x: 0, y: 0 } })
  })
})

// ---------------------------------------------------------------------------
// deriveGroups
// ---------------------------------------------------------------------------

describe('deriveGroups', () => {
  function positionsFor(ids: string[]): Record<string, { x: number; y: number }> {
    const positions: Record<string, { x: number; y: number }> = {}
    ids.forEach((id, i) => {
      positions[id] = { x: i * 300, y: 100 }
    })
    return positions
  }

  it('1. fan-out then join yields one group containing branch nodes', () => {
    const pairs: EdgePair[] = [
      { edgeIndex: 0, source: 'root', target: 'a', kind: 'direct' },
      { edgeIndex: 1, source: 'root', target: 'b', kind: 'direct' },
      { edgeIndex: 2, source: 'a', target: 'merge', kind: 'join' },
      { edgeIndex: 3, source: 'b', target: 'merge', kind: 'join' },
    ]
    const edges = [{ kind: 'join', sources: ['a', 'b'], target: 'merge', join: 'all' }]
    const groups = deriveGroups(pairs, positionsFor(['root', 'a', 'b', 'merge']), edges)
    expect(groups).toHaveLength(1)
    expect(new Set(groups[0].nodeIds)).toEqual(new Set(['a', 'b']))
    expect(groups[0].label).toBe('PARALLEL FLOW')
  })

  it('2. linear graph without join yields empty array', () => {
    const pairs: EdgePair[] = [
      { edgeIndex: 0, source: 'a', target: 'b', kind: 'direct' },
      { edgeIndex: 1, source: 'b', target: 'c', kind: 'direct' },
    ]
    expect(deriveGroups(pairs, positionsFor(['a', 'b', 'c']), [])).toEqual([])
  })

  it('3. two separate non-overlapping joins yield two groups', () => {
    const pairs: EdgePair[] = [
      { edgeIndex: 0, source: 'r1', target: 'a', kind: 'direct' },
      { edgeIndex: 1, source: 'r1', target: 'b', kind: 'direct' },
      { edgeIndex: 2, source: 'a', target: 'm1', kind: 'join' },
      { edgeIndex: 3, source: 'b', target: 'm1', kind: 'join' },
      { edgeIndex: 4, source: 'r2', target: 'c', kind: 'direct' },
      { edgeIndex: 5, source: 'r2', target: 'd', kind: 'direct' },
      { edgeIndex: 6, source: 'c', target: 'm2', kind: 'join' },
      { edgeIndex: 7, source: 'd', target: 'm2', kind: 'join' },
    ]
    const edges = [
      { kind: 'join', sources: ['a', 'b'], target: 'm1', join: 'all' },
      { kind: 'join', sources: ['c', 'd'], target: 'm2', join: 'all' },
    ]
    const groups = deriveGroups(pairs, positionsFor(['r1', 'r2', 'a', 'b', 'c', 'd', 'm1', 'm2']), edges)
    expect(groups).toHaveLength(2)
  })

  it('4. nested groups yield empty array (bail-out)', () => {
    // Outer fan-out root -> {a, b}; branch a itself fans out a -> {a1, a2}
    // which join into m_inner. The inner join group {a1, a2} is nested inside
    // the outer join group {a, a1, a2, m_inner, b}.
    const pairs: EdgePair[] = [
      { edgeIndex: 0, source: 'root', target: 'a', kind: 'direct' },
      { edgeIndex: 1, source: 'root', target: 'b', kind: 'direct' },
      { edgeIndex: 2, source: 'a', target: 'a1', kind: 'direct' },
      { edgeIndex: 3, source: 'a', target: 'a2', kind: 'direct' },
      { edgeIndex: 4, source: 'a1', target: 'm_inner', kind: 'join' },
      { edgeIndex: 5, source: 'a2', target: 'm_inner', kind: 'join' },
      { edgeIndex: 6, source: 'm_inner', target: 'end', kind: 'join' },
      { edgeIndex: 7, source: 'b', target: 'end', kind: 'join' },
    ]
    const edges = [
      { kind: 'join', sources: ['a1', 'a2'], target: 'm_inner', join: 'all' },
      { kind: 'join', sources: ['m_inner', 'b'], target: 'end', join: 'all' },
    ]
    const groups = deriveGroups(pairs, positionsFor(['root', 'a', 'b', 'a1', 'a2', 'm_inner', 'end']), edges)
    expect(groups).toEqual([])
  })

  it('5. bounding box encloses all members plus padding', () => {
    const pairs: EdgePair[] = [
      { edgeIndex: 0, source: 'root', target: 'a', kind: 'direct' },
      { edgeIndex: 1, source: 'root', target: 'b', kind: 'direct' },
    ]
    const edges = [{ kind: 'join', sources: ['a', 'b'], target: 'merge', join: 'all' }]
    const positions: Record<string, { x: number; y: number }> = {
      root: { x: 0, y: 0 },
      a: { x: 100, y: 200 },
      b: { x: 500, y: 400 },
      merge: { x: 300, y: 600 },
    }
    const groups = deriveGroups(
      pairs.map((p, i) => ({ ...p, edgeIndex: i })).concat([
        { edgeIndex: 2, source: 'a', target: 'merge', kind: 'join' },
        { edgeIndex: 3, source: 'b', target: 'merge', kind: 'join' },
      ]),
      positions,
      edges
    )
    expect(groups).toHaveLength(1)
    const g = groups[0]
    // min member x = 100, padding 28
    expect(g.x).toBe(100 - 28)
    expect(g.y).toBe(200 - 28)
    expect(g.width).toBe(500 + NODE_WIDTH - 100 + 56)
    expect(g.height).toBe(400 + NODE_HEIGHT - 200 + 56)
  })
})

// ---------------------------------------------------------------------------
// positions cache (localStorage)
// ---------------------------------------------------------------------------

class MemoryStorage {
  private map = new Map<string, string>()
  getItem(key: string) {
    return this.map.get(key) ?? null
  }
  setItem(key: string, value: string) {
    this.map.set(key, value)
  }
  removeItem(key: string) {
    this.map.delete(key)
  }
  clear() {
    this.map.clear()
  }
}

describe('positions cache', () => {
  let storage: MemoryStorage

  beforeEach(() => {
    storage = new MemoryStorage()
    vi.stubGlobal('localStorage', storage as unknown as Storage)
    return () => vi.unstubAllGlobals()
  })

  it('1. computeSignature is order-insensitive', () => {
    expect(computeSignature(['b', 'a'])).toBe(computeSignature(['a', 'b']))
  })

  it('2. save then load returns the same positions', () => {
    const pos = { a: { x: 10, y: 20 }, b: { x: 30, y: 40 } }
    savePositions('agent-1', ['a', 'b'], pos)
    expect(loadPositions('agent-1', ['b', 'a'])).toEqual(pos)
  })

  // Adding a node must NOT discard the layout the user arranged by hand;
  // the caller places whatever is missing (see placeNewNodes).
  it('3. loading with a larger node set keeps the known positions', () => {
    savePositions('agent-1', ['a', 'b'], { a: { x: 5, y: 6 }, b: { x: 7, y: 8 } })
    expect(loadPositions('agent-1', ['a', 'b', 'c'])).toEqual({
      a: { x: 5, y: 6 },
      b: { x: 7, y: 8 },
    })
  })

  it('4. loading from an empty key returns null', () => {
    expect(loadPositions('missing', ['a'])).toBeNull()
  })

  it('5. corrupted JSON returns null, does not throw', () => {
    storage.setItem('agent-layout:broken', '{not json')
    expect(loadPositions('broken', ['a'])).toBeNull()
  })

  it('6. clearPositions makes the next load return null', () => {
    savePositions('agent-1', ['a'], { a: { x: 1, y: 2 } })
    expect(loadPositions('agent-1', ['a'])).not.toBeNull()
    clearPositions('agent-1')
    expect(loadPositions('agent-1', ['a'])).toBeNull()
  })
})
