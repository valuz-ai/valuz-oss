"use strict";
/**
 * Stand-in for node-addon-require-builtin (see package.json for why).
 *
 * Same API as the unrestricted upstream variant: requireBuiltin(id) returns
 * Node's builtin module ``id`` — internal ones included — and
 * isAllowedInternalId() is always true. Internal ids resolve through the
 * ordinary require, which Node permits only under --expose-internals; dsh's
 * own HMR and plugin loader already take that path first when the flag is
 * present, so this keeps every caller on one mechanism.
 */
const EXPOSED = process.execArgv.includes("--expose-internals");

function requireBuiltin(moduleId) {
  if (!EXPOSED && String(moduleId).startsWith("internal/")) {
    throw new Error(
      `node-addon-require-builtin (Valuz stand-in): ${moduleId} needs the process to ` +
        "run with --expose-internals — launch dsh through Valuz's runtime resolution",
    );
  }
  return require(moduleId);
}

function isAllowedInternalId() {
  return true;
}

function getBindingInfo() {
  return { variant: "valuz-expose-internals", exposed: EXPOSED };
}

exports.requireBuiltin = requireBuiltin;
exports.isAllowedInternalId = isAllowedInternalId;
exports.getBindingInfo = getBindingInfo;
exports.default = { requireBuiltin, isAllowedInternalId, getBindingInfo };
