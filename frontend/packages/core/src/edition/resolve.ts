import { personalProfile } from "./personal-profile";
import type { Edition, EditionProfile } from "./profile";

declare const __EDITION__: Edition | undefined;

export const resolveEdition = (): Edition => {
  if (typeof __EDITION__ !== "undefined") {
    return __EDITION__;
  }
  return "personal";
};

// 公共骨架只内置 personalProfile。未来若引入 enterprise overlay，
// 由 overlay 包通过自己的 hydrate(profile) 接管 registry-store 即可，
// 这里不需要也不知道 enterprise 的存在。
export const getActiveProfile = (): EditionProfile => personalProfile;
