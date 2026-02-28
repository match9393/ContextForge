import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { authOptions } from "@/lib/auth";
import { isAdminEmail } from "@/lib/admin";

const backendUrl = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";

function parseLimit(url: string, defaultValue: number) {
  const searchParams = new URL(url).searchParams;
  const raw = searchParams.get("limit");
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) {
    return defaultValue;
  }
  return Math.min(Math.max(Math.floor(parsed), 1), 200);
}

export async function GET(request: Request) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  if (!isAdminEmail(session.user.email)) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const limit = parseLimit(request.url, 40);
  const response = await fetch(`${backendUrl}/api/v1/admin/ask-history?limit=${limit}`, {
    method: "GET",
    headers: {
      "X-User-Email": session.user.email,
      "X-User-Name": session.user.name || "",
    },
    cache: "no-store",
  });

  const data = await response.json();
  return NextResponse.json(data, { status: response.status });
}
