// ---------------------------------------------------------------------------
// Agent node card — custom React Flow node type.
//
// Renders with theme tokens (no hex, no inline styles) so light mode works.
// Wrapped in memo() with a deliberately narrow `data` payload of primitives
// only: no objects, no functions (handlers go through onNodeClick on
// <ReactFlow>), otherwise memo() is useless and every keystroke re-renders
// every node.
// ---------------------------------------------------------------------------

import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { NodeProps } from '@xyflow/react'
import { AlertTriangle, Bot, CornerDownRight, Wrench } from 'lucide-react'
import { NODE_HEIGHT, NODE_WIDTH } from '../lib/layout'

export interface AgentCardData {
  nodeId: string
  kind: 'agent' | 'tool'
  mode?: 'inline' | 'ref'
  /** revision label for tool/agent, already resolved to a string */
  subtitle: string
  isEntry: boolean
  isSelected: boolean
  errorCount: number
  warningCount: number
}

export const AgentNodeCard = memo(function AgentNodeCard({ data }: NodeProps) {
  const d = data as unknown as AgentCardData
  return (
    <div
      className={`agent-card-node ${d.isSelected ? 'selected' : ''} ${
        d.errorCount > 0 ? 'has-error' : ''
      } ${d.isEntry ? 'is-entry' : ''}`}
      style={{ width: NODE_WIDTH, height: NODE_HEIGHT }}
    >
      {/* Four handles: the layout flows left to right, but an edge may be
          drawn from any side so branches and loops do not force the user to
          route everything through the top and bottom. Each needs a unique id
          per type, otherwise React Flow cannot tell them apart. */}
      <Handle type="target" id="in-left" position={Position.Left} className="agent-node-handle" />
      <Handle type="target" id="in-top" position={Position.Top} className="agent-node-handle" />
      <div className="agent-card-node-header">
        <span className="agent-card-node-icon" aria-hidden="true">
          {d.kind === 'tool' ? <Wrench size={14} /> : <Bot size={14} />}
        </span>
        <span className="agent-card-node-title" title={d.nodeId}>{d.nodeId}</span>
        {d.isEntry && <span className="agent-card-entry-badge">ENTRY</span>}
      </div>
      <div className="agent-card-node-kind">
        {d.kind === 'agent' ? `agent (${d.mode || 'inline'})` : 'tool (deterministic)'}
      </div>
      {d.subtitle && (
        <div className="agent-card-node-subtitle">
          <CornerDownRight size={11} aria-hidden="true" />
          <span>{d.subtitle}</span>
        </div>
      )}
      {(d.errorCount > 0 || d.warningCount > 0) && (
        <div className="agent-card-node-footer">
          {d.errorCount > 0 && (
            <span className="agent-card-diag error">
              <AlertTriangle size={11} aria-hidden="true" /> {d.errorCount} error{d.errorCount === 1 ? '' : 's'}
            </span>
          )}
          {d.warningCount > 0 && (
            <span className="agent-card-diag warning">
              <AlertTriangle size={11} aria-hidden="true" /> {d.warningCount} warning{d.warningCount === 1 ? '' : 's'}
            </span>
          )}
        </div>
      )}
      <Handle type="source" id="out-right" position={Position.Right} className="agent-node-handle" />
      <Handle type="source" id="out-bottom" position={Position.Bottom} className="agent-node-handle" />
    </div>
  )
})
