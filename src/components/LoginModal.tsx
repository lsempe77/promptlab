import { useState } from "react";
import { API_BASE_URL } from "../api";

interface Props {
  onSuccess: (token: string) => void;
  onCancel: () => void;
}

export default function LoginModal({ onSuccess, onCancel }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const canSubmit =
    email.toLowerCase().endsWith("@3ieimpact.org") && password.length > 0;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/auth/token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (res.status === 401) {
        setError("Incorrect password, or email is not a @3ieimpact.org address.");
        return;
      }
      if (!res.ok) {
        setError(`Server error ${res.status}`);
        return;
      }
      const { token } = await res.json();
      sessionStorage.setItem("promptlab_token", token);
      onSuccess(token);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Network error");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="wizard-overlay" onClick={(ev) => ev.target === ev.currentTarget && onCancel()}>
      <div className="login-modal">
        <div className="wizard-header">
          <h2>Sign in to create a project</h2>
          <button className="wizard-close" onClick={onCancel}>✕</button>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>

          {error && <div className="wizard-error">{error}</div>}

          <label className="wizard-label">
            Work email
            <input
              className="wizard-input"
              type="email"
              placeholder="you@3ieimpact.org"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoFocus
            />
            {email && !email.toLowerCase().endsWith("@3ieimpact.org") && (
              <span className="login-domain-warn">Must be a @3ieimpact.org address</span>
            )}
          </label>

          <label className="wizard-label">
            Password
            <input
              className="wizard-input"
              type="password"
              placeholder="Shared team password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>

          <div className="login-footer">
            <button type="button" className="btn-secondary" onClick={onCancel}>
              Cancel
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={!canSubmit || loading}
            >
              {loading ? "Signing in…" : "Sign in →"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
