import { cookies } from "next/headers";
import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { authOptions } from "@/lib/auth";
import { SUPERADMIN_COOKIE_NAME } from "@/lib/admin-access";

const backendUrl = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";
const secureCookie = process.env.NODE_ENV === "production";

type LoginPayload = {
  username: string;
  password: string;
};

export async function POST(request: Request) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const payload = (await request.json()) as LoginPayload;
  if (!payload.username?.trim() || !payload.password) {
    return NextResponse.json({ error: "Missing username or password" }, { status: 400 });
  }

  const response = await fetch(`${backendUrl}/api/v1/admin/superadmin/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-User-Email": session.user.email,
      "X-User-Name": session.user.name || "",
    },
    body: JSON.stringify({
      username: payload.username.trim(),
      password: payload.password,
    }),
    cache: "no-store",
  });

  const data = await response.json();
  if (!response.ok) {
    return NextResponse.json(data, { status: response.status });
  }

  const token = String(data.token || "");
  const expiresIn = Number(data.expires_in_seconds || 0);
  if (!token || expiresIn <= 0) {
    return NextResponse.json({ error: "Invalid super-admin login response" }, { status: 502 });
  }

  cookies().set({
    name: SUPERADMIN_COOKIE_NAME,
    value: token,
    httpOnly: true,
    sameSite: "lax",
    secure: secureCookie,
    path: "/",
    maxAge: Math.floor(expiresIn),
  });

  return NextResponse.json({ status: "ok", role: "super_admin" });
}
