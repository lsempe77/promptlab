export function ModelFilter({
  models,
  selected,
  onToggle,
  onSelectAll,
  onSelectNone,
}: {
  models: string[];
  selected: Set<string>;
  onToggle: (modelId: string) => void;
  onSelectAll: () => void;
  onSelectNone: () => void;
}) {
  return (
    <details className="model-filter">
      <summary>
        Models shown ({selected.size}/{models.length}) ▾
      </summary>
      <div className="model-filter-panel">
        <div className="model-filter-actions">
          <button type="button" className="link-btn" onClick={onSelectAll}>
            select all
          </button>
          <button type="button" className="link-btn" onClick={onSelectNone}>
            select none
          </button>
        </div>
        {models.map((m) => (
          <label key={m} className="model-filter-option">
            <input type="checkbox" checked={selected.has(m)} onChange={() => onToggle(m)} />
            <span>{m}</span>
          </label>
        ))}
      </div>
    </details>
  );
}
