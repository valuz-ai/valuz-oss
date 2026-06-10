import { useEffect } from "react";
import { ChatContextPanel, ProjectContextPanel } from "@valuz/ui";
import { useProjectOutlet } from "@valuz/app/layout";

export const ContextPanelPage = () => {
  const { setRightPanel } = useProjectOutlet();

  useEffect(() => {
    setRightPanel(<ProjectContextPanel />);
    return () => setRightPanel(null);
  }, [setRightPanel]);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[780px] space-y-10 px-10 py-10">
        <div>
          <h1 className="mb-2 font-heading text-2xl font-semibold text-ink-heading">
            上下文面板
          </h1>
          <p className="max-w-[540px] text-base leading-relaxed text-ink-body">
            右侧面板汇总当前会话的产出物与项目级配置。Chat
            模式下展示待办、生成文件和上传文件；Project
            模式额外展示指示词、已选技能和项目文件树。
          </p>
        </div>

        <section>
          <h2 className="label-mono mb-3 font-heading">Chat 模式面板</h2>
          <div className="w-full max-w-[320px] overflow-hidden rounded-xl border border-surface-border bg-surface-soft">
            <ChatContextPanel />
          </div>
        </section>

        <section>
          <h2 className="label-mono mb-3 font-heading">
            Project 模式面板（右侧展示）
          </h2>
          <p className="text-sm text-ink-body">
            见本页右侧侧边栏：包含上下文与文件两个 Tab，上下文 Tab
            进一步区分本次会话与项目级两块。
          </p>
        </section>
      </div>
    </div>
  );
};
