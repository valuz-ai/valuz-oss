"use client";

import { defineComponent } from "@openuidev/react-lang";

import { ProfileTileSchema } from "./schema";

export { ProfileTileSchema } from "./schema";

/**
 * Initials for the avatar fallback. Latin names collapse to first + last
 * initial; a single token (including CJK names, which carry no spaces) keeps
 * its first two characters. `Array.from` so astral characters — an emoji
 * nickname, say — are not split down the middle into replacement glyphs.
 */
function initialsOf(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return Array.from(words[0] ?? "").slice(0, 2).join("");
  const first = Array.from(words[0] ?? "")[0] ?? "";
  const last = Array.from(words[words.length - 1] ?? "")[0] ?? "";
  return `${first}${last}`;
}

export const ProfileTile = defineComponent({
  name: "ProfileTile",
  props: ProfileTileSchema,
  description:
    "A person or organisation: name, an optional role beneath it, an optional detail line (email, location, holding size), and an optional avatarUrl. " +
    "When avatarUrl is absent the initials of name are drawn instead, so never invent an image URL just to fill the circle. " +
    "Use SmallCardBlock or MediumCardBlock to lay out a team, a panel of speakers, or a list of entities.",
  component: ({ props }) => (
    <div className="vgb-tile vgb-profile-tile" data-slot="vgb-profile-tile">
      {props.avatarUrl ? (
        <img className="vgb-profile-avatar" src={props.avatarUrl} alt="" loading="lazy" />
      ) : (
        <span className="vgb-profile-avatar" aria-hidden="true">
          {initialsOf(props.name)}
        </span>
      )}
      <span className="vgb-profile-text">
        <span className="vgb-profile-name">{props.name}</span>
        {props.role ? <span className="vgb-profile-role">{props.role}</span> : null}
        {props.detail ? <span className="vgb-profile-detail">{props.detail}</span> : null}
      </span>
    </div>
  ),
});
