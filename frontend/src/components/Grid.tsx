import type { Episode, Position } from "../types";

const CELL = 100; // SVG units per grid cell

const same = (a: Position, b: Position) => a.row === b.row && a.col === b.col;
const center = ({ row, col }: Position) => ({ x: (col + 0.5) * CELL, y: (row + 0.5) * CELL });

function describe({ layout, position }: Episode, cell: Position): string {
  let type = "Free";
  if (same(cell, layout.start)) type = "Start";
  else if (same(cell, layout.goal)) type = "Destination";
  else if (layout.obstacles.some((p) => same(p, cell))) type = "Obstacle";
  else if (layout.hazards.some((p) => same(p, cell))) type = "Hazard";
  const agent = same(cell, position) ? ", agent" : "";
  return `Row ${cell.row}, column ${cell.col}: ${type}${agent}`;
}

/** A numbered black disc, used for the start and the destination. */
function Node({ x, y, label }: { x: number; y: number; label: string }) {
  return (
    <g className="node">
      <circle cx={x} cy={y} r={30} />
      <text x={x} y={y}>
        {label}
      </text>
    </g>
  );
}

function Obstacle({ x, y }: { x: number; y: number }) {
  return <rect className="obstacle" x={x - 38} y={y - 38} width={76} height={76} rx={14} />;
}

function Hazard({ x, y }: { x: number; y: number }) {
  return (
    <g className="hazard" transform={`translate(${x} ${y})`}>
      <path d="M0 -30 L32 24 L-32 24 Z" />
      <text y={5}>!</text>
    </g>
  );
}

export function Grid({ episode }: { episode: Episode }) {
  const { layout, position, path = [] } = episode;
  const size = layout.grid_size * CELL;
  const indices = Array.from({ length: layout.grid_size }, (_, i) => i);
  const cells = indices.flatMap((row) => indices.map((col) => ({ row, col })));
  const agent = center(position);

  return (
    <figure className="board">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={`Environment grid. Agent at row ${position.row}, column ${position.col}.`}
      >
        <rect className="board-bg" x={2} y={2} width={size - 4} height={size - 4} rx={28} />
        {indices.slice(1).map((i) => (
          <path key={i} className="gridline" d={`M${i * CELL} 2V${size - 2}M2 ${i * CELL}H${size - 2}`} />
        ))}

        {/* Later elements are drawn on top: trail, agent, then the cell markers. */}
        <polyline
          className="trail"
          points={path.map((p) => `${center(p).x},${center(p).y}`).join(" ")}
        />
        <circle className="agent" cx={agent.x} cy={agent.y} r={39} />
        {layout.obstacles.map((p) => (
          <Obstacle key={`${p.row},${p.col}`} {...center(p)} />
        ))}
        {layout.hazards.map((p) => (
          <Hazard key={`${p.row},${p.col}`} {...center(p)} />
        ))}
        <Node {...center(layout.start)} label="1" />
        <Node {...center(layout.goal)} label="2" />

        {/* Invisible hover targets that name each cell. */}
        {cells.map((cell) => (
          <rect
            key={`${cell.row},${cell.col}`}
            className="hit"
            x={cell.col * CELL}
            y={cell.row * CELL}
            width={CELL}
            height={CELL}
          >
            <title>{describe(episode, cell)}</title>
          </rect>
        ))}
      </svg>

      <figcaption className="legend">
        <span>
          <svg viewBox="0 0 200 100" className="wide" aria-hidden="true">
            <polyline className="trail" points="40,50 150,50" />
            <circle className="agent" cx={150} cy={50} r={39} />
          </svg>
          Agent and path
        </span>
        <span>
          <svg viewBox="0 0 100 100" aria-hidden="true">
            <Node x={50} y={50} label="1" />
          </svg>
          Start
        </span>
        <span>
          <svg viewBox="0 0 100 100" aria-hidden="true">
            <Node x={50} y={50} label="2" />
          </svg>
          Destination
        </span>
        <span>
          <svg viewBox="0 0 100 100" aria-hidden="true">
            <Obstacle x={50} y={50} />
          </svg>
          Obstacle
        </span>
        <span>
          <svg viewBox="0 0 100 100" aria-hidden="true">
            <Hazard x={50} y={50} />
          </svg>
          Hazard
        </span>
      </figcaption>
    </figure>
  );
}
