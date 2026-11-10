import {
  formatGb,
  formatPixels,
  planAction,
  planAdvice,
  planRows,
  sceneCosts,
  type SceneFacts,
} from "../concurrencyPlan";

function limitLabel(id: string, bound: "cpu" | "memory", slots: number, nMsBands: number): string {
  if (id === "write" && slots >= nMsBands) return "一起写出";
  return bound === "cpu" ? "按核数" : "按内存";
}

export default function ResourcePlan({
  scene,
  memoryGb,
  cpus,
  orthoScale,
  memoryCeilingGb,
  freeMemoryGb,
  cpuCeiling,
  disabled,
  onApply,
}: {
  scene: SceneFacts;
  memoryGb: number;
  cpus: number;
  orthoScale: number;
  memoryCeilingGb: number | null;
  freeMemoryGb: number | null;
  cpuCeiling: number | null;
  disabled?: boolean;
  onApply: (memoryGb: number, cpus: number) => void;
}) {
  const rows = planRows(scene, memoryGb, cpus, orthoScale);
  const costs = sceneCosts(scene, orthoScale);
  const advice = planAdvice(scene, memoryGb, cpus, orthoScale, memoryCeilingGb, cpuCeiling, freeMemoryGb);
  const action = planAction(scene, memoryGb, cpus, orthoScale, memoryCeilingGb, cpuCeiling, freeMemoryGb);
  const bandLine =
    scene.nMsBands > 0 ? `彩色 + ${scene.nMsBands} 个多光谱` : "彩色";
  const frame = costs.pixels;
  return (
    <section className="resource-plan">
      <header className="resource-plan-head">
        <div>
          <div className="resource-plan-kicker">测区并发</div>
          <div className="resource-plan-meta">
            {scene.width}×{scene.height}
            <span> · </span>
            {bandLine}
            <span> · </span>
            {scene.nColor} 张
            {frame ? (
              <>
                <span> · </span>
                正射约 {formatPixels(frame.width)} × {formatPixels(frame.height)}
              </>
            ) : null}
          </div>
        </div>
        <div className="resource-plan-budget">
          {Math.round(cpus)} 核 · {formatGb(memoryGb)} GB
        </div>
      </header>
      <div className="resource-plan-stats">
        {rows.map((row) => (
          <div key={row.id} className="resource-plan-stat">
            <div className="resource-plan-stat-label">{row.label}</div>
            <div className="resource-plan-stat-value">
              {row.of != null && row.slots < row.of ? `${row.slots}/${row.of}` : row.slots}
              <small>{row.unit}</small>
            </div>
            <div className="resource-plan-stat-sub">
              {row.id === "write" ? "每个 " : ""}
              {formatGb(row.perGb)} GB · {limitLabel(row.id, row.bound, row.slots, scene.nMsBands)}
            </div>
          </div>
        ))}
      </div>
      <footer className={`resource-plan-foot${action ? "" : " resource-plan-foot--note-only"}`}>
        <p>{advice}</p>
        {action ? (
          <button
            type="button"
            className="resource-plan-action"
            disabled={disabled}
            onClick={() => onApply(action.memoryGb, action.cpus)}
          >
            {action.label}
          </button>
        ) : null}
      </footer>
    </section>
  );
}
