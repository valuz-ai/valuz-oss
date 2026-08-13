/**
 * The host state channel (AG-UI semantics over A2UI v0.9): data-ref
 * parameter resolution, the data-slot path convention, STATE_SNAPSHOT /
 * STATE_DELTA folding over JSON Patch, and the polling scheduler behind
 * timed refresh — minInterval floor, visibility pause, backoff, and
 * 424 stop-and-serve-stale.
 *
 * This is the host side of the M2 loop and deliberately edition-agnostic:
 * nothing here knows a data source, an API client, or a domain. The edition
 * boundary is two injected seams, both defined in `scheduler.ts`:
 *
 *   - `SourceRegistryLookup`: `(sourceId) => {ttlMs, minIntervalSec} | undefined`
 *   - `SlotFetcher`: `(sourceId, params) => Promise<FetchResult<unknown>>`
 *
 * An edition satisfies them from its own registry and data client, then
 * pushes each result at the channel's slot path as a native A2UI
 * `updateDataModel` message — the renderer resolves `{path}` bindings
 * against the surface's DataModel, so a pushed slot re-renders whatever
 * is bound to it. Clock and visibility are injected too, which is what
 * lets the scheduler be tested without timers.
 */

export {
  describeMissingParams,
  isHostParam,
  isStateParam,
  parseDataRef,
  resolveParams,
} from "./dataRef";
export type {
  DataRef,
  DataRefParamValue,
  DataRefRefresh,
  HostParamRef,
  MissingParam,
  ParamsMissing,
  ParamsResolution,
  ParamsResolved,
  RenderContext,
  ResolvedParamValue,
  ResolvedParams,
  StateParamRef,
} from "./dataRef";

export { slotPath } from "./slots";

export {
  buildSnapshot,
  classifyOutcome,
  emptyForMissingParams,
  foldToDelta,
} from "./patch";
export type {
  FetchResult,
  JsonPatchOp,
  SlotState,
  SlotValue,
  StateDeltaMessage,
  StateSnapshotMessage,
} from "./patch";

export {
  createScheduler,
  documentVisibilitySource,
  systemClock,
} from "./scheduler";
export type {
  BackoffPolicy,
  RegisterRefInput,
  Scheduler,
  SchedulerClock,
  SchedulerDeps,
  SlotFetcher,
  SourceMeta,
  SourceRegistryLookup,
  VisibilitySource,
} from "./scheduler";

export {
  getGenUIDataHost,
  registerGenUIDataHost,
  unregisterGenUIDataHost,
} from "./host-registry";
export type {
  GenUIDataHostFactory,
  GenUIDataHostHandle,
  GenUIDataHostInput,
  GenUIComponentDataRef,
} from "./host-registry";
