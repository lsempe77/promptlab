import type { PromptVersion } from "../api";

function badge(accepted: number) {
  return accepted ? (
    <span className="badge badge-accepted">accepted</span>
  ) : (
    <span className="badge badge-rejected">rejected</span>
  );
}

export function PromptLineage({ versions }: { versions: PromptVersion[] }) {
  if (versions.length === 0) {
    return <p className="muted">No prompt versions logged yet for this field.</p>;
  }
  return (
    <ol className="lineage">
      {versions.map((v) => (
        <li key={v.id} className={v.accepted ? "lineage-item accepted" : "lineage-item rejected"}>
          <div className="lineage-header">
            <span className="lineage-version">v{v.version}</span>
            {badge(v.accepted)}
            {v.parent_id != null && <span className="muted">parent v{v.parent_id}</span>}
            <span className="muted lineage-date">{new Date(v.created_at).toLocaleString()}</span>
          </div>
          <p className="lineage-template">{v.template}</p>
          {v.notes && <p className="lineage-notes">{v.notes}</p>}
        </li>
      ))}
    </ol>
  );
}
