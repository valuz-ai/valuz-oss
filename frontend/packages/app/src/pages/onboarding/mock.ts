// Visual-first scaffolding for the onboarding v2 build. The shapes here mirror
// what the eventual API will return, so swapping in real data is mechanical.
// All user-facing strings are stored as i18n keys (resolved at render time via
// useTranslation) — only structural fields (emoji, status, ref, avatar, tag,
// numeric params) live as plain values. See docs/product-specs/specs/01-launch-and-login.md.

import type { I18nKey } from "@valuz/shared";

export type FeedStatus = "running" | "done" | "review" | "you";

export interface FeedCard {
  /** Stable key for React lists. */
  id: string;
  /** i18n key for the speaker's display name (agent role name or the user). */
  authorKey: I18nKey;
  /** Emoji avatar — placeholder until real agent avatars exist. */
  avatar: string;
  /** Task / thread reference chip, mirrors a Linear-style "VLZ-42".
   *  Technical id — intentionally not translated. */
  ref: string;
  /** i18n key for the message body. May contain @mentions rendered as accents. */
  bodyKey: I18nKey;
  /** Drives the status pill + accent colour. */
  status: FeedStatus;
  /** i18n key for the relative timestamp label (e.g. "time.justNow"). */
  whenKey?: I18nKey;
  /** Optional interpolation params for whenKey (e.g. {count: 2} for "2 minutes ago"). */
  whenParams?: Record<string, number>;
}

/**
 * Models offered per channel when the user connects it. Picking one both
 * connects the channel and (for the first connected channel) sets the global
 * default — folding the old separate "set default model" step into connect.
 * Real lists come from the provider/models API; these are representative mocks.
 */
export const CHANNEL_MODELS: Record<string, string[]> = {
  claude: ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
  codex: ["gpt-5", "gpt-5-mini", "o4-mini"],
  apikey: ["deepseek-chat", "deepseek-reasoner", "glm-4.6", "kimi-k2"],
};

/** A member in a team pack / showcase / AI-generated draft. */
export interface TeamMember {
  avatar: string;
  /** i18n key for the member's display name. */
  nameKey: I18nKey;
  /** Optional role tag, e.g. "lead". Stable identifier — not translated. */
  tag?: string;
  /** i18n key for the one-line responsibility shown under the name. */
  dutyKey: I18nKey;
  /** Equipped skills (mono chips). */
  skills?: string[];
}

/**
 * A team the user can pick in step 4. The first three are opinionated presets
 * (their agents get created + deployed into the example project on entry); the
 * last ("custom") has no fixed roster and routes to the "skip team" path.
 */
export type TeamId = "general" | "investment" | "product" | "custom";

export interface TeamPreset {
  id: TeamId;
  emoji: string;
  /** i18n key for team name. */
  nameKey: I18nKey;
  /** i18n key for the one-line tagline shown on the grid block. */
  taglineKey: I18nKey;
  /** The 4 roles deployed for this team (empty for custom). */
  members: TeamMember[];
  /** i18n key for the collaboration-shape note shown on the detail screen. */
  collabKey?: I18nKey;
}

export const TEAMS: TeamPreset[] = [
  {
    id: "general",
    emoji: "🧩",
    nameKey: "onboarding.teams.general.name",
    taglineKey: "onboarding.teams.general.tagline",
    collabKey: "onboarding.teams.general.collab",
    members: [
      {
        avatar: "🔎",
        nameKey: "onboarding.roles.general.researcher.name",
        tag: "lead",
        dutyKey: "onboarding.roles.general.researcher.duty",
      },
      {
        avatar: "✍️",
        nameKey: "onboarding.roles.general.writer.name",
        dutyKey: "onboarding.roles.general.writer.duty",
      },
      {
        avatar: "🔍",
        nameKey: "onboarding.roles.general.reviewer.name",
        dutyKey: "onboarding.roles.general.reviewer.duty",
      },
      {
        avatar: "🗂️",
        nameKey: "onboarding.roles.general.archivist.name",
        dutyKey: "onboarding.roles.general.archivist.duty",
      },
    ],
  },
  {
    id: "investment",
    emoji: "💎",
    nameKey: "onboarding.teams.investment.name",
    taglineKey: "onboarding.teams.investment.tagline",
    collabKey: "onboarding.teams.investment.collab",
    members: [
      {
        avatar: "🧭",
        nameKey: "onboarding.roles.investment.analyst.name",
        tag: "lead",
        dutyKey: "onboarding.roles.investment.analyst.duty",
      },
      {
        avatar: "📊",
        nameKey: "onboarding.roles.investment.modeler.name",
        dutyKey: "onboarding.roles.investment.modeler.duty",
      },
      {
        avatar: "📈",
        nameKey: "onboarding.roles.investment.tracker.name",
        dutyKey: "onboarding.roles.investment.tracker.duty",
      },
      {
        avatar: "📋",
        nameKey: "onboarding.roles.investment.compliance.name",
        dutyKey: "onboarding.roles.investment.compliance.duty",
      },
    ],
  },
  {
    id: "product",
    emoji: "🛠️",
    nameKey: "onboarding.teams.product.name",
    taglineKey: "onboarding.teams.product.tagline",
    collabKey: "onboarding.teams.product.collab",
    members: [
      {
        avatar: "🧠",
        nameKey: "onboarding.roles.product.pm.name",
        tag: "lead",
        dutyKey: "onboarding.roles.product.pm.duty",
      },
      {
        avatar: "🎨",
        nameKey: "onboarding.roles.product.designer.name",
        dutyKey: "onboarding.roles.product.designer.duty",
      },
      {
        avatar: "💻",
        nameKey: "onboarding.roles.product.engineer.name",
        dutyKey: "onboarding.roles.product.engineer.duty",
      },
      {
        avatar: "🧪",
        nameKey: "onboarding.roles.product.qa.name",
        dutyKey: "onboarding.roles.product.qa.duty",
      },
    ],
  },
  {
    id: "custom",
    emoji: "💬",
    nameKey: "onboarding.teams.custom.name",
    taglineKey: "onboarding.teams.custom.tagline",
    members: [],
  },
];

/**
 * The welcome screen's right rail: a frozen slice of a project where an agent
 * team is collaborating. Strongest "show, don't tell" of Project-as-Agent-Team
 * — the user sees teammates picking up work and reviewing each other before
 * they've configured anything.
 */
export const WELCOME_FEED: FeedCard[] = [
  {
    id: "you",
    authorKey: "onboarding.feed.cards.userBrief.author",
    avatar: "🧑",
    ref: "VLZ-42",
    bodyKey: "onboarding.feed.cards.userBrief.body",
    status: "you",
  },
  {
    id: "researcher",
    authorKey: "onboarding.feed.cards.researcher.author",
    avatar: "🔎",
    ref: "VLZ-42",
    bodyKey: "onboarding.feed.cards.researcher.body",
    status: "running",
    whenKey: "time.justNow",
  },
  {
    id: "writer",
    authorKey: "onboarding.feed.cards.writer.author",
    avatar: "✍️",
    ref: "VLZ-43",
    bodyKey: "onboarding.feed.cards.writer.body",
    status: "done",
    whenKey: "time.minutesAgo",
    whenParams: { count: 2 },
  },
  {
    id: "reviewer",
    authorKey: "onboarding.feed.cards.reviewer.author",
    avatar: "🔍",
    ref: "VLZ-42",
    bodyKey: "onboarding.feed.cards.reviewer.body",
    status: "review",
    whenKey: "time.minutesAgo",
    whenParams: { count: 5 },
  },
];
