/**
 * @valuz/plugin-sdk/testing — load a plugin into a fresh host without starting
 * Valuz: assert what it registered, render its slots, mock ``ctx.valuz``.
 */
export {
  createTestHost,
  type TestCapture,
  type TestHost,
  type TestHostCalls,
  type TestHostOptions,
} from "./test-host";
export {
  mockValuz,
  type MockValuz,
  type MockValuzCall,
  type MockValuzSpec,
} from "./mock-valuz";
export { PluginContractError } from "../host/adapt";
