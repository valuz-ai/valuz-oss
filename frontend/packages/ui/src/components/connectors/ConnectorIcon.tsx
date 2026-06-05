import { useState } from "react";
import { cn } from "../../lib/cn";

export interface ConnectorIconProps {
  name: string;
  /** Brand icon URL from the catalog. Falls back to a letter avatar when
   *  absent or when the image fails to load. */
  iconUrl?: string | null;
  /** Tailwind size + radius classes for the outer box. */
  className?: string;
}

// Deterministic pastel background derived from the connector name so the
// fallback avatar is stable across renders (mirrors the skill-icon idea
// without coupling to the skills helper, which isn't exported).
const PALETTE: { bg: string; fg: string }[] = [
  { bg: "#eef0ff", fg: "#5a63b6" },
  { bg: "#fde8d4", fg: "#b45309" },
  { bg: "#dbeafe", fg: "#1d4ed8" },
  { bg: "#dcfce7", fg: "#15803d" },
  { bg: "#fce7f3", fg: "#be185d" },
  { bg: "#ede9fe", fg: "#6d28d9" },
];

function pickStyle(name: string): { bg: string; fg: string } {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return PALETTE[hash % PALETTE.length];
}

export const ConnectorIcon = ({
  name,
  iconUrl,
  className,
}: ConnectorIconProps) => {
  const [broken, setBroken] = useState(false);
  const style = pickStyle(name);
  const letter = (name.trim()[0] ?? "?").toUpperCase();

  if (iconUrl && !broken) {
    return (
      <div
        className={cn(
          "flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-white",
          className,
        )}
      >
        <img
          src={iconUrl}
          alt={name}
          className="h-full w-full object-contain"
          onError={() => setBroken(true)}
        />
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-lg font-semibold",
        className,
      )}
      style={{ backgroundColor: style.bg, color: style.fg }}
    >
      {letter}
    </div>
  );
};
