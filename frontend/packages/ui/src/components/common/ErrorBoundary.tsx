import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="flex h-full flex-col items-center justify-center gap-4 p-8">
          <img
            src="/logo.png"
            alt=""
            className="h-10 w-10 opacity-40"
            draggable={false}
          />
          <p className="text-sm text-ink-body">Something went wrong.</p>
          <button
            type="button"
            onClick={() => this.setState({ error: null })}
            className="rounded-md bg-brand px-3 py-1.5 text-xs text-white transition-colors hover:bg-brand-hover"
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
