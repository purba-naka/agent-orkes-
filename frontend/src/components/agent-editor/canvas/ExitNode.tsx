// ---------------------------------------------------------------------------
// Exit node — named exits rendered as pills.
//
// Synthetic node: it does not exist in document.nodes, it is built from
// document.named_exits with the id `__exit_<name>` (matching what
// extractEdgePairs produces). Not selectable, no config panel.
// ---------------------------------------------------------------------------

import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { NodeProps } from '@xyflow/react'
import { LogOut } from 'lucide-react'

export interface ExitNodeData {
  exitName: string
}

export const ExitNode = memo(function ExitNode({ data }: NodeProps) {
  const d = data as unknown as ExitNodeData
  return (
    <div className="exit-node" title={`Exit: ${d.exitName}`}>
      <Handle type="target" id="in-left" position={Position.Left} className="agent-node-handle" />
      <Handle type="target" id="in-top" position={Position.Top} className="agent-node-handle" />
      <LogOut size={12} aria-hidden="true" />
      <span>{d.exitName}</span>
      <span className="exit-node-tag">END</span>
    </div>
  )
})
