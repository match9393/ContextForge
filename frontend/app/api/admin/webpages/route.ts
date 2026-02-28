import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { authOptions } from "@/lib/auth";
import { resolveAdminAccess } from "@/lib/admin-access";

const backendUrl = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";

async function readBackendPayload(response: Response) {
  const raw = await response.text();
  if (!raw) {
    return { error: `Backend returned empty response (${response.status}).` };
  }
  try {
    return JSON.parse(raw);
  } catch {
    return { error: raw };
  }
}

type IngestWebRequest = {
  url: string;
  docs_set_id?: number;
  docs_set_name?: string;
  parent_document_id?: number;
  discovered_link_id?: number;
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

  const payload = (await request.json()) as IngestWebRequest;
  if (!payload.url || !payload.url.trim()) {
    return NextResponse.json({ error: "URL is required" }, { status: 400 });
  }

  const backendPayload: IngestWebRequest = {
    url: payload.url.trim(),
  };
  if (typeof payload.docs_set_id === "number" && Number.isFinite(payload.docs_set_id) && payload.docs_set_id > 0) {
    backendPayload.docs_set_id = Math.floor(payload.docs_set_id);
  }
  if (payload.docs_set_name && payload.docs_set_name.trim()) {
    backendPayload.docs_set_name = payload.docs_set_name.trim();
  }
  if (
    typeof payload.parent_document_id === "number" &&
    Number.isFinite(payload.parent_document_id) &&
    payload.parent_document_id > 0
  ) {
    backendPayload.parent_document_id = Math.floor(payload.parent_document_id);
  }
  if (
    typeof payload.discovered_link_id === "number" &&
    Number.isFinite(payload.discovered_link_id) &&
    payload.discovered_link_id > 0
  ) {
    backendPayload.discovered_link_id = Math.floor(payload.discovered_link_id);
  }

  let response: Response;
  try {
    response = await fetch(`${backendUrl}/api/v1/admin/ingest/webpage`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-User-Email": session.user.email,
        "X-User-Name": session.user.name || "",
        ...(superadminToken ? { "X-Superadmin-Token": superadminToken } : {}),
      },
      body: JSON.stringify(backendPayload),
      cache: "no-store",
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to contact backend";
    return NextResponse.json({ error: message }, { status: 502 });
  }

  const data = await readBackendPayload(response);
  return NextResponse.json(data, { status: response.status });
}
