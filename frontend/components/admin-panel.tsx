"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

type AdminDocument = {
  id: number;
  source_type: string;
  source_name: string;
  source_url?: string | null;
  source_storage_key?: string | null;
  source_parent_document_id?: number | null;
  docs_set_id?: number | null;
  docs_set_name?: string | null;
  status: string;
  text_chunk_count: number;
  image_count: number;
  created_at: string;
  created_by_email?: string | null;
};

type AdminDocumentsResponse = {
  documents: AdminDocument[];
};

type AdminDocsSet = {
  id: number;
  name: string;
  root_url?: string | null;
  source_type: string;
  created_at: string;
  created_by_email?: string | null;
  document_count: number;
};

type AdminDocsSetsResponse = {
  docs_sets: AdminDocsSet[];
};

type AdminDiscoveredLink = {
  id: number;
  source_document_id: number;
  docs_set_id?: number | null;
  url: string;
  normalized_url: string;
  link_text?: string | null;
  same_domain: boolean;
  status: string;
  ingested_document_id?: number | null;
  last_error?: string | null;
  created_at: string;
  updated_at: string;
};

type AdminDiscoveredLinksResponse = {
  links: AdminDiscoveredLink[];
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
  const [docsSets, setDocsSets] = useState<AdminDocsSet[]>([]);
  const [history, setHistory] = useState<AdminAskHistory[]>([]);
  const [discoveredLinks, setDiscoveredLinks] = useState<AdminDiscoveredLink[]>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [infoMessage, setInfoMessage] = useState<string | null>(null);

  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);

  const [webUrl, setWebUrl] = useState("");
  const [selectedDocsSetId, setSelectedDocsSetId] = useState("");
  const [newDocsSetName, setNewDocsSetName] = useState("");
  const [ingestingWeb, setIngestingWeb] = useState(false);

  const [selectedWebDocumentId, setSelectedWebDocumentId] = useState<number | null>(null);
  const [linksLoading, setLinksLoading] = useState(false);
  const [ingestingLinkedBatch, setIngestingLinkedBatch] = useState(false);
  const [ingestingLinkId, setIngestingLinkId] = useState<number | null>(null);
  const [linkedBatchMaxPages, setLinkedBatchMaxPages] = useState(20);

  const [deleteCandidate, setDeleteCandidate] = useState<AdminDocument | null>(null);
  const [deleting, setDeleting] = useState(false);

  const selectedWebDocument = useMemo(() => {
    if (!selectedWebDocumentId) {
      return null;
    }
    return documents.find((document) => document.id === selectedWebDocumentId) || null;
  }, [documents, selectedWebDocumentId]);

  async function loadDocuments() {
    const response = await fetch("/api/admin/documents?limit=200", { cache: "no-store" });
    const data = (await response.json()) as AdminDocumentsResponse & { error?: string; detail?: string };
    if (!response.ok) {
      throw new Error(data.error || data.detail || "Failed to load documents.");
    }
    setDocuments(data.documents || []);
  }

  async function loadDocsSets() {
    const response = await fetch("/api/admin/docs-sets?limit=300", { cache: "no-store" });
    const data = (await response.json()) as AdminDocsSetsResponse & { error?: string; detail?: string };
    if (!response.ok) {
      throw new Error(data.error || data.detail || "Failed to load docs sets.");
    }
    setDocsSets(data.docs_sets || []);
  }

  async function loadHistory() {
    const response = await fetch("/api/admin/ask-history?limit=40", { cache: "no-store" });
    const data = (await response.json()) as AdminAskHistoryResponse & { error?: string; detail?: string };
    if (!response.ok) {
      throw new Error(data.error || data.detail || "Failed to load ask history.");
    }
    setHistory(data.history || []);
  }

  async function loadDiscoveredLinks(sourceDocumentId: number) {
    setLinksLoading(true);
    try {
      const response = await fetch(`/api/admin/discovered-links?source_document_id=${sourceDocumentId}&limit=500`, {
        cache: "no-store",
      });
      const data = (await response.json()) as AdminDiscoveredLinksResponse & { error?: string; detail?: string };
      if (!response.ok) {
        throw new Error(data.error || data.detail || "Failed to load discovered links.");
      }
      setDiscoveredLinks(data.links || []);
    } finally {
      setLinksLoading(false);
    }
  }

  async function loadAll() {
    setLoading(true);
    setError(null);
    try {
      await Promise.all([loadDocuments(), loadDocsSets(), loadHistory()]);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Failed to load admin data.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadAll();
  }, []);

  useEffect(() => {
    if (!selectedWebDocumentId) {
      setDiscoveredLinks([]);
      return;
    }
    void loadDiscoveredLinks(selectedWebDocumentId).catch((loadError) => {
      setError(loadError instanceof Error ? loadError.message : "Failed to load discovered links.");
    });
  }, [selectedWebDocumentId]);

  async function onUploadPdf(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setInfoMessage(null);
    if (!uploadFile) {
      setInfoMessage("Please choose a PDF file first.");
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
        setInfoMessage(data.error || data.detail || "PDF ingest failed.");
        return;
      }

      setInfoMessage(`Ingested PDF document #${data.document_id ?? "?"}.`);
      setUploadFile(null);
      await loadDocuments();
    } catch {
      setInfoMessage("PDF ingest failed due to a network error.");
    } finally {
      setUploading(false);
    }
  }

  async function onIngestWebpage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setInfoMessage(null);
    const normalizedUrl = webUrl.trim();
    if (!normalizedUrl) {
      setInfoMessage("Please enter a webpage URL.");
      return;
    }

    const payload: Record<string, unknown> = { url: normalizedUrl };
    if (selectedDocsSetId) {
      payload.docs_set_id = Number(selectedDocsSetId);
    } else if (newDocsSetName.trim()) {
      payload.docs_set_name = newDocsSetName.trim();
    }

    setIngestingWeb(true);
    try {
      const response = await fetch("/api/admin/webpages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = (await response.json()) as {
        error?: string;
        detail?: string;
        document_id?: number;
        source_name?: string;
        docs_set_id?: number;
      };
      if (!response.ok) {
        setInfoMessage(data.error || data.detail || "Webpage ingestion failed.");
        return;
      }

      setInfoMessage(
        `Ingested webpage #${data.document_id ?? "?"}${data.source_name ? ` (${data.source_name})` : ""}.`,
      );
      setWebUrl("");
      if (data.docs_set_id) {
        setSelectedDocsSetId(String(data.docs_set_id));
      }
      setNewDocsSetName("");
      await Promise.all([loadDocuments(), loadDocsSets()]);
    } catch {
      setInfoMessage("Webpage ingestion failed due to a network error.");
    } finally {
      setIngestingWeb(false);
    }
  }

  async function onIngestSingleLink(link: AdminDiscoveredLink) {
    if (!selectedWebDocument) {
      return;
    }
    setInfoMessage(null);
    setIngestingLinkId(link.id);
    try {
      const response = await fetch("/api/admin/webpages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: link.normalized_url,
          docs_set_id: selectedWebDocument.docs_set_id,
          parent_document_id: selectedWebDocument.id,
          discovered_link_id: link.id,
        }),
      });
      const data = (await response.json()) as { error?: string; detail?: string; document_id?: number };
      if (!response.ok) {
        setInfoMessage(data.error || data.detail || "Linked-page ingestion failed.");
        return;
      }
      setInfoMessage(`Linked-page ingest completed (document #${data.document_id ?? "?"}).`);
      await Promise.all([loadDocuments(), loadDocsSets(), loadDiscoveredLinks(selectedWebDocument.id)]);
    } catch {
      setInfoMessage("Linked-page ingestion failed due to network error.");
    } finally {
      setIngestingLinkId(null);
    }
  }

  async function onIngestLinkedBatch() {
    if (!selectedWebDocument) {
      return;
    }
    setInfoMessage(null);
    setIngestingLinkedBatch(true);
    try {
      const response = await fetch("/api/admin/webpages/linked", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_document_id: selectedWebDocument.id,
          max_pages: linkedBatchMaxPages,
        }),
      });
      const data = (await response.json()) as {
        error?: string;
        detail?: string;
        attempted?: number;
        ingested?: number;
        skipped?: number;
        failed?: number;
      };
      if (!response.ok) {
        setInfoMessage(data.error || data.detail || "Batch linked-page ingestion failed.");
        return;
      }

      setInfoMessage(
        `Batch ingest done: attempted=${data.attempted ?? 0}, ingested=${data.ingested ?? 0}, skipped=${data.skipped ?? 0}, failed=${data.failed ?? 0}.`,
      );
      await Promise.all([loadDocuments(), loadDocsSets(), loadDiscoveredLinks(selectedWebDocument.id)]);
    } catch {
      setInfoMessage("Batch linked-page ingestion failed due to network error.");
    } finally {
      setIngestingLinkedBatch(false);
    }
  }

  async function confirmDelete() {
    if (!deleteCandidate) {
      return;
    }

    setDeleting(true);
    setInfoMessage(null);
    try {
      const response = await fetch(`/api/admin/documents/${deleteCandidate.id}`, { method: "DELETE" });
      const data = (await response.json()) as { error?: string; detail?: string };
      if (!response.ok) {
        setInfoMessage(data.error || data.detail || "Delete failed.");
        return;
      }

      setDeleteCandidate(null);
      await Promise.all([loadDocuments(), loadDocsSets()]);
      if (selectedWebDocumentId === deleteCandidate.id) {
        setSelectedWebDocumentId(null);
      }
    } catch {
      setInfoMessage("Delete failed due to network error.");
    } finally {
      setDeleting(false);
    }
  }

  const selectedLinksStats = useMemo(() => {
    const discovered = discoveredLinks.filter((link) => link.status === "discovered").length;
    const ingested = discoveredLinks.filter((link) => link.status === "ingested").length;
    const failed = discoveredLinks.filter((link) => link.status === "failed").length;
    return { discovered, ingested, failed };
  }, [discoveredLinks]);

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
        <form className="admin-upload-form" onSubmit={onUploadPdf}>
          <input
            type="file"
            accept="application/pdf"
            onChange={(event) => setUploadFile(event.target.files?.[0] || null)}
          />
          <button type="submit" disabled={uploading}>
            {uploading ? "Uploading..." : "Ingest PDF"}
          </button>
        </form>
      </section>

      <section className="admin-card">
        <h3>Webpage Ingestion</h3>
        <form className="admin-upload-form" onSubmit={onIngestWebpage}>
          <input
            type="url"
            className="admin-url-input"
            placeholder="https://example.com/docs/start"
            value={webUrl}
            onChange={(event) => setWebUrl(event.target.value)}
          />
          <select
            className="admin-select"
            value={selectedDocsSetId}
            onChange={(event) => setSelectedDocsSetId(event.target.value)}
          >
            <option value="">Create new docs set</option>
            {docsSets.map((setItem) => (
              <option key={setItem.id} value={String(setItem.id)}>
                #{setItem.id} {setItem.name}
              </option>
            ))}
          </select>
          <button type="submit" disabled={ingestingWeb}>
            {ingestingWeb ? "Ingesting..." : "Ingest Webpage"}
          </button>
        </form>
        {!selectedDocsSetId ? (
          <input
            type="text"
            className="admin-url-input"
            placeholder="New docs set name (optional)"
            value={newDocsSetName}
            onChange={(event) => setNewDocsSetName(event.target.value)}
          />
        ) : null}
        <p className="meta">
          Ingests exactly the URL you submit, extracts text/tables/images, and stores discovered links for controlled
          follow-up ingestion.
        </p>
      </section>

      <section className="admin-card">
        <h3>Documentation Sets</h3>
        {docsSets.length === 0 ? (
          <p className="meta">No documentation sets yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Name</th>
                  <th>Root URL</th>
                  <th>Documents</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {docsSets.map((setItem) => (
                  <tr key={setItem.id}>
                    <td>{setItem.id}</td>
                    <td>{setItem.name}</td>
                    <td>{setItem.root_url || "-"}</td>
                    <td>{setItem.document_count}</td>
                    <td>{formatDate(setItem.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {infoMessage ? <p className="meta">{infoMessage}</p> : null}
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
                  <th>Type</th>
                  <th>Name</th>
                  <th>Docs Set</th>
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
                    <td>{document.source_type}</td>
                    <td>{document.source_name}</td>
                    <td>{document.docs_set_name || "-"}</td>
                    <td>{document.status}</td>
                    <td>{document.text_chunk_count}</td>
                    <td>{document.image_count}</td>
                    <td>{formatDate(document.created_at)}</td>
                    <td className="admin-actions">
                      {document.source_type === "web" ? (
                        <button
                          type="button"
                          className="button secondary"
                          onClick={() => setSelectedWebDocumentId(document.id)}
                          disabled={deleting}
                        >
                          Links
                        </button>
                      ) : null}
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

      {selectedWebDocument ? (
        <section className="admin-card">
          <h3>
            Discovered Links for Web Document #{selectedWebDocument.id} ({selectedWebDocument.source_name})
          </h3>
          <p className="meta">
            discovered={selectedLinksStats.discovered} | ingested={selectedLinksStats.ingested} | failed=
            {selectedLinksStats.failed}
          </p>
          <div className="admin-upload-form">
            <label htmlFor="linked-max-pages">Batch max pages</label>
            <input
              id="linked-max-pages"
              type="number"
              className="admin-number-input"
              min={1}
              max={100}
              value={linkedBatchMaxPages}
              onChange={(event) => {
                const next = Number(event.target.value);
                if (!Number.isFinite(next)) {
                  return;
                }
                setLinkedBatchMaxPages(Math.min(Math.max(Math.floor(next), 1), 100));
              }}
            />
            <button type="button" onClick={() => void onIngestLinkedBatch()} disabled={ingestingLinkedBatch || linksLoading}>
              {ingestingLinkedBatch ? "Ingesting..." : "Ingest Discovered Links (Same Domain)"}
            </button>
          </div>

          {linksLoading ? (
            <p className="meta">Loading discovered links...</p>
          ) : discoveredLinks.length === 0 ? (
            <p className="meta">No links discovered for this page yet.</p>
          ) : (
            <div className="table-wrap">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Status</th>
                    <th>Same Domain</th>
                    <th>URL</th>
                    <th>Ingested Doc</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {discoveredLinks.map((link) => (
                    <tr key={link.id}>
                      <td>{link.id}</td>
                      <td>{link.status}</td>
                      <td>{link.same_domain ? "yes" : "no"}</td>
                      <td>{link.normalized_url}</td>
                      <td>{link.ingested_document_id || "-"}</td>
                      <td>
                        <button
                          type="button"
                          className="button secondary"
                          onClick={() => void onIngestSingleLink(link)}
                          disabled={
                            ingestingLinkId !== null ||
                            link.status === "ingested" ||
                            !link.same_domain
                          }
                        >
                          {ingestingLinkId === link.id ? "Ingesting..." : "Ingest"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ) : null}

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
              This will immediately remove <strong>{deleteCandidate.source_name}</strong>, including chunks, extracted
              images, discovered links, and derived artifacts.
            </p>
            <div className="modal-actions">
              <button
                type="button"
                className="button secondary"
                onClick={() => setDeleteCandidate(null)}
                disabled={deleting}
              >
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
