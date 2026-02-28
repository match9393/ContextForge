const rawAdminEmails = process.env.ADMIN_EMAILS || "";

export function isAdminEmail(email?: string | null): boolean {
  if (!email) {
    return false;
  }

  const configured = rawAdminEmails.trim();
  if (configured === "*") {
    return true;
  }
  if (!configured) {
    return false;
  }

  const normalizedEmail = email.trim().toLowerCase();
  const allowed = configured
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean);

  return allowed.includes(normalizedEmail);
}
