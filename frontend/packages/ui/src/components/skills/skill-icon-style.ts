export const SKILL_ICON_COLORS: Record<string, { bg: string; fg: string }> = {
  A: { bg: "#f3f2ff", fg: "#725cf9" },
  B: { bg: "#eef4ff", fg: "#4c74d9" },
  C: { bg: "#ecfbff", fg: "#2f8fbd" },
  D: { bg: "#effaf5", fg: "#2f9d6a" },
  E: { bg: "#f5f8e9", fg: "#7d9330" },
  F: { bg: "#fff8e8", fg: "#c58a18" },
  G: { bg: "#fff1ec", fg: "#d66a3a" },
  H: { bg: "#fff0f5", fg: "#d75d88" },
  I: { bg: "#f7efff", fg: "#8d55d8" },
  J: { bg: "#eef0ff", fg: "#5c68df" },
  K: { bg: "#eaf7ff", fg: "#3b8fd4" },
  L: { bg: "#eafbf7", fg: "#2f9f8b" },
  M: { bg: "#f0faee", fg: "#53a84d" },
  N: { bg: "#f8f7e8", fg: "#9b9135" },
  O: { bg: "#fff4e3", fg: "#c77c10" },
  P: { bg: "#fff0ea", fg: "#d45f3c" },
  Q: { bg: "#fcefff", fg: "#b552ce" },
  R: { bg: "#f1f0ff", fg: "#7160e8" },
  S: { bg: "#edf5ff", fg: "#4b7fd7" },
  T: { bg: "#e9fbff", fg: "#2694b0" },
  U: { bg: "#edfaf1", fg: "#36a35c" },
  V: { bg: "#f6f9e7", fg: "#82972d" },
  W: { bg: "#fff6e6", fg: "#bd8516" },
  X: { bg: "#fff1f0", fg: "#d9584f" },
  Y: { bg: "#f9f0ff", fg: "#9b59d8" },
  Z: { bg: "#f0f2ff", fg: "#6371df" },
};

export const getSkillIconLetter = (name: string): string => {
  const firstLetter = name.trim().charAt(0).toUpperCase();
  return /^[A-Z]$/.test(firstLetter) ? firstLetter : "A";
};

export const getSkillIconStyle = (
  name: string,
): { letter: string; bg: string; fg: string } => {
  const letter = getSkillIconLetter(name);
  const color = SKILL_ICON_COLORS[letter];
  return { letter, ...color };
};
