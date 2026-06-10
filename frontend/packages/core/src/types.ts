/**
 * Isomorphic type-only re-exports for Node-safe consumption.
 *
 * Desktop main process and build tools can `import type { X } from
 * "@valuz/core/types"` without pulling in browser-only runtime code
 * (transports, stores, React hooks).
 *
 * Keep this file free of any runtime import — only `export type` and
 * `import type` allowed.
 */

// Edition
export type {
  Edition,
  EditionProfile,
  FeatureFlags,
  DesktopRouteModule,
  SettingsSectionModule,
  ProjectPanelModule,
  BrandingProfile,
  NavItemModule,
  ServiceDescriptor,
} from "./edition/profile";

// Platform
export type { PlatformCapabilities } from "./platform/types";

// API response types (re-exported from @valuz/shared)
export type {
  SystemStatusResponse,
  SystemHealthStatus,
  RuntimeId,
} from "@valuz/shared";
export type {
  ProviderAuthType,
  ApiProtocol,
  ProviderDescriptor,
  ProviderListItem,
  ProviderDetail,
  ConnectionTestResult,
  PingResponse,
  ProbeModelsResponse,
  DiscoverModelsResponse,
} from "@valuz/shared";
export type {
  ConnectorItem,
  OauthCredentialField,
  CatalogConnector,
  CatalogGroup,
  CatalogItem,
  CatalogEntry,
} from "@valuz/shared";
export type {
  SessionDetail,
  SessionListItem,
  SessionPermissionMode,
  SessionEventDTO,
  EffortLevel,
  TodoItem,
  RequiresActionSubject,
  SessionRulePreview,
  RequiresActionEvent,
  ActionResolvedEvent,
} from "@valuz/shared";

// Internal API types not yet in shared
export type {
  SessionEventsResponse,
  SessionEventWindowResponse,
  SessionRunResponse,
  SessionCreateRequest,
  SessionMessageRequest,
  SessionActionDecision,
  SessionActionRequest,
  SessionActionResponse,
  SessionAttachmentItem,
} from "./api/sessions-api";

export type { ModelDefaults, PreferencesResponse } from "./api/settings-api";

export type {
  ProjectListItem,
  ProjectDetail,
  ProjectDeletePreview,
  ProjectFileNode,
  LastSessionPick,
} from "./api/projects-api";

export type {
  SkillView,
  SkillDetail,
  SkillOrigin,
  SkillDeletePreview,
  SkillImportArchivePreview,
  SkillImportCandidate,
  SkillCreationContext,
  SkillCreateStartResponse,
  SkillSubmissionConfirmResponse,
  SkillSubmissionDismissResponse,
} from "./api/skills-api";

export type {
  KbListItem,
  KbDetail,
  KbTreeNode,
  BindingItem,
  DocStatus,
  DocListItem,
  DocDetail,
  DocsHealth,
} from "./api/docs-api";

export type { RuntimeListItem } from "./api/runtimes-api";
