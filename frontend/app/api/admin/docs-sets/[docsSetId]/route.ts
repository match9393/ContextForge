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

function parseDocsSetId(raw: string): number {
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) {
    return 0;
  }
  return Math.max(Math.floor(parsed), 0);
}

export async function DELETE(_request: Request, { params }: { params: { docsSetId: string } }) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { allowed, superadminToken } = resolveAdminAccess(session.user.email);
  if (!allowed) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const docsSetId = parseDocsSetId(params.docsSetId);
  if (docsSetId <= 0) {
    return NextResponse.json({ error: "Invalid docs set id" }, { status: 400 });
  }

  let response: Response;
  try {
    response = await fetch(`${backendUrl}/api/v1/admin/docs-sets/${docsSetId}`, {
      method: "DELETE",
      headers: {
        "X-User-Email": session.user.email,
        "X-User-Name": session.user.name || "",
        ...(superadminToken ? { "X-Superadmin-Token": superadminToken } : {}),
      },
      cache: "no-store",
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to contact backend";
    return NextResponse.json({ error: message }, { status: 502 });
  }

  const data = await readBackendPayload(response);
  return NextResponse.json(data, { status: response.status });
}
