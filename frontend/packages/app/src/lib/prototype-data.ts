import type { PrototypeToolCall } from "@valuz/shared";

export const homeSuggestions = [
  "帮我分析英伟达最新季度财报，重点看数据中心业务",
  "对比新能源车板块近一月涨跌幅与资金流向",
  "写一份半导体行业 2025 年展望的周报大纲",
  "从最新研报中提取关于宁德时代的观点摘要",
];

export const conversationToolCalls: PrototypeToolCall[] = [
  {
    id: "tc-kb",
    kind: "kb",
    title: "kb_search",
    subtitle: "检索「英伟达 Q4 数据中心」",
    status: "success",
    output: "命中 3 份文档: Q4 财报原文、CFO 电话会议纪要、投资者简报。",
  },
  {
    id: "tc-web",
    kind: "fetch",
    title: "fetch_stock_price",
    subtitle: "获取 NVDA 最新行情",
    status: "success",
    output: "$912.34 (+2.14%)  市值 $2.24T  VIX 16.8",
  },
  {
    id: "tc-skill",
    kind: "skill",
    title: "skill: 研报撰写模板",
    subtitle: "按模板整理财报要点",
    status: "running",
    output:
      "正在生成: 一、业绩概览 / 二、分业务拆解 / 三、毛利率分析 / 四、展望与风险...",
  },
];

export type KbDocStatus = "ready" | "indexing" | "failed" | "queued";

export interface KbDoc {
  id: string;
  name: string;
  size: string;
  format: string;
  importedAt: string;
  status: KbDocStatus;
  chunks?: number;
  progress?: number;
  preview?: string;
}

export const kbDocs: KbDoc[] = [
  {
    id: "kb1",
    name: "英伟达 FY25Q4 财报原文.pdf",
    size: "6.8 MB",
    format: "PDF",
    importedAt: "今天 14:26",
    status: "ready",
    chunks: 148,
    preview:
      "Data Center revenue grew sharply as hyperscaler and sovereign demand remained elevated across AI training and inference workloads.",
  },
  {
    id: "kb2",
    name: "Q4 电话会议纪要.md",
    size: "412 KB",
    format: "MD",
    importedAt: "今天 12:08",
    status: "ready",
    chunks: 96,
    preview:
      "Management emphasized supply constraints easing into the second half, while Blackwell ramps remain margin-dilutive in the near term.",
  },
  {
    id: "kb3",
    name: "Investor Deck OCR.txt",
    size: "1.1 MB",
    format: "TXT",
    importedAt: "昨天 20:14",
    status: "indexing",
    progress: 67,
  },
  {
    id: "kb4",
    name: "外部研究纪要-半导体景气度.docx",
    size: "784 KB",
    format: "DOCX",
    importedAt: "昨天 11:42",
    status: "failed",
  },
  {
    id: "kb5",
    name: "宏观时间序列-出口与PMI.xlsx",
    size: "2.4 MB",
    format: "XLSX",
    importedAt: "2026-04-20",
    status: "ready",
    chunks: 54,
    preview:
      "制造业 PMI 回升至 50.8，出口交货值环比改善，电子链条修复节奏领先可选消费。",
  },
];

export const knowledgeLibraryMeta: Record<
  string,
  {
    source: string;
    owner: string;
    hits: string;
    summary: string;
    collection: string;
  }
> = {
  kb1: {
    source: "上传自项目文件夹",
    owner: "英伟达深度研究",
    hits: "128 次命中",
    summary: "公司原始财报全文，作为季度分析和估值模型的主要底稿。",
    collection: "Quarterly Filing",
  },
  kb2: {
    source: "手动上传",
    owner: "半导体专题库",
    hits: "74 次命中",
    summary: "电话会议纪要，适合抽取管理层指引、资本开支与需求节奏表述。",
    collection: "Earnings Call",
  },
  kb3: {
    source: "同步知识包",
    owner: "行业对比",
    hits: "43 次命中",
    summary: "投资者简报截图和文字解析结果，适合补齐图表与产品路线信息。",
    collection: "Investor Deck",
  },
  kb4: {
    source: "邮件转存",
    owner: "宏观监控",
    hits: "12 次命中",
    summary: "外部研究纪要，当前等待进一步索引和结构清洗。",
    collection: "Research Note",
  },
  kb5: {
    source: "Excel 数据包导入",
    owner: "宏观监控",
    hits: "31 次命中",
    summary: "宏观时间序列表格，适合在会话中直接做区间对比和趋势抽取。",
    collection: "Macro Dataset",
  },
};

