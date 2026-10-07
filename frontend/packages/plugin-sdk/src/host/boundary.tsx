import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  pluginId: string;
  where: string;
  onError: (message: string) => void;
  children?: ReactNode;
}

interface State {
  failed: boolean;
}

/**
 * Keeps a crashing plugin component from taking its host surface with it: the
 * contribution renders nothing and the error goes to the plugin's log.
 */
export class AppPluginBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    const stack = info.componentStack ?? "";
    this.props.onError(
      `${this.props.where} crashed: ${error.stack ?? error.message}${stack}`,
    );
  }

  render(): ReactNode {
    return this.state.failed ? null : this.props.children;
  }
}
