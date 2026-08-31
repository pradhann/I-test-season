/* The chat sub-app: sidebar · thread · composer.

   Transport: on conversation open, replay the whole transcript from /events,
   then attach SSE from the last seq. If SSE dies, fall back to polling
   /events every 1s while a turn runs (the seq guard makes replays
   idempotent), and periodically try SSE again. A reload mid-turn replays and
   keeps streaming — the backend's replay buffer does the heavy lifting. */

import React, {
  useCallback, useEffect, useMemo, useRef, useState,
} from "react";
import {
  createConversation, getEvents, listConversations, startTurn, stopTurn,
  streamUrl,
} from "./api.js";
import {
  applyEvent, applyEvents, deriveTitle, emptyTranscript,
} from "./transcript.js";
import {
  ErrorBlock, Lightbox, Prose, Thinking, ToolGroup, TurnFooter, UserMsg,
} from "./thread.jsx";

const LAST_CONV_KEY = "chat-app:last-conv";
const TITLES_KEY = "chat-app:titles";

/* localStorage can throw (private mode, disabled); every touch is guarded. */
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* best effort */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* best effort */ } },
};

function loadTitleCache() {
  try { return JSON.parse(store.get(TITLES_KEY) || "{}") || {}; }
  catch { return {}; }
}

const EVENT_TYPES = ["user", "init", "delta", "text", "tool_use", "tool_result", "done", "error"];

/* ---------------- transport hook ---------------- */

function useConversation(convId) {
  const [transcript, setTranscript] = useState(emptyTranscript);
  const [connErr, setConnErr] = useState(null);
  const stateRef = useRef(transcript);

  useEffect(() => {
    if (!convId) {
      stateRef.current = emptyTranscript();
      setTranscript(stateRef.current);
      setConnErr(null);
      return undefined;
    }
    let dead = false;
    let es = null;
    let pollTimer = null;
    let retryTimer = null;
    let polling = false;

    stateRef.current = emptyTranscript();
    setTranscript(stateRef.current);
    setConnErr(null);

    const push = (next) => {
      if (next !== stateRef.current) {
        stateRef.current = next;
        setTranscript(next);
      }
    };

    const attachSSE = () => {
      if (dead || es || !("EventSource" in window)) return;
      const src = new EventSource(streamUrl(convId, stateRef.current.lastSeq));
      for (const type of EVENT_TYPES) {
        src.addEventListener(type, (ev) => {
          try { push(applyEvent(stateRef.current, JSON.parse(ev.data))); }
          catch { /* malformed frame */ }
        });
      }
      src.onopen = () => { if (!dead) setConnErr(null); };
      src.onerror = () => {
        // CONNECTING = the browser is retrying on its own; leave it alone.
        if (src.readyState === EventSource.CLOSED) {
          src.close();
          if (es === src) es = null;
          if (!dead) {
            startPolling();
            clearTimeout(retryTimer);
            retryTimer = setTimeout(attachSSE, 15000);
          }
        }
      };
      es = src;
    };

    const startPolling = () => {
      if (polling || dead) return;
      polling = true;
      const tick = async () => {
        if (dead || es) { polling = false; return; }
        try {
          const page = await getEvents(convId, stateRef.current.lastSeq);
          let next = applyEvents(stateRef.current, page.events);
          if (typeof page.running === "boolean" && page.running !== next.running) {
            next = { ...next, running: page.running };
          }
          push(next);
          setConnErr(null);
        } catch (e) {
          setConnErr(String(e.message || e));
        }
        pollTimer = setTimeout(tick, stateRef.current.running ? 1000 : 4000);
      };
      tick();
    };

    (async () => {
      try {
        const page = await getEvents(convId, -1);
        if (dead) return;
        let next = applyEvents(emptyTranscript(), page.events);
        if (typeof page.running === "boolean") next = { ...next, running: page.running };
        push(next);
        if ("EventSource" in window) attachSSE(); else startPolling();
      } catch (e) {
        if (!dead) setConnErr(String(e.message || e));
      }
    })();

    return () => {
      dead = true;
      if (es) es.close();
      clearTimeout(pollTimer);
      clearTimeout(retryTimer);
    };
  }, [convId]);

  // A turn we start locally flips running before the stream confirms it.
  const markRunning = useCallback(() => {
    stateRef.current = { ...stateRef.current, running: true };
    setTranscript(stateRef.current);
  }, []);

  return { transcript, connErr, markRunning };
}

/* ---------------- sidebar ---------------- */

