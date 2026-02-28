"use client";

import { FormEvent, useEffect, useState } from "react";

import { AdminPanel } from "@/components/admin-panel";

type AdminGateProps = {
  isEmailAdmin: boolean;
};

type AccessState = "checking" | "login" | "authorized";
type AccessMode = "admin_email" | "super_admin" | null;

export function AdminGate({ isEmailAdmin }: AdminGateProps) {
  const [accessState, setAccessState] = useState<AccessState>(isEmailAdmin ? "authorized" : "checking");
  const [accessMode, setAccessMode] = useState<AccessMode>(isEmailAdmin ? "admin_email" : null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (isEmailAdmin) {
      setAccessState("authorized");
      setAccessMode("admin_email");
      return;
    }

    let cancelled = false;
    async function verifySuperadminSession() {
      try {
        const response = await fetch("/api/admin/superadmin/verify", { cache: "no-store" });
        if (!response.ok) {
          if (!cancelled) {
            setAccessState("login");
            setAccessMode(null);
          }
          return;
        }
        const payload = (await response.json()) as { valid?: boolean };
        if (!cancelled) {
          if (payload.valid) {
            setAccessState("authorized");
            setAccessMode("super_admin");
          } else {
            setAccessState("login");
            setAccessMode(null);
          }
        }
      } catch {
        if (!cancelled) {
          setAccessState("login");
          setAccessMode(null);
        }
      }
    }

    void verifySuperadminSession();
    return () => {
      cancelled = true;
    };
  }, [isEmailAdmin]);

  async function onLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedUsername = username.trim();
    if (!trimmedUsername || !password) {
      setMessage("Enter super-admin username and password.");
      return;
    }

    setSubmitting(true);
    setMessage(null);
    try {
      const response = await fetch("/api/admin/superadmin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: trimmedUsername, password }),
      });
      const data = (await response.json()) as { error?: string; detail?: string };
      if (!response.ok) {
        setMessage(data.error || data.detail || "Super-admin login failed.");
        return;
      }
      setPassword("");
      setAccessState("authorized");
      setAccessMode("super_admin");
      setMessage(null);
    } catch {
      setMessage("Super-admin login failed due to network error.");
    } finally {
      setSubmitting(false);
    }
  }

  async function onLogoutSuperadmin() {
    setSubmitting(true);
    setMessage(null);
    try {
      await fetch("/api/admin/superadmin/logout", { method: "POST" });
    } finally {
      setSubmitting(false);
      setAccessState("login");
      setAccessMode(null);
      setPassword("");
      setMessage("Super-admin session closed.");
    }
  }

  if (accessState === "checking") {
    return (
      <section className="admin-card">
        <h3>Admin Access</h3>
        <p className="meta">Checking access…</p>
      </section>
    );
  }

  if (accessState === "login") {
    return (
      <section className="admin-card">
        <h3>Super-admin Login</h3>
        <p className="meta">
          Your account is not on the admin allow-list. Use super-admin credentials to open the admin area.
        </p>
        <form className="admin-login-form" onSubmit={onLogin}>
          <input
            type="text"
            className="admin-url-input"
            placeholder="super-admin username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
          />
          <input
            type="password"
            className="admin-url-input"
            placeholder="super-admin password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
          />
          <button type="submit" disabled={submitting}>
            {submitting ? "Signing in..." : "Sign in as super-admin"}
          </button>
        </form>
        {message ? <p className="error">{message}</p> : null}
      </section>
    );
  }

  return (
    <>
      <section className="admin-card">
        <div className="admin-head">
          <h3>Admin Access</h3>
          {accessMode === "super_admin" ? (
            <button type="button" className="button secondary" onClick={() => void onLogoutSuperadmin()} disabled={submitting}>
              End super-admin session
            </button>
          ) : null}
        </div>
        <p className="meta">
          {accessMode === "admin_email"
            ? "Access granted from admin allow-list."
            : "Access granted from active super-admin session."}
        </p>
        {message ? <p className="meta">{message}</p> : null}
      </section>
      <AdminPanel />
    </>
  );
}
