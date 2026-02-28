"use client";

import { FormEvent, useState } from "react";

type AskResponse = {
  answer: string;
  confidence_percent: number;
  grounded: boolean;
  fallback_mode: "none" | "broadened_retrieval" | "model_knowledge" | "out_of_scope";
  webpage_links: string[];
  image_urls?: string[];
  generated_image_urls?: string[];
};

export function AskPanel() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AskResponse | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setResult(null);

    if (!question.trim()) {
      setError("Please enter a question.");
      return;
    }

    setLoading(true);
    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ question }),
      });

      const data = await response.json();
      if (!response.ok) {
        setError(data.error || data.detail || "Request failed.");
        return;
      }

      setResult(data as AskResponse);
    } catch {
      setError("Unexpected network error.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="chat-shell">
      <form onSubmit={onSubmit} className="ask-form">
        <label htmlFor="question">Ask a question</label>
        <textarea
          id="question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Example: Explain how our orchestration setup works and what tradeoffs we should consider."
          rows={5}
        />
        <button type="submit" disabled={loading}>
          {loading ? "Thinking..." : "Send"}
        </button>
      </form>

      {error ? <p className="error">{error}</p> : null}

      {result ? (
        <section className="answer-card">
          <h2>Answer</h2>
          <div className="answer-text">{result.answer}</div>
          <p className="meta">
            Confidence: {result.confidence_percent}% | Grounded in indexed sources: {result.grounded ? "yes" : "no"}
          </p>
          <p className="meta">Mode: {result.fallback_mode}</p>
          {result.webpage_links.length > 0 ? (
            <div className="links">
              <h3>Helpful links</h3>
              <ul>
                {result.webpage_links.map((url) => (
                  <li key={url}>
                    <a href={url} target="_blank" rel="noreferrer">
                      {url}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {result.image_urls && result.image_urls.length > 0 ? (
            <div className="links">
              <h3>Image evidence</h3>
              <ul>
                {result.image_urls.map((url) => (
                  <li key={url}>
                    <a href={url} target="_blank" rel="noreferrer">
                      Open image
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {result.generated_image_urls && result.generated_image_urls.length > 0 ? (
            <div className="generated-images">
              <h3>Generated Visual</h3>
              {result.generated_image_urls.map((url) => (
                <a key={url} href={url} target="_blank" rel="noreferrer" className="generated-image-link">
                  <img src={url} alt="Generated response visual" className="generated-image" loading="lazy" />
                </a>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
