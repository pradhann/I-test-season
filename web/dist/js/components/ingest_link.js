/* Add a source: paste a link, preview it, then decide whether to spend the GPU.
 *
 * Lifted out of the creators view whole, because it is an operator control and
 * not part of reading what a creator said. The creators tab mounts it behind a
 * fold on level 2; nothing else about it changed in the move.
 *
 * POST /api/ingest/link runs one page fetch and parks at a preview with
 * `awaiting_decision: true`. Accept is the only call that spends GPU seconds;
 * decline spends none. The job machinery below renders the preview, the two
 * actions, the expiry and the stage ladder.
 *
 * Untrusted text: every title, description and bullet on a preview is
 * third-party prose. It is rendered through textContent, never as markup, and
 * never read as an instruction.
 */

import { el, fmtAge } from "/js/app.js";

/* --------------------------------------------------------------- helpers */

/* A value with a stated fallback, a branch written as a call, and a string
   that is present only when a condition holds. The house rule is that a
   chain of nested conditionals is not something a reader can read aloud, so
   this file spells its branches out. */
function or(v, fallback) {
  if (v == null) return fallback;
  return v;
}
function pick(cond, a, b) {
  if (cond) return a;
  return b;
}
function when(cond, text) {
  if (cond) return text;
  return "";
}

const plural = (n, one, many) => {
  if (n == null) return null;
  if (n === 1) return `${n} ${one}`;
  return `${n} ${or(many, one + "s")}`;
};

const parseTs = iso => {
  if (!iso) return null;
  return new Date(String(iso).replace(" ", "T"));
};

/* Age of a stamp, in the app's words. The span comes from the shared
   `fmtAge`; only the freshness class is local. */
function relAge(iso) {
  const span = fmtAge(iso);
  const d = parseTs(iso);
  if (span == null || !d || isNaN(d)) return { text: "date unknown", cls: "bad" };
  const h = (Date.now() - d) / 3.6e6;
  if (h < 72) return { text: `${span} ago`, cls: "good" };
  if (h < 336) return { text: `${span} ago`, cls: "warn" };
  return { text: `${span} ago`, cls: "bad" };
}

function clock(s) {
  if (s == null || !isFinite(s)) return null;
  const t = Math.max(0, Math.round(s));
  const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), sec = t % 60;
  const pad = n => String(n).padStart(2, "0");
  if (h) return `${h}:${pad(m)}:${pad(sec)}`;
  return `${m}:${pad(sec)}`;
}

/* Canonical key for "is this the same video". Two URL forms of one YouTube
   video are the duplicate case the warehouse already contains twice; the
   paste bar catches it locally before it can happen a third time. */
function canonicalKey(raw) {
  let u;
  try { u = new URL(raw); } catch { return String(or(raw, "")).trim().toLowerCase(); }
  const host = u.hostname.replace(/^www\./, "").toLowerCase();
  if (/(^|\.)youtube\.com$/.test(host)) {
    const v = u.searchParams.get("v");
    if (v) return `yt:${v}`;
    const m = u.pathname.match(/\/(shorts|embed|live)\/([\w-]+)/);
    if (m) return `yt:${m[2]}`;
  }
  if (host === "youtu.be") return `yt:${u.pathname.slice(1)}`;
  return `${host}${u.pathname}`.replace(/\/+$/, "").toLowerCase();
}

/* Which transcription route the server said it would take, in its words. */
function pathSentence(path) {
  if (path === "captions")
    return "Captions path, measured at about 286x realtime.";
  if (path === "asr")
    return "No captions, so local speech-to-text, measured at about 11.5x realtime.";
  return `Path: ${path}.`;
}

/* The gameweek chip on a preview: the payload's own basis, or nothing. */
function gwChip(gw) {
  if (gw.is_guess) return el("span", "chip warn", "a guess");
  if (gw.basis === "stated") return el("span", "chip s1", "stated in the content");
  return null;
}

/* The stage ladder, one rung at a time. */
function stageIndex(names, stage) {
  if (!stage) return -1;
  return names.indexOf(stage);
}
function stageState(jobState, idx, i) {
  if (jobState === "done") return "done";
  if (idx < 0) return pick(i === 0, "now", "wait");
  if (i < idx) return "done";
  if (i === idx) return "now";
  return "wait";
}
function stageMark(halt, state) {
  if (halt) return "\u25c6";
  if (state === "done") return "\u2713";
  if (state === "now") return "\u25d0";
  return "\u25cb";
}

/* The state word each job shows, one per closed enum value the poll returns. */
const JOB_STATE_LABEL = {
  posting: "SENDING", running: "WORKING", done: "DONE", down: "UNAVAILABLE",
  declined: "STOPPED", duplicate: "DUPLICATE", nomedia: "NO MEDIA",
  notepisode: "NOT AN EPISODE", failed: "FAILED",
  preview: "YOUR CALL", passed: "NOT TRANSCRIBED", expired: "PREVIEW EXPIRED",
  cancelled: "CANCELLED", stopping: "STOPPING", robots: "ROBOTS SAY NO",
};
function jobStateLabel(j) {
  return or(JOB_STATE_LABEL[j.state], j.state);
}

