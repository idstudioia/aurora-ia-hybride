import { useMemo } from 'react';
import { Background, Controls, ReactFlow, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { ActivityEvent, MemoryItem, TaskItem } from '../lib/types';

interface Props {
  mode: 'memories' | 'tasks';
  memories: MemoryItem[];
  tasks: TaskItem[];
  highlightedEvent: ActivityEvent | null;
}

export default function MemoryGraph({ mode, memories, tasks, highlightedEvent }: Props) {
  const { nodes, edges } = useMemo(() => {
    const records = (mode === 'memories' ? memories : tasks).slice(0, 30);
    const nodes: Node[] = [{
      id: 'aurora', type: 'default', position: { x: 360, y: 200 },
      data: { label: '✦ AURORA · ' + (mode === 'memories' ? 'Mémoire' : 'Tâches') },
      className: 'hub-node', draggable: false,
    }];
    const edges: Edge[] = [];
    records.forEach((item, index) => {
      const angle = (-Math.PI / 2) + (index * (2 * Math.PI / Math.max(records.length, 5)));
      const radiusX = 260 + Math.floor(index / 12) * 155;
      const radiusY = 150 + Math.floor(index / 12) * 95;
      const id = `${mode}-${item.id}`;
      const active = highlightedEvent?.subject_id === item.id && highlightedEvent.subject_type === (mode === 'memories' ? 'memory' : 'task');
      const status = 'status' in item ? item.status : 'saved';
      nodes.push({
        id, type: 'default', position: { x: 360 + radiusX * Math.cos(angle), y: 200 + radiusY * Math.sin(angle) },
        data: { label: item.title }, draggable: false,
        className: `flow-node ${status} ${active ? 'highlight' : ''}`,
      });
      edges.push({
        id: `link-${id}`, source: 'aurora', target: id,
        type: 'smoothstep', animated: active || status === 'running',
        style: { stroke: active ? '#78efff' : status === 'done' ? '#38d1a3' : '#376ba3', strokeWidth: active ? 3 : 1.7 },
      });
    });
    return { nodes, edges };
  }, [mode, memories, tasks, highlightedEvent]);

  return (
    <div className="graph-stage" aria-label={mode === 'memories' ? 'Carte des mémoires' : 'Carte des tâches'}>
      <ReactFlow nodes={nodes} edges={edges} fitView fitViewOptions={{ padding: 0.25 }} nodesConnectable={false}
        nodesDraggable={false} elementsSelectable={true} minZoom={0.35} maxZoom={1.7} proOptions={{ hideAttribution: true }}>
        <Background color="#164070" gap={24} size={0.7} />
        <Controls showInteractive={false} />
      </ReactFlow>
      {nodes.length === 1 && <div className="graph-empty">Aucune donnée enregistrée pour le moment.<br />Ajoutez un élément pour voir apparaître son nœud.</div>}
    </div>
  );
}
