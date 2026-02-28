const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export default function HomePage() {
  return (
    <main className="page">
      <section className="card">
        <h1>ContextForge</h1>
        <p>
          Frontend scaffold is live. Backend health endpoint target:
          <code>{apiBaseUrl}/health</code>
        </p>
      </section>
    </main>
  );
}
