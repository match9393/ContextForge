import { cookies } from "next/headers";
import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { authOptions } from "@/lib/auth";
import { SUPERADMIN_COOKIE_NAME } from "@/lib/admin-access";

const backendUrl = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";

export async function GET() {
  const session = await getServerSession(authOptions);
  if (!session?.user?.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const token = cookies().get(SUPERADMIN_COOKIE_NAME)?.value || "";
  if (!token) {
    return NextResponse.json({ valid: false }, { status: 401 });
  }

  const response = await fetch(`${backendUrl}/api/v1/admin/superadmin/verify`, {
    method: "GET",
    headers: {
      "X-User-Email": session.user.email,
      "X-User-Name": session.user.name || "",
      "X-Superadmin-Token": token,
    },
    cache: "no-store",
  });

  const data = await response.json();
  return NextResponse.json(data, { status: response.status });
}