function Sidebar({ conversations, titles, activeId, onSelect, onNew }) {
  return (
    <nav className="conv-rail" aria-label="conversations">
      <button type="button" className="new-conv" onClick={onNew}>
        New conversation
      </button>
      <div className="conv-list">
        {conversations.map((c) => (
          <button
            type="button"
            key={c.conv_id}
            className={"conv-item" + (c.conv_id === activeId ? " active" : "")}
            onClick={() => onSelect(c.conv_id)}
            title={titles[c.conv_id] || "conversation"}
          >
            <span className="conv-title">{titles[c.conv_id] || "conversation"}</span>
            <span className="conv-when">{shortWhen(c.updated || c.created)}</span>
          </button>
        ))}
        {!conversations.length && (
          <div className="conv-empty">No conversations yet.</div>
        )}
      </div>
    </nav>
  );
}

function shortWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { day: "numeric", month: "short" });
}

/* ---------------- composer ---------------- */

function Composer({ disabled, disabledReason, onSend }) {
  const [text, setText] = useState("");
  const ref = useRef(null);

  useEffect(() => {
    if (!disabled) ref.current?.focus();
  }, [disabled]);

  const autosize = () => {
    const ta = ref.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 132)}px`; // ~6 lines
  };

  const submit = () => {
    const t = text.trim();
    if (!t || disabled) return;
    setText("");
    requestAnimationFrame(autosize);
    onSend(t);
  };

  return (
    <div className="composer">
      <textarea
        ref={ref}
        rows={1}
        value={text}
        placeholder={disabled ? "" : "Ask about your squad, the market, the field…"}
        disabled={disabled}
        aria-label="message"
        autoFocus
        onChange={(e) => { setText(e.target.value); autosize(); }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
        }}
      />
      <button type="button" className="send" disabled={disabled || !text.trim()} onClick={submit}>
        Send
      </button>
      {disabled && disabledReason && (
        <div className="composer-reason">{disabledReason}</div>
      )}
    </div>
  );
}

/* ---------------- thread ---------------- */

function Thread({ transcript, pendingUser, onOpenChart, bottomRef }) {
  const { items, running } = transcript;
  const streaming = items.some((it) => it.kind === "prose" && it.streaming);
  return (
    <>
      {items.map((it, i) => {
        const key = `${it.kind}-${i}`;
        switch (it.kind) {
          case "user": return <UserMsg key={key} text={it.text} />;
          case "prose": return (
            <Prose key={key} text={it.text} streaming={it.streaming} onOpenChart={onOpenChart} />
          );
          case "tools": return <ToolGroup key={key} calls={it.calls} />;
          case "done": return <TurnFooter key={key} item={it} />;
          case "error": return <ErrorBlock key={key} message={it.message} />;
          default: return null;
        }
      })}
      {pendingUser && <UserMsg text={pendingUser} />}
      {running && !streaming && <Thinking />}
      <div ref={bottomRef} />
    </>
  );
}

/* ---------------- app ---------------- */

export default function App() {
  const [conversations, setConversations] = useState([]);
  const [titles, setTitles] = useState(loadTitleCache);
  const [activeId, setActiveId] = useState(null);
  const [pendingUser, setPendingUser] = useState(null);
  const [sendErr, setSendErr] = useState(null);
  const [lightbox, setLightbox] = useState(null);
  const { transcript, connErr, markRunning } = useConversation(activeId);

  const scrollRef = useRef(null);
  const bottomRef = useRef(null);
  const pinnedRef = useRef(true);
  const [showJump, setShowJump] = useState(false);

  const saveTitle = useCallback((convId, title) => {
    if (!convId || !title) return;
    setTitles((prev) => {
      if (prev[convId] === title) return prev;
      const next = { ...prev, [convId]: title };
      store.set(TITLES_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  // Conversation list, newest first (the API already orders by updated).
  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const res = await listConversations();
        if (dead) return;
        const convs = res.conversations || [];
        setConversations(convs);
        const saved = store.get(LAST_CONV_KEY);
        const ids = new Set(convs.map((c) => c.conv_id));
        if (saved && ids.has(saved)) setActiveId(saved);
        else if (convs.length) setActiveId(convs[0].conv_id);
      } catch (e) {
        if (!dead) setSendErr(`could not load conversations: ${e.message || e}`);
      }
    })();
    return () => { dead = true; };
  }, []);

  // Titles for conversations we have never opened: derive lazily from the
  // first user event (no title API server-side yet).
  useEffect(() => {
    let dead = false;
    const missing = conversations
      .filter((c) => !titles[c.conv_id])
      .slice(0, 20);
    if (!missing.length) return undefined;
    (async () => {
      for (const c of missing) {
        if (dead) return;
        try {
          const page = await getEvents(c.conv_id, -1);
          const t = deriveTitle(applyEvents(emptyTranscript(), page.events));
          if (t && !dead) saveTitle(c.conv_id, t);
        } catch { /* a dead conversation keeps its placeholder */ }
      }
    })();
    return () => { dead = true; };
  }, [conversations]); // eslint-disable-line react-hooks/exhaustive-deps

  // Remember the open conversation; keep its derived title fresh.
  useEffect(() => {
    if (activeId) store.set(LAST_CONV_KEY, activeId);
  }, [activeId]);
  useEffect(() => {
    const t = deriveTitle(transcript);
    if (t && activeId) saveTitle(activeId, t);
  }, [transcript, activeId, saveTitle]);

  // The stream echoes our user message back; drop the optimistic copy then.
  const userCount = useMemo(
    () => transcript.items.filter((it) => it.kind === "user").length,
    [transcript],
  );
  const userCountAtSend = useRef(0);
  useEffect(() => {
    if (pendingUser && userCount > userCountAtSend.current) setPendingUser(null);
  }, [userCount, pendingUser]);

  // Scroll: pinned to bottom while streaming unless the reader scrolled up.
  const onScroll = () => {
    const n = scrollRef.current;
    if (!n) return;
    const pinned = n.scrollHeight - n.scrollTop - n.clientHeight < 80;
    pinnedRef.current = pinned;
    setShowJump(!pinned && transcript.running);
  };
  useEffect(() => {
    if (pinnedRef.current) {
      bottomRef.current?.scrollIntoView?.({ block: "end" });
    } else {
      setShowJump(transcript.running);
    }
  }, [transcript, pendingUser]);
  useEffect(() => {   // conversation switch: jump to the end, re-pin
    pinnedRef.current = true;
    setShowJump(false);
    bottomRef.current?.scrollIntoView?.({ block: "end" });
  }, [activeId]);

  const jumpToLatest = () => {
    pinnedRef.current = true;
    setShowJump(false);
    bottomRef.current?.scrollIntoView?.({ block: "end", behavior: "smooth" });
  };

  const onNew = async () => {
    setSendErr(null);
    try {
      const created = await createConversation();
      setConversations((prev) => [
        { conv_id: created.conv_id, ...created.meta },
        ...prev.filter((c) => c.conv_id !== created.conv_id),
      ]);
      setActiveId(created.conv_id);
    } catch (e) {
      setSendErr(`could not create a conversation: ${e.message || e}`);
    }
  };

  const onSend = async (text) => {
    setSendErr(null);
    let id = activeId;
    try {
      if (!id) {
        const created = await createConversation();
        setConversations((prev) => [{ conv_id: created.conv_id, ...created.meta }, ...prev]);
        setActiveId(created.conv_id);
        id = created.conv_id;
      }
      userCountAtSend.current = userCount;
      setPendingUser(text);
      pinnedRef.current = true;
      await startTurn(id, text);
      markRunning();
    } catch (e) {
      setPendingUser(null);
      if (e.status === 409) {
        setSendErr("a turn is already running in this conversation — stop it or wait");
        markRunning();
      } else {
        setSendErr(String(e.message || e));
      }
    }
  };

  const onStop = async () => {
    if (!activeId) return;
    try { await stopTurn(activeId); }
    catch (e) { setSendErr(`stop failed: ${e.message || e}`); }
  };

  const running = transcript.running;

  return (
    <div className="chat-frame">
      <Sidebar
        conversations={conversations}
        titles={titles}
        activeId={activeId}
        onSelect={setActiveId}
        onNew={onNew}
      />
      <div className="thread-pane">
        <div className="thread-scroll" ref={scrollRef} onScroll={onScroll}>
          <div className="thread">
            {!activeId && !conversations.length && (
              <div className="thread-empty">
                Ask the agent anything — your squad, the market, the field.
                It answers with the warehouse behind it.
              </div>
            )}
            {activeId && !transcript.items.length && !pendingUser && !connErr && (
              <div className="thread-empty">New conversation. Ask away.</div>
            )}
            {connErr && <ErrorBlock message={`connection: ${connErr}`} />}
            <Thread
              transcript={transcript}
              pendingUser={pendingUser}
              onOpenChart={setLightbox}
              bottomRef={bottomRef}
            />
          </div>
        </div>
        {showJump && (
          <button type="button" className="jump-chip" onClick={jumpToLatest}>
            ↓ jump to latest
          </button>
        )}
        <div className="composer-dock">
          {sendErr && <div className="send-err">{sendErr}</div>}
          {running && (
            <div className="run-row">
              <span className="run-note">the agent is working…</span>
              <button type="button" className="stop" onClick={onStop}>Stop</button>
            </div>
          )}
          <Composer
            disabled={running}
            disabledReason="a turn is running — stop it or wait for it to finish"
            onSend={onSend}
          />
        </div>
      </div>
      <Lightbox src={lightbox} onClose={() => setLightbox(null)} />
    </div>
  );
}
