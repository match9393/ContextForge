import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { authOptions } from "@/lib/auth";
import { resolveAdminAccess } from "@/lib/admin-access";

const backendUrl = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";

type DeleteContext = {
  params: {
    documentId: string;
  };
};

export async function DELETE(_request: Request, context: DeleteContext) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { allowed, superadminToken } = resolveAdminAccess(session.user.email);
  if (!allowed) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const documentId = Number(context.params.documentId);
  if (!Number.isFinite(documentId) || documentId <= 0) {
    return NextResponse.json({ error: "Invalid document id" }, { status: 400 });
  }

  const response = await fetch(`${backendUrl}/api/v1/admin/documents/${documentId}`, {
    method: "DELETE",
    headers: {
      "X-User-Email": session.user.email,
      "X-User-Name": session.user.name || "",
      ...(superadminToken ? { "X-Superadmin-Token": superadminToken } : {}),
    },
    cache: "no-store",
  });

  const data = await response.json();
  return NextResponse.json(data, { status: response.status });
}

export async function POST(_request: Request, context: DeleteContext) {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { allowed, superadminToken } = resolveAdminAccess(session.user.email);
  if (!allowed) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const documentId = Number(context.params.documentId);
  if (!Number.isFinite(documentId) || documentId <= 0) {
    return NextResponse.json({ error: "Invalid document id" }, { status: 400 });
  }

  const response = await fetch(`${backendUrl}/api/v1/admin/documents/${documentId}/reingest`, {
    method: "POST",
    headers: {
      "X-User-Email": session.user.email,
      "X-User-Name": session.user.name || "",
      ...(superadminToken ? { "X-Superadmin-Token": superadminToken } : {}),
    },
    cache: "no-store",
  });

  const data = await response.json();
  return NextResponse.json(data, { status: response.status });
}
