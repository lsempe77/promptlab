import { useRef } from "react";
import type { WizardState } from "./types";

interface Props {
  state: WizardState;
  update: (patch: Partial<WizardState>) => void;
  onNext: () => void;
  onBack: () => void;
}

export default function Step3CorpusUpload({ state, update, onNext, onBack }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const files = state.corpusFiles;

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return;
    const existing = new Set(files.map((f) => f.name));
    const newOnes = Array.from(incoming).filter((f) => !existing.has(f.name));
    update({ corpusFiles: [...files, ...newOnes] });
  };

  const removeFile = (name: string) =>
    update({ corpusFiles: files.filter((f) => f.name !== name) });

  const pdfCount = files.filter((f) => f.name.endsWith(".pdf")).length;
  const mdCount = files.filter((f) => f.name.endsWith(".md") || f.name.endsWith(".txt")).length;

  return (
    <div className="wizard-step">
      <h3 className="step-title">Upload your corpus</h3>
      <p className="step-subtitle">
        Drop the documents you want to process. PDFs are converted to markdown automatically.
        Markdown and plain-text files are used as-is.
      </p>

      <div
        className="drop-zone"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); addFiles(e.dataTransfer.files); }}
        onClick={() => inputRef.current?.click()}
      >
        <span className="drop-icon">📁</span>
        <p>Drag PDFs or markdown files here, or <u>click to browse</u></p>
        <p className="drop-hint">Accepted: .pdf · .md · .txt</p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.md,.txt"
          style={{ display: "none" }}
          onChange={(e) => addFiles(e.target.files)}
        />
      </div>

      {files.length > 0 && (
        <div className="file-list">
          <div className="file-list-summary">
            {files.length} file{files.length !== 1 ? "s" : ""}
            {pdfCount > 0 && <> · {pdfCount} PDF{pdfCount !== 1 ? "s" : ""} (will be converted)</>}
            {mdCount > 0 && <> · {mdCount} markdown/text</>}
          </div>
          {files.map((f) => (
            <div key={f.name} className="file-row">
              <span className="file-icon">{f.name.endsWith(".pdf") ? "📄" : "📝"}</span>
              <span className="file-name">{f.name}</span>
              <span className="file-size">{(f.size / 1024).toFixed(0)} KB</span>
              <button className="file-remove" onClick={() => removeFile(f.name)}>✕</button>
            </div>
          ))}
        </div>
      )}

      <div className="wizard-footer">
        <button className="btn-secondary" onClick={onBack}>← Back</button>
        <button className="btn-primary" onClick={onNext} disabled={files.length === 0}>
          Continue →
        </button>
      </div>
    </div>
  );
}
