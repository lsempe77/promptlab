import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** What this section represents (shown in the error message). */
  label?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * ErrorBoundary — catches errors from async data-fetching children and shows
 * a "Data unavailable — Retry" message instead of silently hiding the section.
 *
 * This replaces the old pattern of `.catch(() => setEmpty)` which silently
 * swallowed API failures and left the user staring at "—" with no explanation.
 */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary">
          <span className="error-boundary__icon">⚠</span>
          <span className="error-boundary__text">
            {this.props.label ? `${this.props.label}: ` : ""}
            {this.state.error?.message ?? "Data unavailable"}
          </span>
          <button className="error-boundary__retry" onClick={this.handleRetry}>
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
