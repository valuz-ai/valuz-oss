// Imported first by the build tests. The workspace runs vitest in a jsdom
// environment, which replaces the global ``Uint8Array`` with jsdom's; esbuild
// then refuses to start ("new TextEncoder().encode("") instanceof Uint8Array
// is incorrectly false"). These tests run Node-side code only, so restore
// Node's own Uint8Array (the one Buffer extends).
import { Buffer } from "node:buffer";

const NodeUint8Array = Object.getPrototypeOf(Buffer.prototype)
  .constructor as Uint8ArrayConstructor;
if (globalThis.Uint8Array !== NodeUint8Array) {
  globalThis.Uint8Array = NodeUint8Array;
}

export {};