/* ==================================================================== mount
 *
 * One card, its own job list, its own session duplicate map. The polls stop
 * on their own once `host` leaves the document, so a re-entered view never
 * leaves a timer behind.
 */
export function mountIngestLink(host) {
  const linkCard = host;
  let linkBody = null;
  const jobs = [];                    // paste-a-link jobs, newest first
  const pasted = new Map();           // canonical key -> url, this session only

  /* ======================================================== the link bar
   *
   * POST /api/ingest/link runs one page fetch and parks at a preview with
   * `awaiting_decision: true`. Accept is the only call that spends GPU
   * seconds; decline spends none. The job machinery below renders the
   * preview, the two actions, the expiry and the stage ladder.
   */

  const LINK_STAGES = ["fetch", "preview", "transcribe", "analyse", "attribute"];

  function renderLinkBar() {
    linkCard.textContent = "";
    linkCard.appendChild(el("h2", null, "Add a source"));

    const row = el("div", "toolbar");
    row.appendChild(el("span", "tlabel", "Paste a link"));
    const input = el("input", "cx-url");
    input.type = "url";
    input.placeholder = "a YouTube video or a podcast episode";
    input.setAttribute("aria-label", "URL of a YouTube video or podcast episode");
    const btn = el("button", "primary", "Add");
    row.append(input, btn);
    linkCard.appendChild(row);

    const gate = el("p", "cx-tiny cx-gate");
    gate.append("Preview first; nothing is transcribed until you accept.");
    gate.title = "captions run at about 286x realtime (a 20-minute video in about 4s); " +
      "local speech-to-text at about 11.5x (about 105s). Both measured from stored runs.";
    linkCard.appendChild(gate);

    const err = el("div", "cx-linkerr");
    linkCard.appendChild(err);
    linkBody = el("div", "cx-jobs");
    linkCard.appendChild(linkBody);

    const submit = () => {
      err.textContent = "";
      const raw = input.value.trim();
      if (!raw) return;
      if (!/^https?:\/\/\S+$/i.test(raw)) {
        err.appendChild(failLine("That is not a link.",
          "It needs to start with http:// or https://. Nothing was sent."));
        return;
      }
      const key = canonicalKey(raw);
      if (pasted.has(key)) {
        err.appendChild(failLine("Already pasted this session.",
          `Same video as ${pasted.get(key)}. Nothing was sent.`));
        return;
      }
      pasted.set(key, raw);
      input.value = "";
      startJob(raw, key);
    };
    btn.onclick = submit;
    input.onkeydown = e => { if (e.key === "Enter") submit(); };
    renderJobs();
  }

  function failLine(head, detail, kind) {
    const d = el("div", "cx-fail" + when(kind, " " + kind));
    d.appendChild(el("b", null, head));
    d.appendChild(el("div", "sub", detail));
    return d;
  }

  /* ---------------------------------------------------------- job driving */

  function stopJob(job) {
    clearInterval(job.timer); clearTimeout(job.timer); job.timer = null;
    clearInterval(job.tick);  clearTimeout(job.tick);  job.tick = null;
  }

  /* A job that stored nothing may be pasted again, because the duplicate
     guard exists to stop a second INGEST, and a declined, expired or
     cancelled job did not ingest anything. */
  const STORED_NOTHING = new Set(["passed", "expired", "cancelled", "failed",
                                  "down", "robots", "nomedia", "notepisode",
                                  "declined"]);

  async function startJob(url, key) {
    const job = { url, key, state: "posting", stages: null, stage: null,
                  pct: null, eta: null, error: null, item_id: null,
                  started: Date.now(), timer: null, tick: null, path: null,
                  path_reason: null, eta_basis: null, eta_reason: null,
                  preview: null, expires: null, accepted: false, aborting: false,
                  busy: null, conflict: null, actErr: null, descOpen: false,
                  dupes: [], note: null, p: null };
    jobs.unshift(job);
    renderJobs();
    /* Raw fetch, not postJSON: the STATUS CODE is the thing that tells a
       not-deployed endpoint apart from a rejected link, and postJSON throws
       it away into a message string. */
    let r;
    try {
      r = await fetch("/api/ingest/link", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
    } catch (e) {
      job.state = "down";
      job.error = `the request never reached the server (${String(e.message || e)})`;
      renderJobs(); return;
    }
    if (r.status === 404 || r.status === 405 || r.status === 501) {
      job.state = "down";
      job.error = `POST /api/ingest/link returned HTTP ${r.status}`;
      renderJobs(); return;
    }
    if (r.status === 403 || r.status === 429) {
      job.state = "declined";
      job.error = `the server answered HTTP ${r.status}`;
      renderJobs(); return;
    }
    let payload = null;
    try { payload = await r.json(); } catch { /* body may be empty */ }
    if (!r.ok) {
      job.state = "failed";
      job.error = (payload && (payload.detail || payload.error)) || `HTTP ${r.status}`;
      renderJobs(); return;
    }
    job.job_id = payload && payload.job_id;
    job.stages = (payload && payload.stages) || null;
    if (!job.job_id) {
      job.state = "failed";
      job.error = "the endpoint accepted the link but returned no job_id";
      renderJobs(); return;
    }
    job.state = "running";
    renderJobs();
    poll(job);
  }

  function poll(job) {
    stopJob(job);
    const once = async () => {
      if (!document.body.contains(linkCard)) { stopJob(job); return; }
      let r, p = null;
      try {
        r = await fetch(`/api/ingest/link/${encodeURIComponent(job.job_id)}`);
        p = await r.json();
      } catch (e) {
        stopJob(job);
        job.state = "down";
        job.error = `polling failed: ${String(e.message || e)}`;
        renderJobs(); return;
      }
      if (!r.ok) {
        stopJob(job);
        job.state = pick(r.status === 404, "down", "failed");
        job.error = (p && (p.detail || p.error)) || `HTTP ${r.status}`;
        renderJobs(); return;
      }
      applyPoll(job, p);
      renderJobs();
    };
    job.timer = setInterval(once, 900);
    once();
  }

  /* Every terminal answer the server can give, BY ITS OWN CODE. The regex
     classifier below stays as the fallback for a server that predates
     `error_code`, but a code is exact and a message is a guess about a
     message. */
  const ERR_STATE = {
    declined: "passed",                    // the OWNER said no, at the halt
    preview_expired: "expired",
    cancelled: "cancelled",
    not_an_episode: "notepisode",
    source_refused: "declined",            // the SOURCE said no (403 / 429)
    robots_disallow: "robots",
    no_transcript_source: "nomedia",
    no_asr_route_for_pasted_media: "nomedia",
  };

  function errState(code, msg) {
    if (code && ERR_STATE[code]) return ERR_STATE[code];
    if (code) return "failed";
    return classifyError(msg);
  }

  function applyPoll(job, p) {
    if (!p || typeof p !== "object") return;
    job.p = p;
    if (Array.isArray(p.stages) && p.stages.length) job.stages = p.stages;
    job.stage = or(p.stage, job.stage);
    job.pct = or(p.pct, job.pct);
    job.item_id = or(p.item_id, job.item_id);
    /* NOT sticky. The server nulls the ETA when the job ends and says why in
       `eta_reason`; carrying the old number forward would show a countdown
       for work that is not going to happen. */
    job.eta = or(p.eta_s, null);
    job.eta_basis = or(p.eta_basis, null);
    job.eta_reason = or(p.eta_reason, null);
    if (p.transcript_path) job.path = p.transcript_path;
    if (p.path_reason) job.path_reason = p.path_reason;
    if (p.preview) job.preview = p.preview;
    if (p.preview_expires_utc) job.expires = p.preview_expires_utc;
    job.note = or(p.note, job.note);

    if (p.awaiting_decision) {
      /* PARKED IS NOT RUNNING. Nothing advances until a button is pressed, so
         the poll stops here. What changes from now on is the clock, and the
         clock is local. */
      stopJob(job);
      job.state = "preview";
      startExpiryTicker(job);
      return;
    }
    if (p.error) {
      stopJob(job);
      job.error = String(p.error);
      job.state = errState(p.error_code, job.error);
      if (STORED_NOTHING.has(job.state)) pasted.delete(job.key);
      return;
    }
    if (p.done) {
      stopJob(job);
      job.dupes = p.duplicate_of || [];
      /* Finished without an accept and without an error means the server
         SKIPPED the halt, and only one thing does that on a clean run: the
         video is already in the warehouse. No decision was owed because
         nothing would have been transcribed either way. */
      job.state = pick(!job.accepted && (job.dupes.length || p.note),
                   "duplicate", "done");
      return;
    }
    job.state = pick(job.aborting, "stopping", "running");
  }

  /* The four failures the warehouse already contains, handled BY NAME. */
  function classifyError(msg) {
    const m = String(msg).toLowerCase();
    if (/\b(403|429)\b|forbidden|rate.?limit|too many requests/.test(m)) return "declined";
    // `exists` alone matched the not-an-episode refusal, whose own sentence
    // ends "that is what this refusal exists to prevent" -- so pasting an FPL
    // league invite reported "Already held. Its take is on the board already",
    // asserting a stored item that does not exist and swallowing the real
    // reason. Order matters too: the specific refusal is tested first.
    if (/too.thin|toothin|not (a|an) (episode|article|video)|no text|3 characters|league/
        .test(m)) return "notepisode";
    if (/duplicate|already (held|ingested|have)|already exists/.test(m)) {
      return "duplicate";
    }
    if (/no captions|no audio|no_media|no enclosure|nothing to transcribe/.test(m)) return "nomedia";
    return "failed";
  }

  /* ------------------------------------------------------------- the gate */

  function expiryLeft(job) {
    if (!job.expires) return null;
    const t = Date.parse(String(job.expires).replace(" ", "T"));
    if (!isFinite(t)) return null;
    return (t - Date.now()) / 1000;
  }

  function expiryText(job) {
    const left = expiryLeft(job);
    if (left == null) return "the payload carried no preview_expires_utc";
    if (left <= 0) return "the 30 minutes are up; asking the server";
    const m = Math.floor(left / 60), s = Math.floor(left % 60);
    return `${m}m ${String(s).padStart(2, "0")}s left`;
  }

  function startExpiryTicker(job) {
    stopJob(job);
    if (!job.expires) return;
    job.tick = setInterval(() => {
      if (!document.body.contains(linkCard)) { stopJob(job); return; }
      if (job.state !== "preview") { stopJob(job); return; }
      const left = expiryLeft(job);
      if (left != null && left <= 0) { stopJob(job); confirmExpiry(job); return; }
      if (job.expEl && job.expEl.isConnected) job.expEl.textContent = expiryText(job);
    }, 1000);
  }

  /* The server expires a preview LAZILY, on the next poll. So a countdown
     reaching zero is the reason to go and ask for an answer, not an answer.
     Until the server agrees, this keeps saying "parked", because inventing an
     expiry the server has not applied is inventing state. */
  async function confirmExpiry(job) {
    let r, p = null;
    try {
      r = await fetch(`/api/ingest/link/${encodeURIComponent(job.job_id)}`);
      p = await r.json();
    } catch { /* the retry below is the handler */ }
    if (r && r.ok && p) { applyPoll(job, p); renderJobs(); }
    if (job.state === "preview") job.tick = setTimeout(() => confirmExpiry(job), 5000);
  }

  async function decide(job, verb) {
    if (job.busy) return;
    job.busy = verb; job.conflict = null; job.actErr = null;
    renderJobs();
    let r, p = null;
    try {
      r = await fetch(
        `/api/ingest/link/${encodeURIComponent(job.job_id)}/${verb}`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "" }) });
      p = await r.json();
    } catch (e) {
      job.busy = null;
      job.actErr = `the ${verb} never reached the server (${String(e.message || e)}). ` +
        "Nothing has changed on the server either way.";
      renderJobs(); return;
    }
    job.busy = null;
    if (r.status === 409) {
      job.conflict = (p && p.detail) ||
        "the server says this job is no longer waiting for a decision";
      if (p && p.job) applyPoll(job, p.job);
      renderJobs(); return;
    }
    if (!r.ok) {
      job.actErr = (p && (p.detail || p.error)) || `HTTP ${r.status}`;
      renderJobs(); return;
    }
    if (verb === "accept") job.accepted = true;
    applyPoll(job, p);
    renderJobs();
    if (verb === "accept") poll(job);
  }

  async function abortJob(job) {
    if (job.busy) return;
    job.busy = "abort"; job.aborting = true; job.conflict = null; job.actErr = null;
    renderJobs();
    let r, p = null;
    try {
      r = await fetch(`/api/ingest/link/${encodeURIComponent(job.job_id)}`,
                      { method: "DELETE" });
      p = await r.json();
    } catch (e) {
      job.busy = null; job.aborting = false;
      job.actErr = `the abort never reached the server (${String(e.message || e)}). ` +
        "The job is still running there.";
      renderJobs(); return;
    }
    job.busy = null;
    if (r.status === 409) {
      job.aborting = false;
      job.conflict = (p && p.detail) || `the server answered HTTP ${r.status}`;
      if (p && p.job) applyPoll(job, p.job);
      renderJobs(); return;
    }
    if (!r.ok) {
      job.aborting = false;
      job.actErr = (p && (p.detail || p.error)) || `HTTP ${r.status}`;
      renderJobs(); return;
    }
    applyPoll(job, p);
    renderJobs();
    /* A cancel is a REQUEST. The worker applies it at its next checkpoint, and
       the one write has no checkpoint inside it, so keep polling until the
       server says which of the two happened. */
    if (!(job.p && job.p.done)) poll(job);
  }

  function repaste(job) {
    pasted.set(job.key, job.url);
    startJob(job.url, job.key);
  }

  /* --------------------------------------------------------- job renderer */

  function renderJobs() {
    if (!linkBody) return;
    linkBody.textContent = "";
    for (const job of jobs) linkBody.appendChild(renderJob(job));
  }

  function renderJob(job) {
    const d = el("div", "cx-job" + when(job.state === "preview", " parked"));
    const hd = el("div", "cx-job-head");
    hd.appendChild(el("span", "cx-job-state " + job.state, jobStateLabel(job)));
    const a = el("a", "cx-job-url", job.url);
    a.href = job.url; a.target = "_blank"; a.rel = "noopener noreferrer";
    hd.appendChild(a);
    const x = el("button", "cx-x", "✕");
    x.title = "remove this row (the server keeps whatever it is doing)";
    x.onclick = () => {
      stopJob(job);
      if (STORED_NOTHING.has(job.state) || job.state === "preview")
        pasted.delete(job.key);
      jobs.splice(jobs.indexOf(job), 1); renderJobs();
    };
    hd.appendChild(x);
    d.appendChild(hd);

    if (job.state === "down") {
      d.appendChild(failLine("Link ingestion is not deployed on this server.",
        `${job.error}. The board above is a different service and is ` +
        "unaffected, so nothing on this page is stale because of it. When " +
        "the endpoint lands, this bar starts working with no change here."));
      return d;
    }
    if (job.state === "declined") {
      d.appendChild(failLine("The source declined the request.",
        `${job.error}. The URL is private, age-gated or rate-limited. ` +
        "This has stopped and does not retry, because repeated 403 and 429 " +
        "responses can get the whole source blocked.", "stop"));
      return d;
    }
    if (job.state === "robots") {
      d.appendChild(failLine("robots.txt disallows this URL for our fetcher.",
        `${job.error} Nothing was fetched beyond the robots file, and nothing ` +
        "was stored. This is our own rule, obeyed rather than worked around.",
        "stop"));
      return d;
    }
    if (job.state === "duplicate") {
      d.appendChild(failLine("Already held.",
        when(job.item_id, `Stored as ${job.item_id}. `) +
        "The same video under a second URL form is already in the corpus, so " +
        "nothing was ingested twice. Its take is on the board already."));
      if (job.note) d.appendChild(el("div", "cx-verbatim", job.note));
      if (job.dupes && job.dupes.length > 1)
        d.appendChild(el("div", "sub",
          `The warehouse holds it under ${plural(job.dupes.length, "item id")}: ` +
          job.dupes.join(", ") + ". watch?v=, youtu.be/ and /live/ are one video."));
      d.appendChild(el("div", "sub",
        "No decision was owed here: a duplicate skips the preview halt " +
        "because nothing would have been transcribed either way."));
      return d;
    }
    if (job.state === "notepisode") {
      d.appendChild(failLine("That link is not an episode.",
        `${job.error} There is no substantive text behind this URL, so ` +
        "there is nothing to transcribe or analyse. Paste a video or an " +
        "episode page instead."));
      d.appendChild(el("div", "sub",
        "No preview was offered: nothing would have been spent either way."));
      return d;
    }
    if (job.state === "nomedia") {
      d.appendChild(failLine("No captions published, and no audio file behind the page.",
        `${job.error}. There is nothing to read and nothing to transcribe. ` +
        "For a podcast, the episode's YouTube link usually has captions, " +
        "which is the four-second path rather than the transcription one."));
      return d;
    }
    if (job.state === "passed") {
      const box = el("div", "cx-pass");
      box.appendChild(el("b", null, "Declined. Nothing was spent."));
      box.appendChild(el("div", "sub", String(job.error ||
        "the server recorded the decline but returned no sentence about it")));
      box.appendChild(againRow(job, "Paste it again"));
      d.appendChild(box);
      return d;
    }
    if (job.state === "expired") {
      const box = el("div", "cx-fail");
      box.appendChild(el("b", null, "The preview expired before you decided."));
      box.appendChild(el("div", "sub", String(job.error ||
        "the preview aged out; the server returned no sentence about it")));
      box.appendChild(el("div", "sub",
        "Expiring writes nothing and undoes nothing, because nothing had been " +
        "written. A fresh paste fetches the page again and parks again."));
      box.appendChild(againRow(job, "Paste it again"));
      d.appendChild(box);
      return d;
    }
    if (job.state === "cancelled") {
      const late = !!(job.p && job.p.cancelled_after_write);
      const box = el("div", "cx-fail" + when(late, " stop"));
      box.appendChild(el("b", null, pick(late,
      "Stopped, but the cancel arrived after the write.",
      "Stopped before anything was written.")));
      box.appendChild(el("div", "sub", String(job.error ||
        "the server recorded the cancel but returned no sentence about it")));
      if (late) {
        const id = job.p && job.p.discarded_item_id;
        box.appendChild(el("div", "sub",
          "That is not the same as never having run. The transcription and " +
          "analysis finished and " +
        pick(id, `item ${id} was written, then `, "what they wrote was ") +
          "discarded: hidden from every read path, not deleted. GPU seconds " +
          "were spent. Restoring it is possible; the archive still has it."));
      }
      box.appendChild(againRow(job, "Paste it again"));
      d.appendChild(box);
      return d;
    }
    if (job.state === "failed") {
      d.appendChild(failLine("The job failed.", String(job.error)));
      if (job.p && job.p.error_code)
        d.appendChild(el("div", "sub", `error_code: ${job.p.error_code}`));
      d.appendChild(againRow(job, "Try it again"));
      return d;
    }

    /* ---- the live states: parked, running, stopping, done -------------- */

    if (job.state === "preview") {
      d.appendChild(renderPreview(job));
      d.appendChild(el("div", "cx-ledger-cap", "What yes would run"));
    } else if (job.path) {
      const p = el("div", "cx-job-path");
      p.append(pathSentence(job.path));
      d.appendChild(p);
    }

    d.appendChild(renderLedger(job));

    if (job.conflict) d.appendChild(failLine("The server disagreed.", job.conflict));
    if (job.actErr) d.appendChild(failLine("That request did not land.", job.actErr));

    if (job.state === "done") {
      const ok = el("div", "cx-job-ok");
      ok.append(el("b", null, "Ingested."),
        pick(job.item_id, ` Stored as ${job.item_id}. `, " "),
        "It joins the board on the next panel read; reload to see it.");
      d.appendChild(ok);
      d.appendChild(renderTake(job));
    } else {
      if (job.state === "running" || job.state === "stopping") {
        const acts = el("div", "cx-job-acts");
        const stop = el("button", "",
        pick(job.state === "stopping", "Stopping…", "Stop this"));
        stop.disabled = job.state === "stopping" || job.busy === "abort";
        stop.onclick = () => abortJob(job);
        acts.appendChild(stop);
        acts.appendChild(el("span", "sub",
          pick(job.stage === "transcribe" || job.stage === "analyse",
             "The transcribe and analyse call cannot be interrupted mid-way. A " +
             "stop now lets it finish and discards what it wrote. The " +
             "seconds are spent either way, and this row will say so.",
             "Nothing has been written yet, so stopping now is clean by " +
             "ordering rather than by cleanup.")));
        d.appendChild(acts);
      }
      d.appendChild(el("div", "sub",
        "This keeps running on the server if you leave the page. This ledger " +
        "does not; it stops updating when the view unmounts."));
    }
    return d;
  }

  function againRow(job, label) {
    const row = el("div", "cx-job-acts");
    const b = el("button", "", label);
    b.onclick = () => { jobs.splice(jobs.indexOf(job), 1); repaste(job); };
    row.appendChild(b);
    row.appendChild(el("span", "sub",
      "Nothing was stored for this url, so pasting it again is a fresh page " +
      "fetch and a fresh preview."));
    return row;
  }

  /* ---- the preview card: everything needed to judge relevance, once ---- */

  function renderPreview(job) {
    const pv = job.preview || {};
    const box = el("div", "cx-pv");

    box.appendChild(el("h3", "cx-pv-title", pv.title ||
      "the page stated no title"));

    /* WHO. The resolved identity, an honest "we do not track this one", or an
       honest absence, never a placeholder standing in for a name. */
    const who = el("div", "cx-pv-who");
    const name = pv.creator || pv.channel || null;
    if (name) {
      who.appendChild(el("b", null, name));
      if (pv.creator == null)
        who.appendChild(el("span", "chip warn", "unresolved; this is the channel name"));
      else if (pv.tracked === true)
        who.appendChild(el("span", "chip s1", "on your panel"));
      else if (pv.tracked === false)
        who.appendChild(el("span", "chip warn", "a real channel you do not track"));
      else
        who.appendChild(el("span", "chip", "the payload did not say whether it is tracked"));
    } else {
      who.appendChild(el("b", null, "Nobody is named on this page"));
      who.appendChild(el("span", "chip warn", "creator unresolved"));
    }
    if (pv.channel && pv.creator && pv.channel !== pv.creator)
      who.appendChild(el("span", "cx-pv-chan", `channel: ${pv.channel}`));
    box.appendChild(who);
    if (pv.creator_reason) box.appendChild(el("div", "cx-pv-why", pv.creator_reason));
    else if (pv.creator_basis)
      box.appendChild(el("div", "cx-pv-why",
        `resolved by ${pv.creator_basis}; the payload carried no creator_reason.`));
    else
      box.appendChild(el("div", "cx-pv-why",
        "the payload carried no creator_basis and no creator_reason, so how " +
        "this name was arrived at is not recorded."));

    /* WHEN, and therefore WHICH WEEK, with the basis attached to the week,
       never separated from it. An inferred week rendered bare is a guess
       presented as a fact. */
    const facts = el("dl", "cx-pv-facts");
    const fact = (k, v, why, chip) => {
      facts.appendChild(el("dt", null, k));
      const dd = el("dd");
      dd.appendChild(el("span", "cx-pv-v", v));
      if (chip) dd.appendChild(chip);
      if (why) dd.appendChild(el("div", "cx-pv-why", why));
      facts.appendChild(dd);
    };

    const shown = fmtWhen(pv.published_at);
    fact("Published",
      pick(shown, `${shown} · ${relAge(pv.published_at).text}`, "not stated"),
      pv.published_basis || null);

    const gw = pv.gameweek;
    if (gw && gw.label) {
      fact("Gameweek", gw.label, gw.reason || null,
        gwChip(gw));
    } else {
      fact("Gameweek", "none derived",
        "the payload carried no gameweek block for this preview. With no " +
        "publication date there is no deadline to place it against, and none " +
        "is invented.");
    }

    fact("Length", clock(pv.media_seconds) ||
      "the source did not state a duration");

    box.appendChild(facts);

    /* WHAT IT SAYS IT IS ABOUT. Verbatim, and clamped rather than cut, so the
       judgement is made on the source's own words. */
    if (pv.description) {
      const desc = el("p", "cx-pv-desc" + when(job.descOpen, " open"),
        String(pv.description));
      box.appendChild(desc);
      if (String(pv.description).length > 260) {
        const more = el("button", "cx-link-btn",
          pick(job.descOpen, "less", "more of the description"));
        more.onclick = () => { job.descOpen = !job.descOpen; renderJobs(); };
        box.appendChild(more);
      }
    } else {
      box.appendChild(el("p", "cx-pv-desc empty-note",
        "The page carried no description, so there is nothing here to read " +
        "except the title."));
    }

    if (Array.isArray(pv.duplicate_of) && pv.duplicate_of.length)
      box.appendChild(el("div", "cx-pv-why",
        `The warehouse already holds ${plural(pv.duplicate_of.length, "row")} ` +
        `for this url shape (${pv.duplicate_of.join(", ")}), but none of them ` +
        "is this video's stored item."));

    /* WHAT YES COSTS. The measured ETA for THIS video, or the payload's own
       sentence about why there is not one. Never a number we made up. */
    const cost = el("div", "cx-pv-cost");
    const line = el("div", "cx-pv-costline");
    if (pv.eta_s != null) {
      line.append("Saying yes runs ", el("b", null, secs(pv.eta_s)),
                  " of transcription on this machine.");
    } else {
      line.append(el("b", null, "No ETA for this one."),
        " " + (pv.eta_reason || "the payload gave no eta_s and no eta_reason, " +
               "so there is nothing to quote, and no number is invented here."));
    }
    cost.appendChild(line);
    const path = el("div", "cx-pv-why");
    path.append(pathLabel(pv.transcript_path),
      pick(pv.path_reason, `. ${pv.path_reason}`,
           ". The payload carried no path_reason."));
    cost.appendChild(path);
    if (pv.eta_s != null && pv.eta_basis)
      cost.appendChild(el("div", "cx-pv-why", `measured basis: ${pv.eta_basis}`));
    box.appendChild(cost);

    /* TWO ACTIONS. The free one says it is free where the button is. */
    const acts = el("div", "cx-pv-acts");
    const yes = el("button", "primary",
      pick(job.busy === "accept", "Starting…", "Transcribe it"));
    yes.disabled = !!job.busy;
    yes.onclick = () => decide(job, "accept");
    const no = el("button", "cx-no",
      pick(job.busy === "decline", "Declining…", "Not relevant"));
    no.disabled = !!job.busy;
    no.onclick = () => decide(job, "decline");
    acts.append(yes, no);
    box.appendChild(acts);
    box.appendChild(el("div", "cx-pv-freeline",
      "Declining costs nothing: the page has already been fetched, and " +
      "nothing is transcribed, analysed or stored unless you say yes."));

    if (job.conflict) box.appendChild(failLine("The server disagreed.", job.conflict));
    if (job.actErr) box.appendChild(failLine("That request did not land.", job.actErr));

    /* THE CLOCK. A parked job that has aged out must not look live. */
    const exp = el("div", "cx-pv-exp");
    const until = fmtWhen(job.expires);
    job.expEl = el("b", null, expiryText(job));
    exp.append(job.expEl, pick(until,
      ` to decide. This preview is held until ${until}, then it expires and ` +
      "you paste the link again.",
      ". The payload carried no preview_expires_utc, so when this parks out " +
      "is unknown to this page."));
    box.appendChild(exp);
    return box;
  }

  function pathLabel(path) {
    if (path === "captions") return "Published captions, about 286× realtime";
    if (path === "asr") return "Local speech-to-text, about 11.5× realtime";
    if (path === "text") return "Article text, nothing to transcribe";
    if (!path) return "No transcription path stated in the payload";
    return `Path: ${path}`;
  }

  /* Seconds, kept in seconds where seconds is what makes two decisions
     comparable: "about 4 seconds" and "about 105 seconds" must be readable as
     the same unit at a glance. */
  function secs(s) {
    const n = Number(s);
    if (!isFinite(n)) return "an unstated amount";
    if (n < 1) return "under a second";
    if (n < 300) return `about ${Math.round(n)} seconds`;
    return `about ${Math.round(n / 60)} minutes`;
  }

  function fmtWhen(iso) {
    const d = parseTs(iso);
    if (!d || isNaN(d)) return null;
    return d.toLocaleString(undefined, { year: "numeric", month: "short",
      day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  /* ---- the stage ladder ------------------------------------------------ */

  function renderLedger(job) {
    const names = job.stages || LINK_STAGES;
    const idx = stageIndex(names, job.stage);
    const parked = job.state === "preview";
    const ledger = el("div", "cx-stages");
    names.forEach((nm, i) => {
      const state = stageState(job.state, idx, i);
      const halt = parked && state === "now";
      const row = el("div", "cx-stage " + state + when(halt, " halt"));
      row.appendChild(el("span", "cx-stage-mark", stageMark(halt, state)));
      row.appendChild(el("span", "cx-stage-name", nm));
      if (halt) row.appendChild(el("span", "cx-stage-note", "parked, your call"));
      if (!parked && state === "now" && job.pct != null) {
        const barwrap = el("span", "cx-stage-bar");
        const fill = el("span", "cx-stage-fill");
        fill.style.width = `${Math.max(0, Math.min(100, Number(job.pct)))}%`;
        barwrap.appendChild(fill);
        row.appendChild(barwrap);
        row.appendChild(el("span", "cx-stage-pct", `${Math.round(job.pct)}%`));
      }
      /* `eta_s` is the ESTIMATE FOR THE WHOLE TRANSCRIPTION, set once from a
         measured rate and a measured duration. The server never decrements
         it, so calling it "left" was a countdown that never counted down. */
      if (!parked && state === "now" && nm === "transcribe" && job.eta != null)
        row.appendChild(el("span", "cx-stage-eta",
          `≈${Math.round(job.eta)}s for this video`));
      ledger.appendChild(row);
    });
    return ledger;
  }

  /* ---- what it turned into --------------------------------------------- */

  /* The take's own bucket names, which are NOT the analysis's: `_take` renames
     `captaincy` to `captain` and `chip_advice` to `chips` on the way out, and
     lifts every "watch" call into `watching` so a watch is never counted as a
     transfer in. Counting the wrong keys here would print "no calls at all"
     over a take that has twelve. */
  const TAKE_BUCKETS = [["transfers_in", "in"], ["transfers_out", "out"],
                        ["captain", "captain"], ["differentials", "differential"],
                        ["chips", "chip"], ["watching", "watching"]];

  function renderTake(job) {
    const p = job.p || {}, r = p.result || null;
    const box = el("div", "cx-take");
    const who = p.creator || (r && r.creator);
    if (who) {
      const w = el("div", "cx-take-row");
      w.appendChild(el("span", "cx-take-k", "Creator"));
      w.appendChild(el("span", null, who));
      if (p.tracked === false) w.appendChild(el("span", "chip warn", "not tracked"));
      else if (p.tracked === true) w.appendChild(el("span", "chip s1", "on your panel"));
      box.appendChild(w);
      if (p.creator_reason) box.appendChild(el("div", "cx-pv-why", p.creator_reason));
    }
    const gw = p.gameweek;
    if (gw && gw.label) {
      const g = el("div", "cx-take-row");
      g.appendChild(el("span", "cx-take-k", "Gameweek"));
      g.appendChild(el("span", null, gw.label));
      box.appendChild(g);
      if (gw.reason) box.appendChild(el("div", "cx-pv-why", gw.reason));
    }
    if (!r) {
      box.appendChild(el("div", "sub",
        "The payload carried no `result` block, so what this became is not " +
        "readable from here; open the board to see it."));
      return box;
    }
    if (!r.take) {
      box.appendChild(el("div", "sub", r.reason ||
        "there is no take and the payload gave no reason for its absence"));
      return box;
    }
    const counts = TAKE_BUCKETS
      .map(([k, label]) => [((r.take[k] || []).length), label])
      .filter(([n]) => n > 0)
      .map(([n, label]) => `${n} ${label}`);
    const c = el("div", "cx-take-row");
    c.appendChild(el("span", "cx-take-k", "Calls"));
    c.appendChild(el("span", null,
      pick(counts.length, counts.join(" · "),
           "the analysis returned no calls at all")));
    box.appendChild(c);
    const bullets = (r.take.summary_bullets || []).slice(0, 2);
    for (const b of bullets) box.appendChild(el("div", "cx-take-b", String(b)));
    if (r.take.model)
      box.appendChild(el("div", "cx-pv-why", `read by ${r.take.model}` +
        when(r.n_segments, `, over ${plural(r.n_segments, "transcript segment")}`)));
    return box;
  }

  renderLinkBar();
  return { stop() { for (const job of jobs) stopJob(job); } };
}
