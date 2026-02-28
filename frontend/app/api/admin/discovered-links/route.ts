import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { authOptions } from "@/lib/auth";
import { resolveAdminAccess } from "@/lib/admin-access";

const backendUrl = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";

function parseIntParam(value: string | null, fallback: number, minValue: number, maxValue: number) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(Math.max(Math.floor(parsed), minValue), maxValue);
}

export async function GET(request: Request) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { allowed, superadminToken } = resolveAdminAccess(session.user.email);
  if (!allowed) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const searchParams = new URL(request.url).searchParams;
  const sourceDocumentId = parseIntParam(searchParams.get("source_document_id"), 0, 1, 1_000_000_000);
  if (sourceDocumentId <= 0) {
    return NextResponse.json({ error: "source_document_id is required" }, { status: 400 });
  }
  const limit = parseIntParam(searchParams.get("limit"), 200, 1, 1000);

  const response = await fetch(
    `${backendUrl}/api/v1/admin/discovered-links?source_document_id=${sourceDocumentId}&limit=${limit}`,
    {
      method: "GET",
      headers: {
        "X-User-Email": session.user.email,
        "X-User-Name": session.user.name || "",
        ...(superadminToken ? { "X-Superadmin-Token": superadminToken } : {}),
      },
      cache: "no-store",
    },
  );

  const data = await response.json();
  return NextResponse.json(data, { status: response.status });
}
