"use client";

import { FormEvent, useEffect, useState } from "react";

type AdminDocument = {
  id: number;
  source_type: string;
  source_name: string;
  source_url?: string | null;
  status: string;
  text_chunk_count: number;
  image_count: number;
  created_at: string;
  created_by_email?: string | null;
};

type AdminDocumentsResponse = {
  documents: AdminDocument[];
};

type AdminAskHistory = {
  id: number;
  created_at: string;
  user_email: string;
  question: string;
  fallback_mode: string;
  retrieval_outcome: string;
  confidence_percent: number;
  grounded: boolean;
  documents_used: Array<{ document_id?: number; source_name?: string; source_type?: string }>;
  chunks_used: number[];
  images_used: number[];
  webpage_links: string[];
  evidence: Record<string, unknown>;
};

type AdminAskHistoryResponse = {
  history: AdminAskHistory[];
};

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function summarizeSources(item: AdminAskHistory): string {
  const documentNames = item.documents_used
    .map((doc) => doc.source_name?.trim())
    .filter((name): name is string => Boolean(name));
  if (documentNames.length > 0) {
    return documentNames.join(", ");
  }
  if (item.webpage_links.length > 0) {
    return item.webpage_links.join(", ");
  }
  return "No source trace stored";
}

export function AdminPanel() {
  const [documents, setDocuments] = useState<AdminDocument[]>([]);
  const [history, setHistory] = useState<AdminAskHistory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);

  const [deleteCandidate, setDeleteCandidate] = useState<AdminDocument | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function loadDocuments() {
    const response = await fetch("/api/admin/documents?limit=100", { cache: "no-store" });
    const data = (await response.json()) as AdminDocumentsResponse & { error?: string; detail?: string };
    if (!response.ok) {
      throw new Error(data.error || data.detail || "Failed to load documents.");
    }
    setDocuments(data.documents || []);
  }

  async function loadHistory() {
    const response = await fetch("/api/admin/ask-history?limit=40", { cache: "no-store" });
    const data = (await response.json()) as AdminAskHistoryResponse & { error?: string; detail?: string };
    if (!response.ok) {
      throw new Error(data.error || data.detail || "Failed to load ask history.");
    }
    setHistory(data.history || []);
  }

  async function loadAll() {
    setLoading(true);
    setError(null);
    try {
      await Promise.all([loadDocuments(), loadHistory()]);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Failed to load admin data.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadAll();
  }, []);

  async function onUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setUploadMessage(null);

    if (!uploadFile) {
      setUploadMessage("Please choose a PDF file first.");
      return;
    }

    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", uploadFile, uploadFile.name);

      const response = await fetch("/api/admin/documents", {
        method: "POST",
        body: formData,
      });
      const data = (await response.json()) as { error?: string; detail?: string; document_id?: number };

      if (!response.ok) {
        setUploadMessage(data.error || data.detail || "Upload failed.");
        return;
      }

      setUploadMessage(`Uploaded document #${data.document_id ?? "?"}.`);
      setUploadFile(null);
      await loadDocuments();
    } catch {
      setUploadMessage("Upload failed due to a network error.");
    } finally {
      setUploading(false);
    }
  }

  async function confirmDelete() {
    if (!deleteCandidate) {
      return;
    }

    setDeleting(true);
    setUploadMessage(null);
    try {
      const response = await fetch(`/api/admin/documents/${deleteCandidate.id}`, {
        method: "DELETE",
      });
      const data = (await response.json()) as { error?: string; detail?: string };
      if (!response.ok) {
        setUploadMessage(data.error || data.detail || "Delete failed.");
        return;
      }

      setDeleteCandidate(null);
      await loadDocuments();
    } catch {
      setUploadMessage("Delete failed due to a network error.");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <section className="admin-shell">
      <div className="admin-head">
        <h2>Admin</h2>
        <button type="button" className="button secondary" onClick={() => void loadAll()} disabled={loading}>
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      <section className="admin-card">
        <h3>PDF Upload</h3>
        <form className="admin-upload-form" onSubmit={onUpload}>
          <input
            type="file"
            accept="application/pdf"
            onChange={(event) => setUploadFile(event.target.files?.[0] || null)}
          />
          <button type="submit" disabled={uploading}>
            {uploading ? "Uploading..." : "Ingest PDF"}
          </button>
        </form>
        {uploadMessage ? <p className="meta">{uploadMessage}</p> : null}
      </section>

      {error ? <p className="error">{error}</p> : null}

      <section className="admin-card">
        <h3>Documents</h3>
        {documents.length === 0 ? (
          <p className="meta">No documents indexed yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Name</th>
                  <th>Status</th>
                  <th>Chunks</th>
                  <th>Images</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {documents.map((document) => (
                  <tr key={document.id}>
                    <td>{document.id}</td>
                    <td>{document.source_name}</td>
                    <td>{document.status}</td>
                    <td>{document.text_chunk_count}</td>
                    <td>{document.image_count}</td>
                    <td>{formatDate(document.created_at)}</td>
                    <td>
                      <button
                        type="button"
                        className="button danger"
                        onClick={() => setDeleteCandidate(document)}
                        disabled={deleting}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="admin-card">
        <h3>Ask History</h3>
        {history.length === 0 ? (
          <p className="meta">No questions logged yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>User</th>
                  <th>Question</th>
                  <th>Retrieval</th>
                  <th>Confidence</th>
                  <th>Chunks</th>
                  <th>Images</th>
                  <th>Source Trace</th>
                </tr>
              </thead>
              <tbody>
                {history.map((item) => (
                  <tr key={item.id}>
                    <td>{formatDate(item.created_at)}</td>
                    <td>{item.user_email}</td>
                    <td>{item.question}</td>
                    <td>
                      {item.retrieval_outcome} / {item.fallback_mode}
                    </td>
                    <td>{item.confidence_percent}%</td>
                    <td>{item.chunks_used.length}</td>
                    <td>{item.images_used.length}</td>
                    <td>{summarizeSources(item)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {deleteCandidate ? (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal-card">
            <h3>Delete document?</h3>
            <p>
              This will immediately remove <strong>{deleteCandidate.source_name}</strong>, including its chunks and
              extracted images.
            </p>
            <div className="modal-actions">
              <button type="button" className="button secondary" onClick={() => setDeleteCandidate(null)} disabled={deleting}>
                Cancel
              </button>
              <button type="button" className="button danger" onClick={() => void confirmDelete()} disabled={deleting}>
                {deleting ? "Deleting..." : "Delete now"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
