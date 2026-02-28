import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { authOptions } from "@/lib/auth";
import { isAdminEmail } from "@/lib/admin";

const backendUrl = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";

type IngestWebRequest = {
  url: string;
};

export async function POST(request: Request) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  if (!isAdminEmail(session.user.email)) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const payload = (await request.json()) as IngestWebRequest;
  if (!payload.url || !payload.url.trim()) {
    return NextResponse.json({ error: "URL is required" }, { status: 400 });
  }

  const response = await fetch(`${backendUrl}/api/v1/admin/ingest/webpage`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-User-Email": session.user.email,
      "X-User-Name": session.user.name || "",
    },
    body: JSON.stringify({ url: payload.url.trim() }),
    cache: "no-store",
  });

  const data = await response.json();
  return NextResponse.json(data, { status: response.status });
}