export type SkillSource = "official" | "custom";

export interface PrototypeSkill {
  id: string;
  name: string;
  description: string;
  tags: string[];
  source: SkillSource;
  locked?: boolean;
  version: string;
}

export const skillsLibrary: PrototypeSkill[] = [
  {
    id: "s1",
    name: "研报撰写模板",
    description:
      "提供标准化研报流程，包括行业概览、公司基本面、估值模型和风险提示。",
    tags: ["投研"],
    source: "official",
    version: "1.4.0",
  },
  {
    id: "s2",
    name: "行业对比框架",
    description: "多维度行业对比分析，自动拉取同业数据并生成结构化对比表。",
    tags: ["数据分析"],
    source: "official",
    version: "1.2.3",
  },
  {
    id: "s3",
    name: "DCF 估值",
    description: "现金流折现估值助手，支持假设参数敏感性分析。",
    tags: ["工具", "投研"],
    source: "official",
    locked: true,
    version: "2.0.1",
  },
  {
    id: "s4",
    name: "财报电话会纪要",
    description: "上传录音或文字稿，自动抽取问答重点与管理层 Guidance。",
    tags: ["写作"],
    source: "official",
    version: "1.1.8",
  },
  {
    id: "s5",
    name: "周报速成",
    description: "按周度节奏聚合关注池行情、公告、研报并生成草稿。",
    tags: ["写作", "自动化"],
    source: "custom",
    version: "0.3.2",
  },
  {
    id: "s6",
    name: "我的分析框架",
    description: "个人自定义行业研究模板，偏重消费电子与半导体。",
    tags: ["投研"],
    source: "custom",
    version: "0.1.9",
  },
];

export const skillFilters = [
  "全部",
  "投研",
  "数据分析",
  "写作",
  "工具",
  "自动化",
];

export const toolCallGallery: { label: string; calls: PrototypeToolCall[] }[] =
  [
    {
      label: "数据源调用",
      calls: [
        {
          id: "g1",
          kind: "fetch",
          title: "fetch_stock_price",
          subtitle: "NVDA 最新行情",
          status: "success",
          input: '{ "symbol": "NVDA", "period": "1d" }',
          output: "NVDA  $875.32  +2.41%  vol 52.3M  mktcap $2.15T",
        },
        {
          id: "g2",
          kind: "fetch",
          title: "fetch_financial_report",
          subtitle: "NVDA 2024-Q4",
          status: "cached",
          input: '{ "symbol": "NVDA", "quarter": "2024Q4" }',
          output:
            "revenue: $22.1B (+265% YoY)\ndata_center: $18.4B\ngross_margin: 76.7%",
        },
      ],
    },
    {
      label: "Skill 调用",
      calls: [
        {
          id: "g3",
          kind: "skill",
          title: "研报撰写模板",
          subtitle: "按标准模板整理财报要点",
          status: "running",
          input:
            '{ "sector": "半导体", "company": "NVDA", "framework": "SWOT" }',
        },
        {
          id: "g4",
          kind: "skill",
          title: "行业对比框架",
          subtitle: "NVDA / AMD / INTC",
          status: "success",
          input:
            '{ "peers": ["NVDA", "AMD", "INTC"], "metrics": ["pe", "ps", "roe"] }',
          output: "生成 peers_comparison.xlsx (3 sheets, 48 rows)",
        },
      ],
    },
    {
      label: "知识库检索",
      calls: [
        {
          id: "g5",
          kind: "kb",
          title: "kb_search",
          subtitle: "数据中心 毛利率",
          status: "success",
          input: '{ "query": "数据中心 毛利率", "top_k": 5 }',
          output:
            "命中 5 个分片:\n- 英伟达 2024 Q4 财报.pdf p.12\n- 英伟达 2024 Q4 财报.pdf p.14\n- 半导体行业趋势分析.md §3.2",
        },
      ],
    },
    {
      label: "文件与命令",
      calls: [
        {
          id: "g6",
          kind: "file",
          title: "file_read",
          subtitle: "读取本地财报",
          status: "success",
          input:
            '{ "path": "~/research/nvidia-2025/Q4_earnings.pdf", "pages": "10-15" }',
          output: "读取 6 页 · 共 2348 字",
        },
        {
          id: "g7",
          kind: "bash",
          title: "bash",
          subtitle: "清理并聚合营收数据",
          status: "success",
          input:
            "grep \"revenue\" data/*.csv | awk '{sum+=$3} END {print sum}'",
          output: "22140.5",
        },
        {
          id: "g8",
          kind: "bash",
          title: "bash",
          subtitle: "尝试访问外部 API",
          status: "error",
          input: "curl https://internal.api.example.com/data",
          output: "curl: (28) Failed to connect — Connection timed out",
        },
      ],
    },
  ];

