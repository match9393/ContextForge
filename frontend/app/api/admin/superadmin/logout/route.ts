import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { SUPERADMIN_COOKIE_NAME } from "@/lib/admin-access";

const secureCookie = process.env.NODE_ENV === "production";

export async function POST() {
  cookies().set({
    name: SUPERADMIN_COOKIE_NAME,
    value: "",
    httpOnly: true,
    sameSite: "lax",
    secure: secureCookie,
    path: "/",
    maxAge: 0,
  });
  return NextResponse.json({ status: "ok" });
}
