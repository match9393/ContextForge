import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { authOptions } from "@/lib/auth";
import { isAdminEmail } from "@/lib/admin";

const backendUrl = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";

type LinkedIngestRequest = {
  source_document_id: number;
  max_pages?: number;
};

export async function POST(request: Request) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  if (!isAdminEmail(session.user.email)) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const payload = (await request.json()) as LinkedIngestRequest;
  if (
    !Number.isFinite(payload.source_document_id) ||
    Math.floor(payload.source_document_id) <= 0
  ) {
    return NextResponse.json({ error: "source_document_id is required" }, { status: 400 });
  }

  let maxPages = 20;
  if (Number.isFinite(payload.max_pages)) {
    maxPages = Math.min(Math.max(Math.floor(payload.max_pages ?? 20), 1), 100);
  }

  const response = await fetch(`${backendUrl}/api/v1/admin/ingest/webpage/linked`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-User-Email": session.user.email,
      "X-User-Name": session.user.name || "",
    },
    body: JSON.stringify({
      source_document_id: Math.floor(payload.source_document_id),
      max_pages: maxPages,
    }),
    cache: "no-store",
  });

  const data = await response.json();
  return NextResponse.json(data, { status: response.status });
}
