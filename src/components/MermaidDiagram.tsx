import { useEffect, useRef, useState } from "react";
import mermaid from "mermaid";

// Static, trusted diagrams only (no user input), so "loose" is safe and lets
// node labels use <br/> line breaks. Light theme to match the 3ie dashboard.
mermaid.initialize({
  startOnLoad: false,
  theme: "neutral",
  securityLevel: "loose",
  flowchart: { htmlLabels: true, curve: "basis" },
});

let idCounter = 0;

export function MermaidDiagram({ chart, caption }: { chart: string; caption?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const id = `mermaid-${idCounter++}`;
    mermaid
      .render(id, chart)
      .then(({ svg }) => {
        if (!cancelled && ref.current) ref.current.innerHTML = svg;
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [chart]);

  if (error) {
    return <pre className="mermaid-error">{error}</pre>;
  }
  return (
    <figure className="mermaid-figure">
      <div className="mermaid-diagram" ref={ref} role="img" aria-label={caption ?? "Process diagram"} />
      {caption && <figcaption className="muted">{caption}</figcaption>}
    </figure>
  );
}
