import { z } from "zod/v4";

export const ProfileTileSchema = z.object({
  name: z.string(),
  role: z.string().optional(),
  detail: z.string().optional(),
  avatarUrl: z.string().optional(),
});
