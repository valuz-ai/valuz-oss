import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../ui/card";

interface LogViewerProps {
  title?: string;
  description?: string;
  logs: string[];
}

export const LogViewer = ({
  title = "Runtime logs",
  description = "Latest output from the desktop bootstrap sequence.",
  logs,
}: LogViewerProps) => (
  <Card className="dark:border-white/10 dark:bg-white/5 border-surface-border bg-surface-soft">
    <CardHeader className="gap-2">
      <CardTitle className="text-base dark:text-white text-ink-heading">
        {title}
      </CardTitle>
      <CardDescription className="dark:text-slate-300 text-ink-body">
        {description}
      </CardDescription>
    </CardHeader>
    <CardContent>
      <div className="desktop-log-panel max-h-64 dark:border-white/10 dark:bg-black/30 dark:text-slate-100 border-surface-border bg-surface-base text-ink-body">
        {logs.length > 0 ? (
          logs.map((line) => <p key={line}>{line}</p>)
        ) : (
          <p>No runtime logs yet.</p>
        )}
      </div>
    </CardContent>
  </Card>
);
