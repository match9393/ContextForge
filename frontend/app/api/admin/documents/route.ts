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
  const { allowed, superadminToken } = resolveAdminAccess(session.user.email);
  if (!allowed) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const limit = parseLimit(request.url, 50);
  const response = await fetch(`${backendUrl}/api/v1/admin/documents?limit=${limit}`, {
    method: "GET",
    headers: {
      "X-User-Email": session.user.email,
      "X-User-Name": session.user.name || "",
      ...(superadminToken ? { "X-Superadmin-Token": superadminToken } : {}),
    },
    cache: "no-store",
  });

  const data = await readBackendPayload(response);
  return NextResponse.json(data, { status: response.status });
}

export async function POST(request: Request) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { allowed, superadminToken } = resolveAdminAccess(session.user.email);
  if (!allowed) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const formData = await request.formData();
  const file = formData.get("file");
  if (!(file instanceof File)) {
    return NextResponse.json({ error: "Missing PDF file" }, { status: 400 });
  }

  const backendForm = new FormData();
  backendForm.append("file", file, file.name);

  const response = await fetch(`${backendUrl}/api/v1/admin/ingest/pdf`, {
    method: "POST",
    headers: {
      "X-User-Email": session.user.email,
      "X-User-Name": session.user.name || "",
      ...(superadminToken ? { "X-Superadmin-Token": superadminToken } : {}),
    },
    body: backendForm,
    cache: "no-store",
  });

  const data = await readBackendPayload(response);
  return NextResponse.json(data, { status: response.status });
}
