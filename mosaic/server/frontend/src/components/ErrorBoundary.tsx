import { Component, type ReactNode } from "react";
import { Alert, Button } from "antd";

type Props = { children: ReactNode; title?: string };
type State = { error: Error | null };

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <Alert
        type="error"
        showIcon
        message={this.props.title || "这一页出错了"}
        description={this.state.error.message || String(this.state.error)}
        action={
          <Button size="small" onClick={() => this.setState({ error: null })}>
            重试
          </Button>
        }
      />
    );
  }
}
