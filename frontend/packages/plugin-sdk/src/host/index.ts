export {
  EXTENSIONS_SETTINGS_GROUP,
  PluginContractError,
  adaptThirdPartyPlugin,
  isPublicSlot,
  type AdaptOptions,
  type AdaptedPlugin,
  type ThirdPartyPluginItem,
} from "./adapt";
export { adaptSlotProps } from "./convert";
export {
  SHARED_MODULES_GLOBAL,
  SHARED_MODULE_IDS,
  createSharedModuleTable,
  installSharedModules,
  type SharedModuleTable,
} from "./shared-modules";
export {
  getHostServices,
  setHostServices,
  type HostRequest,
  type HostServices,
} from "../services";
