"use client";

import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";

type AskResponse = {
  answer: string;
  confidence_percent: number;
  grounded: boolean;
  fallback_mode: "none" | "broadened_retrieval" | "model_knowledge" | "out_of_scope";
  webpage_links: string[];
  image_urls?: string[];
  generated_image_urls?: string[];
};

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  pending?: boolean;
  error?: boolean;
  response?: AskResponse;
};

type ChatConversation = {
  id: string;
  title: string;
  createdAt: number;
  messages: ChatMessage[];
};

type ChatSessionState = {
  conversations: ChatConversation[];
  activeConversationId: string;
};

const SESSION_STORAGE_KEY = "contextforge_chat_session_v1";

function createId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function createConversation(): ChatConversation {
  return {
    id: createId(),
    title: "New chat",
    createdAt: Date.now(),
    messages: [],
  };
}

function createInitialSession(): ChatSessionState {
  const initialConversation = createConversation();
  return {
    conversations: [initialConversation],
    activeConversationId: initialConversation.id,
  };
}

function formatTimestamp(value: number): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString();
}

export function AskPanel() {
  const [chatState, setChatState] = useState<ChatSessionState>(createInitialSession);
  const [hydrated, setHydrated] = useState(false);
  const [question, setQuestion] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submittingConversationId, setSubmittingConversationId] = useState<string | null>(null);
  const [renamingConversationId, setRenamingConversationId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(SESSION_STORAGE_KEY);
      if (!raw) {
        setHydrated(true);
        return;
      }

      const parsed = JSON.parse(raw) as ChatSessionState;
      if (!parsed || !Array.isArray(parsed.conversations) || parsed.conversations.length === 0) {
        setHydrated(true);
        return;
      }

      const hasActiveConversation = parsed.conversations.some(
        (conversation) => conversation.id === parsed.activeConversationId,
      );

      setChatState({
        conversations: parsed.conversations,
        activeConversationId: hasActiveConversation
          ? parsed.activeConversationId
          : parsed.conversations[0].id,
      });
    } catch {
      // Ignore invalid session cache.
    } finally {
      setHydrated(true);
    }
  }, []);

  useEffect(() => {
    if (!hydrated) {
      return;
    }
    try {
      sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(chatState));
    } catch {
      // Ignore storage failures.
    }
  }, [chatState, hydrated]);

  const activeConversation = useMemo(() => {
    const found = chatState.conversations.find((conversation) => conversation.id === chatState.activeConversationId);
    return found || chatState.conversations[0] || null;
  }, [chatState]);

  const loading = submittingConversationId !== null;

  function updateConversation(
    conversationId: string,
    updater: (conversation: ChatConversation) => ChatConversation,
  ) {
    setChatState((current) => ({
      ...current,
      conversations: current.conversations.map((conversation) =>
        conversation.id === conversationId ? updater(conversation) : conversation,
      ),
    }));
  }

  function onCreateNewChat() {
    const conversation = createConversation();
    setChatState((current) => ({
      conversations: [conversation, ...current.conversations],
      activeConversationId: conversation.id,
    }));
    setQuestion("");
    setError(null);
    setRenamingConversationId(null);
    setRenameDraft("");
  }

  function onSelectConversation(conversationId: string) {
    setChatState((current) => ({ ...current, activeConversationId: conversationId }));
    setError(null);
    if (renamingConversationId && renamingConversationId !== conversationId) {
      setRenamingConversationId(null);
      setRenameDraft("");
    }
  }

  function beginRename(conversation: ChatConversation) {
    setRenamingConversationId(conversation.id);
    setRenameDraft(conversation.title === "New chat" ? "" : conversation.title);
  }

  function commitRename() {
    if (!renamingConversationId) {
      return;
    }
    const finalTitle = renameDraft.trim() || "New chat";
    updateConversation(renamingConversationId, (conversation) => ({
      ...conversation,
      title: finalTitle,
    }));
    setRenamingConversationId(null);
    setRenameDraft("");
  }

  function cancelRename() {
    setRenamingConversationId(null);
    setRenameDraft("");
  }

  function onDeleteConversation(conversationId: string) {
    setChatState((current) => {
      const remaining = current.conversations.filter((conversation) => conversation.id !== conversationId);
      if (remaining.length === 0) {
        const nextConversation = createConversation();
        return {
          conversations: [nextConversation],
          activeConversationId: nextConversation.id,
        };
      }

      const activeConversationId =
        current.activeConversationId === conversationId ? remaining[0].id : current.activeConversationId;

      return {
        conversations: remaining,
        activeConversationId,
      };
    });

    if (submittingConversationId === conversationId) {
      setSubmittingConversationId(null);
    }
    if (renamingConversationId === conversationId) {
      cancelRename();
    }
    if (activeConversation?.id === conversationId) {
      setQuestion("");
    }
  }

  async function submitQuestion() {
    const active = activeConversation;
    if (!active) {
      return;
    }

    const trimmedQuestion = question.trim();
    if (!trimmedQuestion) {
      setError("Please enter a question.");
      return;
    }

    const userMessage: ChatMessage = {
      id: createId(),
      role: "user",
      content: trimmedQuestion,
      createdAt: Date.now(),
    };

    const assistantMessage: ChatMessage = {
      id: createId(),
      role: "assistant",
      content: "Thinking...",
      createdAt: Date.now(),
      pending: true,
    };

    setError(null);
    setQuestion("");
    setSubmittingConversationId(active.id);

    updateConversation(active.id, (conversation) => {
      const hasUserMessages = conversation.messages.some((message) => message.role === "user");
      const nextTitle = hasUserMessages ? conversation.title : trimmedQuestion.slice(0, 64);
      return {
        ...conversation,
        title: nextTitle || "New chat",
        messages: [...conversation.messages, userMessage, assistantMessage],
      };
    });

    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          question: trimmedQuestion,
          conversation_id: active.id,
        }),
      });

      const data = (await response.json()) as AskResponse & { error?: string; detail?: string };
      if (!response.ok) {
        const message = data.error || data.detail || "Request failed.";
        updateConversation(active.id, (conversation) => ({
          ...conversation,
          messages: conversation.messages.map((messageItem) =>
            messageItem.id === assistantMessage.id
              ? {
                  ...messageItem,
                  content: message,
                  pending: false,
                  error: true,
                }
              : messageItem,
          ),
        }));
        return;
      }

      updateConversation(active.id, (conversation) => ({
        ...conversation,
        messages: conversation.messages.map((messageItem) =>
          messageItem.id === assistantMessage.id
            ? {
                ...messageItem,
                content: data.answer,
                pending: false,
                error: false,
                response: data,
              }
            : messageItem,
        ),
      }));
    } catch {
      updateConversation(active.id, (conversation) => ({
        ...conversation,
        messages: conversation.messages.map((messageItem) =>
          messageItem.id === assistantMessage.id
            ? {
                ...messageItem,
                content: "Unexpected network error.",
                pending: false,
                error: true,
              }
            : messageItem,
        ),
      }));
    } finally {
      setSubmittingConversationId((current) => (current === active.id ? null : current));
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitQuestion();
  }

  function onComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      void submitQuestion();
    }
  }

  return (
    <div className="chat-shell">
      <div className="chat-layout">
        <aside className="chat-history-pane">
          <button type="button" className="button secondary" onClick={onCreateNewChat}>
            New chat
          </button>
          <div className="chat-history-list">
            {chatState.conversations.map((conversation) => {
              const isActive = conversation.id === activeConversation?.id;
              const isRenaming = renamingConversationId === conversation.id;
              return (
                <article key={conversation.id} className={`chat-history-item ${isActive ? "active" : ""}`}>
                  <div className="chat-history-item-head">
                    {isRenaming ? (
                      <input
                        type="text"
                        className="chat-history-title-input"
                        value={renameDraft}
                        onChange={(event) => setRenameDraft(event.target.value)}
                        onBlur={commitRename}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            commitRename();
                          }
                          if (event.key === "Escape") {
                            event.preventDefault();
                            cancelRename();
                          }
                        }}
                        autoFocus
                      />
                    ) : (
                      <button
                        type="button"
                        className="chat-history-title-button"
                        onClick={() => {
                          if (isActive) {
                            beginRename(conversation);
                          } else {
                            onSelectConversation(conversation.id);
                          }
                        }}
                        title={isActive ? "Click to rename" : "Open chat"}
                      >
                        {conversation.title}
                      </button>
                    )}

                    <button
                      type="button"
                      className="chat-history-delete"
                      aria-label={`Delete chat ${conversation.title}`}
                      onClick={() => onDeleteConversation(conversation.id)}
                      disabled={loading}
                      title="Delete chat"
                    >
                      x
                    </button>
                  </div>

                  <button
                    type="button"
                    className="chat-history-select"
                    onClick={() => onSelectConversation(conversation.id)}
                  >
                    <span className="chat-history-meta">{formatTimestamp(conversation.createdAt)}</span>
                  </button>
                </article>
              );
            })}
          </div>
        </aside>

        <section className="chat-main">
          <div className="chat-transcript">
            {activeConversation && activeConversation.messages.length > 0 ? (
              activeConversation.messages.map((message) => (
                <div key={message.id} className={`chat-bubble-row ${message.role}`}>
                  <article className={`chat-bubble ${message.role} ${message.error ? "error-state" : ""}`}>
                    <p className={`chat-message-text ${message.pending ? "thinking" : ""}`}>{message.content}</p>

                    {message.role === "assistant" && message.response ? (
                      <div className="chat-bubble-meta">
                        <p className="meta">
                          Confidence: {message.response.confidence_percent}% | Grounded: {message.response.grounded ? "yes" : "no"}
                        </p>
                        <p className="meta">Mode: {message.response.fallback_mode}</p>

                        {message.response.webpage_links.length > 0 ? (
                          <div className="chat-links">
                            <h3>Helpful links</h3>
                            <ul>
                              {message.response.webpage_links.map((url) => (
                                <li key={url}>
                                  <a href={url} target="_blank" rel="noreferrer">
                                    {url}
                                  </a>
                                </li>
                              ))}
                            </ul>
                          </div>
                        ) : null}

                        {message.response.image_urls && message.response.image_urls.length > 0 ? (
                          <div className="chat-links">
                            <h3>Image evidence</h3>
                            <ul>
                              {message.response.image_urls.map((url) => (
                                <li key={url}>
                                  <a href={url} target="_blank" rel="noreferrer">
                                    Open image
                                  </a>
                                </li>
                              ))}
                            </ul>
                          </div>
                        ) : null}

                        {message.response.generated_image_urls && message.response.generated_image_urls.length > 0 ? (
                          <div className="generated-images">
                            <h3>Generated Visual</h3>
                            {message.response.generated_image_urls.map((url) => (
                              <a key={url} href={url} target="_blank" rel="noreferrer" className="generated-image-link">
                                <img src={url} alt="Generated response visual" className="generated-image" loading="lazy" />
                              </a>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                  </article>
                </div>
              ))
            ) : (
              <p className="chat-empty">Start a conversation by asking a question.</p>
            )}
          </div>

          <form onSubmit={onSubmit} className="chat-composer">
            <label htmlFor="question">Ask a question</label>
            <div className="chat-input-wrap">
              <textarea
                id="question"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={onComposerKeyDown}
                placeholder="Example: Explain how our orchestration setup works and what tradeoffs we should consider."
                rows={2}
                disabled={loading}
              />
              <button
                type="submit"
                className="chat-send-inside"
                disabled={loading || !question.trim()}
              >
                {loading ? "..." : "Send"}
              </button>
            </div>
            <p className="meta">Enter = newline, Ctrl/Cmd + Enter = send</p>
          </form>
        </section>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {!hydrated ? <p className="meta">Loading chat session...</p> : null}
    </div>
  );
}
