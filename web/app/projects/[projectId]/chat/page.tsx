"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { shouldPollMeasurementWorkflows } from "@/lib/chat-runtime";
import {
  deduplicateCitations,
  evidencePresentation,
  formatGroundingQueryTitle,
  groupGroundingEvidence,
  inferEvidenceType,
  measurementRunEvidence,
} from "@/lib/evidence-presentation";
import type { ChatCitation, ChatStatus, Conversation, GeoEvidenceType, SourceClass } from "@/lib/types";
import { Icon } from "@/components/icons";
import { AssistantMarkdown } from "@/components/assistant-markdown";
import { useProject } from "@/components/project-context";
import { LoadingState, UnavailableState } from "@/components/status-state";

const runSuggestions = [
  "Find the highest-priority evidence-backed opportunities",
  "Review visibility for the measured page",
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
  return deduplicateCitations(citations).reduce<Partial<Record<SourceClass, ChatCitation[]>>>((groups, citation) => {
    (groups[citation.source_class] ||= []).push(citation);
    return groups;
  }, {});
}

function sourcesOfType(sources: ChatCitation[], type: GeoEvidenceType) {
  return sources.filter((source) =>
    inferEvidenceType(source.geo_evidence_type, source.source_id, source.title) === type);
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

function SourceCard({
  source,
  grouped = false,
  hideLink = false,
}: {
  source: ChatCitation;
  grouped?: boolean;
  hideLink?: boolean;
}) {
  const sourceUrl = safeCitationUrl(source.url);
  const url = hideLink ? null : sourceUrl;
  const evidenceType = source.source_class === "geo-evidence"
    ? inferEvidenceType(source.geo_evidence_type, source.source_id, source.title)
    : null;
  const presentation = evidenceType ? evidencePresentation(evidenceType) : null;
  const displayTitle = evidenceType === "measurement-run"
    ? "Measurement run"
    : evidenceType === "grounding-query"
      ? formatGroundingQueryTitle(source.title, source.query_id)
      : source.title;
  return (
    <article className="source-card">
      {(!grouped || source.brand_status === "matched") && (
        <div className="source-card-badges">
          {!grouped && (presentation
            ? <span className={`pill evidence-kind ${presentation.pillClass}`}>{presentation.label}</span>
            : <span className="source-type">{sourceLabels[source.source_class]}</span>)}
          {source.brand_status === "matched" && source.brand_name && (
            <span className="pill green">✓ {source.brand_name} found</span>
          )}
        </div>
      )}
      <strong>{displayTitle}</strong>
      {!grouped && presentation && <p>{presentation.description}</p>}
      {evidenceType === "grounding-citation" && sourceUrl && (
        <span className="source-url" title={sourceUrl}>{sourceUrl}</span>
      )}
      {url && <a href={url} target="_blank" rel="noreferrer">Open source</a>}
    </article>
  );
}

function GeoEvidenceSources({
  sources,
  runUrl,
}: {
  sources: ChatCitation[];
  runUrl: string | null;
}) {
  const measurementSources = sourcesOfType(sources, "measurement-run");
  const groundingGroups = groupGroundingEvidence(sources);
  const testAnswerSources = sourcesOfType(sources, "test-answer");
  const measurementPresentation = evidencePresentation("measurement-run");
  const testAnswerPresentation = evidencePresentation("test-answer");

  return (
    <>
      <div className="source-group-header">
        <h3>GEO evidence</h3>
      </div>
      <p className="source-group-description">
        Saved evidence from the measurement run bound to this conversation.
      </p>

      {measurementSources.length > 0 && (
        <section className="source-subgroup compact">
          <div className="source-subgroup-header">
            <h4>Measurement run</h4>
            <span className={`pill ${measurementPresentation.pillClass}`}>{measurementSources.length}</span>
          </div>
          {measurementSources.map((source) => runUrl ? (
            <Link
              className="source-card source-card-link"
              href={runUrl}
              key={`${source.source_class}-${source.source_id}`}
            >
              <strong>Measurement run</strong>
              <span>Open in Control Plane</span>
            </Link>
          ) : (
            <SourceCard grouped hideLink source={source} key={`${source.source_class}-${source.source_id}`} />
          ))}
        </section>
      )}

      {groundingGroups.length > 0 && (
        <section className="source-subgroup">
          <div className="source-subgroup-header">
            <h4>Grounding queries</h4>
            <span className="pill amber">{groundingGroups.length}</span>
          </div>
          <div className="grounding-query-groups">
            {groundingGroups.map((group) => {
              const title = group.query
                ? formatGroundingQueryTitle(group.query.title, group.queryId)
                : group.queryId
                  ? formatGroundingQueryTitle("", group.queryId)
                  : "Other grounding citations";
              return (
                <details className="grounding-query-group" key={group.key}>
                  <summary>
                    <strong>{title}</strong>
                    <span>{group.citations.length} {group.citations.length === 1 ? "citation" : "citations"}</span>
                  </summary>
                  <div className="grounding-query-content">
                    {group.query?.brand_status === "matched" && group.query.brand_name && (
                      <span className="pill green">✓ {group.query.brand_name} found</span>
                    )}
                    {group.citations.length > 0 ? group.citations.map((source) => (
                      <SourceCard grouped hideLink source={source} key={`${source.source_class}-${source.source_id}`} />
                    )) : (
                      <p className="small muted">No returned citations are included in this response.</p>
                    )}
                  </div>
                </details>
              );
            })}
          </div>
        </section>
      )}

      {testAnswerSources.length > 0 && (
        <section className="source-subgroup compact">
          <div className="source-subgroup-header">
            <h4>LLM Provider Survey</h4>
            <span className={`pill ${testAnswerPresentation.pillClass}`}>{testAnswerSources.length}</span>
          </div>
          {testAnswerSources.map((source) => (
            <SourceCard grouped hideLink source={source} key={`${source.source_class}-${source.source_id}`} />
          ))}
        </section>
      )}
    </>
  );
}

function suggestedMeasurementUrl(domain?: string) {
  if (!domain) return "https://example.com/";
  try {
    return new URL(domain.includes("://") ? domain : `https://${domain}`).toString();
  } catch {
    return domain;
  }
}

export default function ChatPage() {
  const { project } = useProject();
  const searchParams = useSearchParams();
  const requestedConversationId = searchParams.get("conversation");
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
  const [runSources, setRunSources] = useState<ChatCitation[]>([]);
  const [runSourcesLoading, setRunSourcesLoading] = useState(false);
  const [runSourcesError, setRunSourcesError] = useState<string | null>(null);
  const streamRef = useRef<HTMLDivElement>(null);
  const busyRef = useRef(false);
  const recoveryKey = useRef<string | null>(null);

  const reloadSavedConversation = async (conversationId: string) => {
    if (!project) return;
    // GET-only reconciliation refreshes saved history and runtime before another turn.
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
    Promise.all([
      api.conversations(project.project_id),
      api.chatStatus(project.project_id),
    ])
      .then(([items, status]) => {
        if (!active) return;
        setConversations(items);
        setSelected(items.find((item) => item.conversation_id === requestedConversationId) || items[0] || null);
        setRuntime(status);
      })
      .catch((requestError) => {
        if (active) setError(requestError instanceof ApiError ? requestError.message : "Chat history is unavailable.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [project, requestedConversationId]);

  const workflows = selected?.measurement_workflows?.length
    ? selected.measurement_workflows
    : selected?.measurement_workflow ? [selected.measurement_workflow] : [];
  const workflowRunId = selected?.run_id || selected?.measurement_workflow?.run_id || null;
  const selectedConversationId = selected?.conversation_id || null;
  const workflowPollingRequired = shouldPollMeasurementWorkflows(workflows);

  useEffect(() => {
    if (!project || !workflowRunId) {
      setRunSources([]);
      setRunSourcesError(null);
      setRunSourcesLoading(false);
      return;
    }
    let active = true;
    setRunSources([]);
    setRunSourcesError(null);
    setRunSourcesLoading(true);
    api.run(project.project_id, workflowRunId)
      .then((run) => {
        if (active) setRunSources(measurementRunEvidence(run, project.name));
      })
      .catch(() => {
        if (active) setRunSourcesError("Complete measurement evidence could not be loaded.");
      })
      .finally(() => {
        if (active) setRunSourcesLoading(false);
      });
    return () => { active = false; };
  }, [project, workflowRunId]);

  useEffect(() => {
    if (!project || !selectedConversationId || !workflowPollingRequired) return;
    const conversationId = selectedConversationId;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let attempts = 0;
    const schedule = () => {
      if (!stopped && attempts < 120) timer = setTimeout(tick, 3000);
    };
    const tick = async () => {
      if (stopped) return;
      if (document.visibilityState === "hidden") {
        schedule();
        return;
      }
      attempts += 1;
      const conversationResult = await Promise.allSettled([
        api.conversation(project.project_id, conversationId),
      ]);
      if (!stopped && conversationResult[0].status === "fulfilled") {
        const saved = conversationResult[0].value;
        setSelected(saved);
        setConversations((items) => items.map((item) =>
          item.conversation_id === saved.conversation_id ? saved : item));
      }
      schedule();
    };
    schedule();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [project, selectedConversationId, workflowPollingRequired]);

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
  const visibleDrawerSources = useMemo(
    () => deduplicateCitations(
      drawerSources.length > 0 ? drawerSources : runSources,
    ),
    [runSources, drawerSources],
  );

  if (!project) return null;
  const projectUrl = suggestedMeasurementUrl(project.primary_domain);
  const projectSuggestions = [
    `Help me plan a measurement for ${projectUrl}`,
    `Check whether any of my brand URLs appear for ${project.primary_domain}`,
    "Explain how chat-first measurement works",
    "What URL and objective should I use for a measurement?",
  ];
  const suggestions = workflowRunId ? runSuggestions : projectSuggestions;
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

  const deleteConversation = async (conversation: Conversation) => {
    if (busyRef.current || interactionLocked) return;
    if (!window.confirm(`Delete "${conversation.title}"? Measurement run data will be retained.`)) return;
    busyRef.current = true;
    setChangingConversation(true);
    setError(null);
    try {
      await api.deleteConversation(project.project_id, conversation.conversation_id);
      const remaining = conversations.filter(
        (item) => item.conversation_id !== conversation.conversation_id,
      );
      setConversations(remaining);
      if (selected?.conversation_id === conversation.conversation_id) {
        setSelected(remaining[0] || null);
        setMessage("");
        setRecoveryId(null);
      }
    } catch (requestError) {
      setError(
        requestError instanceof ApiError ? requestError.message : "The conversation could not be deleted.",
      );
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
            <h3>Run-bound conversations</h3>
            {filtered.filter((conversation) => conversation.run_id || conversation.linked_run_ids?.length || conversation.measurement_workflow?.run_id).map((conversation) => (
              <div className="chat-history-row" key={conversation.conversation_id}>
                <button
                  className={`chat-history-item ${selected?.conversation_id === conversation.conversation_id ? "selected" : ""}`}
                  type="button"
                  disabled={interactionLocked}
                  onClick={() => void openConversation(conversation.conversation_id)}
                  aria-current={selected?.conversation_id === conversation.conversation_id ? "true" : undefined}
                >
                  <Icon name="chat" />
                  <span><strong>{conversation.title}</strong><small>{new Date(conversation.updated_at).toLocaleDateString("en-GB")}</small></span>
                </button>
                <button
                  className="icon-button chat-delete"
                  type="button"
                  aria-label={`Delete ${conversation.title}`}
                  disabled={interactionLocked || conversation.turns.some((turn) => turn.status === "running")}
                  onClick={() => void deleteConversation(conversation)}
                >
                  <Icon name="close" />
                </button>
              </div>
            ))}
          </div>
          <div className="history-group">
            <h3>Project conversations</h3>
            {filtered.filter((conversation) => !conversation.run_id && !conversation.linked_run_ids?.length && !conversation.measurement_workflow?.run_id).map((conversation) => (
              <div className="chat-history-row" key={conversation.conversation_id}>
                <button
                  className={`chat-history-item ${selected?.conversation_id === conversation.conversation_id ? "selected" : ""}`}
                  type="button"
                  disabled={interactionLocked}
                  onClick={() => void openConversation(conversation.conversation_id)}
                  aria-current={selected?.conversation_id === conversation.conversation_id ? "true" : undefined}
                >
                  <Icon name="chat" />
                  <span><strong>{conversation.title}</strong><small>{new Date(conversation.updated_at).toLocaleDateString("en-GB")}</small></span>
                </button>
                <button
                  className="icon-button chat-delete"
                  type="button"
                  aria-label={`Delete ${conversation.title}`}
                  disabled={interactionLocked || conversation.turns.some((turn) => turn.status === "running")}
                  onClick={() => void deleteConversation(conversation)}
                >
                  <Icon name="close" />
                </button>
              </div>
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
            {(selected?.linked_run_ids || []).map((runId) => (
              <button
                className={`pill ${runId === selected?.run_id ? "blue" : ""}`}
                type="button"
                key={runId}
                disabled={interactionLocked || pendingTurn}
                title={runId}
                onClick={() => void submit(undefined, `Use run ${runId}`)}
              >
                {runId === selected?.run_id ? "Active " : ""}{runId.slice(0, 8)}
              </button>
            ))}
            <button className="button ghost" type="button" onClick={() => openSources(allSources)}><Icon name="sources" /><span>Sources</span></button>
          </div>
        </header>

        <div className={`conversation-stream ${!selected?.turns.length ? "welcome-stream" : ""}`} ref={streamRef}>
          {loading ? <LoadingState label="Loading conversations" /> : !selected?.turns.length ? (
            <div className="chat-welcome">
              <span className="welcome-project">{project.name} workspace</span>
              <h1>Measure a page<br />from Chat</h1>
              <p>{workflowRunId ? "Discuss the active run, paste another project run ID, or compare historic measurements." : "Share a project URL or domain. The assistant will clarify the goal, audience, and desired outcome before asking you to confirm the run."}</p>
              <div className="welcome-suggestions">
                {suggestions.map((suggestion, index) => (
                  <button className="suggestion-card" type="button" key={suggestion} disabled={!runtime?.can_send || interactionLocked || pendingTurn} onClick={() => void submit(undefined, suggestion)}>
                    <span className="suggestion-icon"><Icon name={index === 0 ? "trend" : index === 1 ? "search" : index === 2 ? "sources" : "spark"} /></span>
                    <strong>{suggestion}</strong><Icon name="chevron" />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="thread">
              {selected.turns.map((turn) => (
                <div className={`turn-pair ${turn.origin === "workflow" ? "workflow-turn" : ""}`} key={turn.sequence}>
                  {turn.origin !== "workflow" && <div className="thread-message from-user"><div className="thread-bubble"><p>{turn.message}</p></div></div>}
                  <div className="thread-message from-agent">
                    <span className="thread-avatar"><Icon name="spark" /></span>
                    <div className="thread-bubble">
                      <div className="thread-speaker">{turn.origin === "workflow" ? "Measurement insight" : "GEO assistant"}</div>
                      {turn.status === "running" ? <p>Working on your request...</p> : turn.error ? <p className="error-text">{turn.error}</p> : (
                        <AssistantMarkdown
                          content={turn.answer || ""}
                          citations={turn.citations}
                          onCitationClick={(citation) => openSources([citation])}
                        />
                      )}
                      {turn.citations.length > 0 && (
                        <button className="source-summary-button" type="button" onClick={() => openSources(turn.citations)}>
                          <Icon name="sources" /> {turn.citations.length} evidence {turn.citations.length === 1 ? "record" : "records"}
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
          {error && <UnavailableState title="Request unavailable" message={error} compact />}
          {(recoveryId || pendingTurn) && <button className="button ghost" type="button" disabled={sending || changingConversation} onClick={() => void recover()}>Reload saved conversation</button>}
          <form className="prompt-composer" onSubmit={(event) => void submit(event)}>
            <label className="sr-only" htmlFor="chat-input">Message the GEO assistant</label>
            <textarea id="chat-input" maxLength={4000} placeholder={`Plan a measurement for ${projectUrl}...`} value={message} disabled={interactionLocked || pendingTurn || !runtime?.can_send} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => {
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
          {runSourcesLoading && <LoadingState label="Loading complete measurement evidence" />}
          {runSourcesError && <UnavailableState title="Complete evidence unavailable" message={runSourcesError} compact />}
          {visibleDrawerSources.length === 0 ? <UnavailableState title="No sources yet" message="Sources will appear when the assistant returns grounded citations." compact /> : (
            Object.entries(groupSources(visibleDrawerSources)).map(([sourceClass, sources]) => (
              <section className="source-group" key={sourceClass}>
                {sourceClass === "geo-evidence"
                  ? <GeoEvidenceSources
                      sources={sources || []}
                      runUrl={workflowRunId
                        ? `/projects/${project.project_id}/control-plane/${encodeURIComponent(workflowRunId)}`
                        : null}
                    />
                  : (
                    <>
                      <h3>{sourceLabels[sourceClass as SourceClass]}</h3>
                      {sources?.map((source) => (
                        <SourceCard source={source} key={`${source.source_class}-${source.source_id}`} />
                      ))}
                    </>
                  )}
              </section>
            ))
          )}
        </div>
      </aside>
    </section>
  );
}
