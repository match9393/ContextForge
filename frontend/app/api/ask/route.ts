import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { authOptions } from "@/lib/auth";

type AskRequest = {
  question: string;
};

const backendUrl = process.env.BACKEND_INTERNAL_URL || "http://backend:8000";

export async function POST(request: Request) {
  const session = await getServerSession(authOptions);

  if (!session?.user?.email) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const payload = (await request.json()) as AskRequest;
  if (!payload.question || !payload.question.trim()) {
    return NextResponse.json({ error: "Question is required" }, { status: 400 });
  }

  const response = await fetch(`${backendUrl}/api/v1/ask`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-User-Email": session.user.email,
      "X-User-Name": session.user.name || "",
    },
    body: JSON.stringify({ question: payload.question.trim() }),
    cache: "no-store",
  });

  const data = await response.json();
  return NextResponse.json(data, { status: response.status });
}
