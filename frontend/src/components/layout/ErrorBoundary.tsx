// Top-level error boundary. Shows a recoverable error pane instead of the
// React white-screen-of-death when a child component throws.

import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallbackTitle?: string;
}

interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: unknown) {
    // eslint-disable-next-line no-console
    console.error("ErrorBoundary caught", error, info);
  }

  reset = () => this.setState({ error: null });

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex h-full items-center justify-center px-6">
        <div className="max-w-lg space-y-3 rounded-2xl border border-red-500/40 bg-red-500/5 p-6">
          <h2 className="text-lg font-semibold text-red-300">
            {this.props.fallbackTitle ?? "Something went wrong"}
          </h2>
          <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-muted">
            {this.state.error.message}
          </pre>
          <button
            type="button"
            onClick={this.reset}
            className="rounded bg-accent px-3 py-1.5 text-sm font-medium text-bg"
          >
            Try again
          </button>
        </div>
      </div>
    );
  }
}
