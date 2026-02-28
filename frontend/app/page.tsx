import { getServerSession } from "next-auth";

import { AskPanel } from "@/components/ask-panel";
import { authOptions } from "@/lib/auth";

export default async function HomePage() {
  const session = await getServerSession(authOptions);

  if (!session?.user?.email) {
    return (
      <main className="page">
        <section className="card">
          <h1>ContextForge</h1>
          <p>Sign in with Google to use the assistant.</p>
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
            <h1>ContextForge</h1>
            <p>Signed in as {session.user.email}</p>
          </div>
          <a className="button secondary" href="/api/auth/signout">
            Sign out
          </a>
        </div>
        <AskPanel />
      </section>
    </main>
  );
}
