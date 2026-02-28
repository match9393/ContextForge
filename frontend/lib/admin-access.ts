import { cookies } from "next/headers";

import { isAdminEmail } from "@/lib/admin";

export const SUPERADMIN_COOKIE_NAME = "contextforge_superadmin_token";

export function resolveAdminAccess(email?: string | null): {
  allowed: boolean;
  superadminToken: string;
} {
  const superadminToken = cookies().get(SUPERADMIN_COOKIE_NAME)?.value || "";
  const allowed = isAdminEmail(email) || Boolean(superadminToken);
  return { allowed, superadminToken };
}

