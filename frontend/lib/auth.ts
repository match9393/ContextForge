import type { NextAuthOptions } from "next-auth";
import GoogleProvider from "next-auth/providers/google";

const rawAllowedDomains = process.env.ALLOWED_GOOGLE_DOMAINS || "netaxis.be";

function isAllowedDomain(email?: string | null): boolean {
  if (!email) {
    return false;
  }

  if (rawAllowedDomains.trim() === "*") {
    return true;
  }

  const emailDomain = email.split("@")[1]?.toLowerCase();
  if (!emailDomain) {
    return false;
  }

  const allowed = rawAllowedDomains
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean);

  return allowed.includes(emailDomain);
}

export const authOptions: NextAuthOptions = {
  providers: [
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID || "",
      clientSecret: process.env.GOOGLE_CLIENT_SECRET || "",
    }),
  ],
  session: {
    strategy: "jwt",
  },
  callbacks: {
    async signIn({ user }) {
      return isAllowedDomain(user.email);
    },
  },
  pages: {
    error: "/auth-error",
  },
  secret: process.env.NEXTAUTH_SECRET,
};
