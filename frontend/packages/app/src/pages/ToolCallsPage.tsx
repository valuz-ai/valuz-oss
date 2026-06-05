import { ToolCallCard } from "@valuz/ui";
import { toolCallGallery } from "@valuz/app/lib/prototype-data";

export const ToolCallsPage = () => (
  <div className="h-full overflow-y-auto">
    <div className="mx-auto max-w-[780px] space-y-10 px-10 py-10">
      <div>
        <h1 className="mb-2 font-heading text-2xl font-semibold text-ink-heading">
          工具调用可视化
        </h1>
        <p className="max-w-[540px] text-base leading-relaxed text-ink-body">
          Agent
          在回答过程中会调用各种工具。每个调用都以可折叠卡片展示，点击展开可查看输入、输出和执行时间。
        </p>
      </div>
      {toolCallGallery.map((group) => (
        <section key={group.label}>
          <h2 className="label-mono mb-3 font-heading">{group.label}</h2>
          <div className="space-y-4">
            {group.calls.map((c) => (
              <ToolCallCard key={c.id} tc={c} />
            ))}
          </div>
        </section>
      ))}
    </div>
  </div>
);
