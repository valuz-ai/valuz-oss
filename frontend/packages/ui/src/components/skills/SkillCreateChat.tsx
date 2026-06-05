import { useState, useRef, useEffect } from "react";
import { Loader2, Bot, User, Sparkles } from "lucide-react";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface GeneratedFile {
  name: string;
  content: string;
  language?: string;
}

export interface SkillCreateChatProps {
  onSubmit: (description: string) => void;
  onSave: () => void;
  onCancel: () => void;
  messages: ChatMessage[];
  generatedFiles: GeneratedFile[];
  generating: boolean;
  saving: boolean;
  skillName?: string;
}

const SAMPLE_PROMPTS = [
  "help me auto-write a semiconductor industry weekly report",
  "comparative analysis of two companies' financial data",
  "generate an investment research report template with industry overview and risk alerts",
];

export const SkillCreateChat = ({
  onSubmit,
  onSave,
  onCancel,
  messages,
  generatedFiles,
  generating,
  saving,
  skillName,
}: SkillCreateChatProps) => {
  const { t } = useI18n();
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (generatedFiles.length > 0 && !selectedFile) {
      setSelectedFile(generatedFiles[0].name);
    }
  }, [generatedFiles, selectedFile]);

  const currentFile = generatedFiles.find((f) => f.name === selectedFile);
  const canSave = generatedFiles.length > 0 && !generating;

  return (
    <div className="flex h-full">
      {/* Left panel — Chat */}
      <div className="flex w-1/2 flex-col border-r border-surface-border">
        <div className="border-b border-surface-border px-4 py-3">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-brand" />
            <span className="text-sm font-medium text-ink-heading">
              {t("skill.aiCreate")}
            </span>
          </div>
          <p className="mt-1 text-xs text-ink-body">
            {t("skill.livePreviewHint")}
          </p>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-3">
          {messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-4">
              <Bot className="h-10 w-10 text-ink-muted" />
              <p className="text-sm text-ink-body">{t("skill.aiCreate")}</p>
              <div className="flex flex-wrap justify-center gap-2">
                {SAMPLE_PROMPTS.map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => setInput(prompt)}
                    className="rounded-lg border border-surface-border px-3 py-1.5 text-xs text-ink-body transition hover:border-brand/30 hover:bg-brand/5"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              {messages.map((msg, i) => (
                <div
                  key={i}
                  className={cn(
                    "flex gap-2.5",
                    msg.role === "user" ? "justify-end" : "justify-start",
                  )}
                >
                  {msg.role === "assistant" && (
                    <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand/10">
                      <Bot className="h-3.5 w-3.5 text-brand" />
                    </div>
                  )}
                  <div
                    className={cn(
                      "max-w-[80%] rounded-lg px-3 py-2 text-xs leading-relaxed",
                      msg.role === "user"
                        ? "bg-brand text-white"
                        : "bg-surface-soft text-ink-body",
                    )}
                  >
                    {msg.content}
                  </div>
                  {msg.role === "user" && (
                    <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface-soft">
                      <User className="h-3.5 w-3.5 text-ink-muted" />
                    </div>
                  )}
                </div>
              ))}
              {generating && (
                <div className="flex items-center gap-2 text-xs text-ink-meta">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  {t("common.loading")}
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* Input */}
        <div className="border-t border-surface-border p-3">
          <div className="flex gap-2">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={t("skill.descPlaceholder")}
              rows={2}
              className="min-h-[60px] flex-1 resize-none text-xs"
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (input.trim() && !generating) {
                    onSubmit(input.trim());
                    setInput("");
                  }
                }
              }}
            />
            <Button
              size="sm"
              disabled={!input.trim() || generating}
              onClick={() => {
                if (input.trim()) {
                  onSubmit(input.trim());
                  setInput("");
                }
              }}
            >
              {t("common.send")}
            </Button>
          </div>
        </div>
      </div>

      {/* Right panel — Preview */}
      <div className="flex w-1/2 flex-col">
        <div className="border-b border-surface-border px-4 py-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-ink-heading">
              {t("skill.livePreview")}
            </span>
            {skillName && (
              <span className="rounded bg-surface-soft px-2 py-0.5 text-2xs text-ink-meta">
                {skillName}
              </span>
            )}
          </div>
        </div>

        {generatedFiles.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 text-ink-meta">
            <Sparkles className="h-8 w-8" />
            <p className="text-xs">{t("skill.livePreviewHint")}</p>
          </div>
        ) : (
          <div className="flex flex-1 overflow-hidden">
            {/* File tree */}
            <div className="w-[160px] shrink-0 border-r border-surface-border bg-surface-soft p-2">
              {generatedFiles.map((file) => (
                <button
                  key={file.name}
                  type="button"
                  onClick={() => setSelectedFile(file.name)}
                  className={cn(
                    "flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-xs transition",
                    selectedFile === file.name
                      ? "bg-card text-ink-heading shadow-xs"
                      : "text-ink-body hover:bg-card/50",
                  )}
                >
                  <span className="shrink-0 text-[10px]">
                    {file.name.endsWith(".md")
                      ? "📝"
                      : file.name.endsWith(".json")
                        ? "{ }"
                        : "📄"}
                  </span>
                  <span className="truncate">{file.name}</span>
                </button>
              ))}
            </div>

            {/* File content */}
            <div className="flex-1 overflow-auto p-3">
              {currentFile && (
                <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-ink-label">
                  {currentFile.content}
                </pre>
              )}
            </div>
          </div>
        )}

        {/* Bottom action bar */}
        <div className="flex items-center justify-end gap-2 border-t border-surface-border px-4 py-3">
          <Button variant="outline" onClick={onCancel}>
            {t("common.cancel")}
          </Button>
          <Button onClick={onSave} disabled={!canSave || saving}>
            {saving && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            {t("skill.saveToLib")}
          </Button>
        </div>
      </div>
    </div>
  );
};
