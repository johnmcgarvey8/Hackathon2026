"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { agentLabel, budgetLabel, runtimeLabel } from "@/lib/chat-runtime";
import type { ChatCitation, ChatStatus, Conversation, SourceClass } from "@/lib/types";
import { Icon } from "@/components/icons";
import { useProject } from "@/components/project-context";
import { LoadingState, UnavailableState } from "@/components/status-state";

const suggestions = [
  "Find the highest-impact GEO opportunities",
  "Review visibility for a priority page",
  "Explain the latest measurement evidence",
  "Identify content gaps using approved context",
];

const sourceLabels: Record<SourceClass, string> = {
  "geo-evidence": "GEO evidence",
  "org-knowledge": "Organisational knowledge",
  "work-context": "Work context",
  "model-knowledge": "Model knowledge",
};

function groupSources(citations: ChatCitation[]) {
  return citations.reduce<Partial<Record<SourceClass, ChatCitation[]>>>((groups, citation) => {
    (groups[citation.source_class] ||= []).push(citation);
    return groups;
  }, {});
}

function safeCitationUrl(value: string | null) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.toString() : null;
  } catch {
    return null;
  }
}

export default function ChatPage() {
  const { project } = useProject();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [runtime, setRuntime] = useState<ChatStatus | null>(null);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [search, setSearch] = useState("");
  const [message, setMessage] = useState("");
  const [useContext, setUseContext] = useState(false);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [changingConversation, setChangingConversation] = useState(false);
  const [recoveryId, setRecoveryId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerSources, setDrawerSources] = useState<ChatCitation[]>([]);
  const streamRef = useRef<HTMLDivElement>(null);
  const busyRef = useRef(false);
  const recoveryKey = useRef<string | null>(null);

  const reloadSavedConversation = async (conversationId: string) => {
    if (!project) return;
    // GET-only reconciliation also refreshes the owner budget before another turn.
    const [saved, items, status] = await Promise.all([
      api.conversation(project.project_id, conversationId),
      api.conversations(project.project_id),
      api.chatStatus(project.project_id),
    ]);
    setSelected(saved);
    setConversations(items);
    setRuntime(status);
    const pending = saved.turns.some((turn) => turn.status === "running");
    setRecoveryId(pending ? conversationId : null);
    return saved;
  };

  useEffect(() => {
    if (!project) return;
    let active = true;
    setLoading(true);
    setError(null);
    setRuntime(null);
    Promise.all([api.conversations(project.project_id), api.chatStatus(project.project_id)])
      .then(([items, status]) => {
        if (!active) return;
        setConversations(items);
        setSelected(items[0] || null);
        setRuntime(status);
      })
      .catch((requestError) => {
        if (active) setError(requestError instanceof ApiError ? requestError.message : "Chat history is unavailable.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [project]);

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight, behavior: "smooth" });
  }, [selected, sending]);

  const filtered = useMemo(
    () => conversations.filter((item) => item.title.toLowerCase().includes(search.toLowerCase())),
    [conversations, search],
  );

  const allSources = useMemo(
    () => selected?.turns.flatMap((turn) => turn.citations) || [],
    [selected],
  );

  if (!project) return null;
  const interactionLocked = sending || changingConversation || loading || recoveryId !== null;
  const pendingTurn = selected?.turns.some((turn) => turn.status === "running") ?? false;

  const openConversation = async (conversationId: string) => {
    if (busyRef.current || interactionLocked) return;
    busyRef.current = true;
    setChangingConversation(true);
    setHistoryOpen(false);
    setError(null);
    try {
      setSelected(await api.conversation(project.project_id, conversationId));
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "The conversation could not be opened.");
    } finally {
      busyRef.current = false;
      setChangingConversation(false);
    }
  };

  const newConversation = async () => {
    if (busyRef.current || interactionLocked) return;
    busyRef.current = true;
    setChangingConversation(true);
    setError(null);
    try {
      const conversation = await api.createConversation(project.project_id);
      setSelected(conversation);
      setConversations((items) => [conversation, ...items]);
      setHistoryOpen(false);
      setMessage("");
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "A new conversation could not be created.");
    } finally {
      busyRef.current = false;
      setChangingConversation(false);
    }
  };

  const ensureConversation = async () => {
    if (selected) return selected;
    const conversation = await api.createConversation(project.project_id);
    setConversations((items) => [conversation, ...items]);
    setSelected(conversation);
    return conversation;
  };

  const submit = async (event?: FormEvent, suggestedMessage?: string) => {
    event?.preventDefault();
    const text = (suggestedMessage || message).trim();
    if (!text || busyRef.current || interactionLocked || pendingTurn || !runtime?.can_send) return;
    busyRef.current = true;
    setSending(true);
    setMessage(text);
    setError(null);
    let conversationId: string | null = null;
    try {
      const conversation = await ensureConversation();
      conversationId = conversation.conversation_id;
      setRecoveryId(conversationId);
      recoveryKey.current = crypto.randomUUID();
      const updated = await api.sendMessage(project.project_id, conversation.conversation_id, {
        message: text,
        expected_revision: conversation.revision,
        idempotency_key: recoveryKey.current,
        use_organisational_context: useContext,
      });
      setSelected(updated);
      const turn = updated.turns.at(-1);
      if (turn?.status === "completed") setMessage("");
      else setError(turn?.error || "The turn is still pending. Reload its saved status before sending again.");
      await reloadSavedConversation(updated.conversation_id);
    } catch (requestError) {
      const detail = requestError instanceof Error ? requestError.message : "The message could not be sent.";
      setError(detail);
      if (conversationId) {
        try {
          const saved = await reloadSavedConversation(conversationId);
          const turn = saved?.turns.at(-1);
          if (turn?.idempotency_key === recoveryKey.current && turn?.status === "completed") {
            setMessage("");
            setError(`${detail} The saved reply was recovered. Review it before sending another message.`);
          } else if (turn?.status === "running") {
            setError(`${detail} The saved turn is still pending. Reload its status; no message will be retried automatically.`);
          } else {
            setError(`${detail} Saved history has been reloaded. Review it before sending again.${turn?.error ? ` ${turn.error}` : ""}`);
          }
        } catch {
          setRuntime(null);
          setError(`${detail} Saved history could not be reloaded. Sending is paused until recovery succeeds.`);
        }
      }
    } finally {
      busyRef.current = false;
      setSending(false);
    }
  };

  const recover = async () => {
    const conversationId = recoveryId || selected?.conversation_id;
    if (!conversationId || busyRef.current) return;
    busyRef.current = true;
    setSending(true);
    setRecoveryId(conversationId);
    try {
      const saved = await reloadSavedConversation(conversationId);
      const turn = saved?.turns.at(-1);
      if (turn?.status === "completed" && turn.idempotency_key === recoveryKey.current) setMessage("");
      setError(turn?.status === "running"
        ? "The saved turn is still pending. Reload again later; no message has been retried."
        : turn?.error || "Saved history and runtime refreshed. Review the conversation before sending again.");
    } catch {
      setRuntime(null);
      setError("Saved history could not be reloaded. Sending remains paused. Try reloading again.");
    } finally {
      busyRef.current = false;
      setSending(false);
    }
  };

  const openSources = (sources: ChatCitation[]) => {
    setDrawerSources(sources);
    setDrawerOpen(true);
  };

  return (
    <section className={`chat-screen ${historyOpen ? "history-open" : ""}`}>
      <aside className="conversation-history" aria-label="Project chat history">
        <div className="history-heading">
          <h2>Conversations</h2><span>{conversations.length}</span>
          <button className="icon-button history-close" type="button" aria-label="Close conversations" onClick={() => setHistoryOpen(false)}><Icon name="close" /></button>
        </div>
        <button className="new-chat-button" type="button" disabled={interactionLocked} onClick={() => void newConversation()}><Icon name="chat" /> New chat <Icon name="plus" /></button>
        <label className="history-search"><Icon name="search" /><span className="sr-only">Search chats</span><input type="search" placeholder="Search chats" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
        <nav aria-label="Saved conversations">
          <div className="history-group">
            <h3>Project history</h3>
            {filtered.map((conversation) => (
              <button
                className={`chat-history-item ${selected?.conversation_id === conversation.conversation_id ? "selected" : ""}`}
                type="button"
                key={conversation.conversation_id}
                disabled={interactionLocked}
                onClick={() => void openConversation(conversation.conversation_id)}
                aria-current={selected?.conversation_id === conversation.conversation_id ? "true" : undefined}
              >
                <Icon name="chat" />
                <span><strong>{conversation.title}</strong><small>{new Date(conversation.updated_at).toLocaleDateString("en-GB")}</small></span>
              </button>
            ))}
          </div>
        </nav>
        {filtered.length === 0 && <p className="small muted">No conversations found.</p>}
        <p className="history-footnote"><Icon name="lock" /> Project-scoped history<br /><span>The browser never receives the bearer token.</span></p>
      </aside>

      <section className="conversation-canvas">
        <header className="conversation-toolbar">
          <button className="history-mobile-toggle icon-button" type="button" aria-label="Show conversations" aria-expanded={historyOpen} onClick={() => setHistoryOpen(true)}><Icon name="menu" /></button>
          <div className="conversation-heading"><span className="agent-symbol"><Icon name="spark" /></span><span><strong>GEO assistant</strong><small>{project.name}</small></span></div>
          <div className="conversation-toolbar-actions">
            <button className="button ghost" type="button" onClick={() => openSources(allSources)}><Icon name="sources" /><span>Sources</span></button>
          </div>
        </header>
        <div className="agent-surfaces" aria-label="Grounding sources">
          <span className="agent-surfaces-label">Grounding</span>
          <span className="surface-badge">{runtimeLabel(runtime)}</span>
          {agentLabel(runtime) && <span className="surface-badge">{agentLabel(runtime)}</span>}
          <span className="surface-badge">Organisational retrieval unavailable</span>
          <span className="surfaces-status">Project scoped</span>
        </div>

        <div className={`conversation-stream ${!selected?.turns.length ? "welcome-stream" : ""}`} ref={streamRef}>
          {loading ? <LoadingState label="Loading conversations" /> : !selected?.turns.length ? (
            <div className="chat-welcome">
              <span className="welcome-project">{project.name} workspace</span>
              <h1>What would you like<br />to improve today?</h1>
              <p>Turn GEO goals into evidence-backed recommendations and grounded answers.</p>
              <div className="welcome-suggestions">
                {suggestions.map((suggestion, index) => (
                  <button className="suggestion-card" type="button" key={suggestion} disabled={!runtime?.can_send || interactionLocked || pendingTurn} onClick={() => void submit(undefined, suggestion)}>
                    <span className="suggestion-icon"><Icon name={index === 0 ? "trend" : index === 1 ? "search" : index === 2 ? "sources" : "spark"} /></span>
                    <strong>{suggestion}</strong><Icon name="chevron" />
                  </button>
                ))}
              </div>
              <div className="welcome-grounding"><Icon name="lock" /> Server-side authentication. Grounding availability is shown above.</div>
            </div>
          ) : (
            <div className="thread">
              {selected.turns.map((turn) => (
                <div className="turn-pair" key={turn.sequence}>
                  <div className="thread-message from-user"><div className="thread-bubble"><p>{turn.message}</p></div></div>
                  <div className="thread-message from-agent">
                    <span className="thread-avatar"><Icon name="spark" /></span>
                    <div className="thread-bubble">
                      <div className="thread-speaker">GEO assistant</div>
                      {turn.status === "running" ? <p>Working on your request...</p> : turn.error ? <p className="error-text">{turn.error}</p> : <p>{turn.answer}</p>}
                      {turn.citations.length > 0 && (
                        <button className="source-summary-button" type="button" onClick={() => openSources(turn.citations)}>
                          <Icon name="sources" /> {turn.citations.length} grounded {turn.citations.length === 1 ? "source" : "sources"}
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
              {sending && <div className="thread-message from-agent"><span className="thread-avatar"><Icon name="spark" /></span><div className="thread-bubble"><div className="typing-dots"><span /><span /><span /></div></div></div>}
            </div>
          )}
        </div>

        <div className="composer-dock">
          {runtime && <div className="small muted" role="status"><strong>{runtimeLabel(runtime)}</strong><p>{runtime.detail}</p>{runtime.mode === "foundry" && <p>Configuration is not remote verification. No tools or knowledge retrieval are connected.</p>}{budgetLabel(runtime) && <p>{budgetLabel(runtime)}</p>}</div>}
          {error && <UnavailableState title="Request unavailable" message={error} compact />}
          {(recoveryId || pendingTurn) && <button className="button ghost" type="button" disabled={sending || changingConversation} onClick={() => void recover()}>Reload saved conversation</button>}
          <form className="prompt-composer" onSubmit={(event) => void submit(event)}>
            <label className="sr-only" htmlFor="chat-input">Message the GEO assistant</label>
            <textarea id="chat-input" maxLength={4000} placeholder="Ask about evidence, opportunities, or project context..." value={message} disabled={interactionLocked || pendingTurn || !runtime?.can_send} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }} />
            <div className="prompt-actions">
              <label className="context-toggle"><input type="checkbox" checked={useContext} disabled={!runtime?.organisational_context_available} onChange={(event) => setUseContext(event.target.checked)} /> Use organisational context</label>
              <span className="prompt-key-hint">Enter to send</span>
              <button className="send-prompt" type="submit" aria-label="Send message" disabled={!message.trim() || interactionLocked || pendingTurn || !runtime?.can_send}><Icon name="send" /></button>
            </div>
          </form>
          <p className="conversation-disclaimer">Review AI-generated answers and their sources before use.</p>
        </div>
      </section>

      {drawerOpen && <button className="drawer-backdrop" aria-label="Close sources" onClick={() => setDrawerOpen(false)} />}
      <aside className={`drawer ${drawerOpen ? "open" : ""}`} aria-modal="true" role="dialog" aria-labelledby="source-drawer-title">
        <div className="drawer-header"><div><p className="eyebrow">Grounding</p><h2 id="source-drawer-title">Sources</h2></div><button className="icon-button" type="button" aria-label="Close sources" onClick={() => setDrawerOpen(false)}><Icon name="close" /></button></div>
        <div className="drawer-content">
          {drawerSources.length === 0 ? <UnavailableState title="No sources yet" message="Sources will appear when the assistant returns grounded citations." compact /> : (
            Object.entries(groupSources(drawerSources)).map(([sourceClass, sources]) => (
              <section className="source-group" key={sourceClass}>
                <h3>{sourceLabels[sourceClass as SourceClass]}</h3>
                {sources?.map((source) => {
                  const url = safeCitationUrl(source.url);
                  return <article className="source-card" key={`${source.source_class}-${source.source_id}`}><span className="source-type">{sourceLabels[source.source_class]}</span><strong>{source.title}</strong><small>{source.source_id}</small>{url && <a href={url} target="_blank" rel="noreferrer">Open source</a>}</article>;
                })}
              </section>
            ))
          )}
        </div>
      </aside>
    </section>
  );
}