export const scheduledGroups = [
  {
    space: "Chat",
    tasks: [
      {
        name: "今日财报速览",
        prompt: "抓取 A 股 / 港股 / 美股当日财报并速览",
        trigger: "每日 08:00",
        last: "3h 前",
        status: "on" as const,
      },
    ],
  },
  {
    space: "英伟达研究",
    tasks: [
      {
        name: "行业数据日报",
        prompt: "抓取行业数据，整理结构化报告",
        trigger: "每日 09:00",
        last: "2h 前",
        status: "on" as const,
      },
      {
        name: "投研周报生成",
        prompt: "生成本周摘要报告",
        trigger: "每周五 17:00",
        last: "昨天",
        status: "on" as const,
      },
      {
        name: "盘后复盘（暂停）",
        prompt: "美股收盘后拉取盘后数据并生成复盘",
        trigger: "工作日 04:30",
        last: "4 天前",
        status: "off" as const,
      },
    ],
  },
  {
    space: "新能源周报",
    tasks: [] as {
      name: string;
      prompt: string;
      trigger: string;
      last: string;
      status: "on" | "off";
    }[],
  },
];

export const executionLog = [
  {
    time: "2026-04-13 09:00",
    status: "ok" as const,
    duration: "2m30s",
    output: "已生成报告 -> 行业日报-0413.md",
  },
  {
    time: "2026-04-12 09:00",
    status: "ok" as const,
    duration: "3m12s",
    output: "已生成报告 -> 行业日报-0412.md",
  },
  {
    time: "2026-04-11 09:00",
    status: "err" as const,
    duration: "0m45s",
    output: "LLM API 请求超时",
  },
  {
    time: "2026-04-10 09:00",
    status: "skip" as const,
    duration: "-",
    output: "跳过 · 应用未运行",
  },
];

export const onboardingSteps = [
  {
    id: "project",
    eyebrow: "Step 1",
    title: "连接你的工作空间",
    desc: "选择本地优先还是云端协同，决定 Valuz 如何访问模型、文件与知识资产。",
  },
  {
    id: "models",
    eyebrow: "Step 2",
    title: "设置模型",
    desc: "把常用的大模型接入进来，先配默认模型，后续可以在设置里继续扩充。",
  },
  {
    id: "parsing",
    eyebrow: "Step 3",
    title: "确定解析策略",
    desc: "为 PDF、扫描件和表格选择默认解析方式，平衡准确率、速度和离线能力。",
  },
  {
    id: "finish",
    eyebrow: "Step 4",
    title: "完成初始化",
    desc: "导入第一批知识文档，并生成一个带默认能力的起始研究工作区。",
  },
];

export const modelProviders = [
  {
    id: "anthropic",
    name: "Anthropic",
    desc: "主对话模型，适合长上下文研究与高质量写作。",
    endpoint: "api.anthropic.com",
    accent: "from-[#5B45FF] to-[#8778FF]",
    connected: true,
  },
  {
    id: "openai",
    name: "OpenAI",
    desc: "适合工具调用和结构化输出，作为快速任务备援。",
    endpoint: "api.openai.com",
    accent: "from-[#0EA5A4] to-[#15C6B7]",
    connected: true,
  },
  {
    id: "custom",
    name: "自定义网关",
    desc: "接入自建代理或内部模型路由层，统一配额与审计。",
    endpoint: "gateway.valuz.local",
    accent: "from-[#101828] to-[#475467]",
    connected: false,
  },
];

export const parsingModes = [
  {
    id: "local",
    name: "本地解析",
    detail: "完全离线，适合结构简单的 PDF、Markdown 和 CSV。",
    latency: "延迟最低",
  },
  {
    id: "hybrid",
    name: "混合解析",
    detail: "普通文档本地解析，复杂版面自动切到云端高级 OCR。",
    latency: "推荐默认",
  },
  {
    id: "cloud",
    name: "云端高级解析",
    detail: "扫描件、表格和图像识别最佳，但会消耗云端额度。",
    latency: "精度更高",
  },
];
