import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { resolveAdminAccess } from "@/lib/admin-access";
import { authOptions } from "@/lib/auth";

const backendUrl = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";

type RolePayload = {
  email: string;
  role: "user" | "admin";
};

export async function POST(request: Request) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { allowed, superadminToken } = resolveAdminAccess(session.user.email);
  if (!allowed) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const payload = (await request.json()) as RolePayload;
  if (!payload.email?.trim()) {
    return NextResponse.json({ error: "Email is required" }, { status: 400 });
  }
  if (!(payload.role === "user" || payload.role === "admin")) {
    return NextResponse.json({ error: "Invalid role" }, { status: 400 });
  }

  const response = await fetch(`${backendUrl}/api/v1/admin/users/role`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-User-Email": session.user.email,
      "X-User-Name": session.user.name || "",
      ...(superadminToken ? { "X-Superadmin-Token": superadminToken } : {}),
    },
    body: JSON.stringify({
      email: payload.email.trim(),
      role: payload.role,
    }),
    cache: "no-store",
  });

  const data = await response.json();
  return NextResponse.json(data, { status: response.status });
}
