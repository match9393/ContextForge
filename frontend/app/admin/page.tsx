import { getServerSession } from "next-auth";

import { AdminGate } from "@/components/admin-gate";
import { authOptions } from "@/lib/auth";
import { isAdminEmail } from "@/lib/admin";

export default async function AdminPage() {
  const session = await getServerSession(authOptions);

  if (!session?.user?.email) {
    return (
      <main className="page">
        <section className="card">
          <h1>ContextForge Admin</h1>
          <p>Sign in with Google to continue.</p>
          <a className="button" href="/api/auth/signin">
            Sign in with Google
          </a>
        </section>
      </main>
    );
  }

  return (
    <main className="page">
      <section className="card">
        <div className="top-row">
          <div>
            <h1>ContextForge Admin</h1>
            <p>Signed in as {session.user.email}</p>
          </div>
          <div className="top-row-actions">
            <a className="button secondary" href="/">
              Assistant
            </a>
            <a className="button secondary" href="/api/auth/signout">
              Sign out
            </a>
          </div>
        </div>
        <AdminGate isEmailAdmin={isAdminEmail(session.user.email)} />
      </section>
    </main>
  );
}
