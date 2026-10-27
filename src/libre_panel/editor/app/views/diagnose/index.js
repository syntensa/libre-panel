// Diagnose (#/live/diagnose), the third part of Live: finding out what keeps the panel from
// working, and mending it. At the top what needs attention (Settings → Overview's list,
// settings/health.js) with "Check and fix", which runs every fix marked safe there (they change
// only Libre Panel's own state and ask for no rights), and "Restart the panel", which lets the
// panel go and connects it again (no command restarts the panel itself). Then the panel test: the
// doctor (doctor_session.py) shows test pictures on the panel and asks what is seen, with buttons;
// its report goes to the project's form on GitHub with one click, and the person sends it. Then
// the log (/api/logs, logview.py): its last entries, the problems or all, in detail on request, to
// copy or keep, without the names of the person's folders. And what Libre Panel costs the PC
// (/api/load, load_report.py): its processes, the main one's threads and the rounds of the sensor
// service, asked for every two seconds while the card is on screen. Built on the shell: see
// ../../README.md.

import { endpoints } from "../../core/api.js";
import { formatNumber, t } from "../../core/i18n.js";
import { loadPanelInfo, loadServiceStates, loadSettings, loadSourceStates, openFolder, restartPanel } from "../../core/settings.js";
import { useStore } from "../../core/state.js";
import { html, useEffect, useRef, useState } from "../../lib/preact.js";
import { Segmented, TextField, Toggle } from "../../ui/controls.js";
import { Icon } from "../../ui/icons.js";
import { toast, toastError } from "../../ui/toast.js";
import { AtAGlance, HealthCard, useHealth } from "../settings/health.js";
import { copyText } from "../settings/parts.js";

// -- check and fix, restart ----------------------------------------------------------------------------

/** Run every safe fix of what needs attention; what was mended and what could not be. */
async function fixAll(items) {
  const fixed = [];
  const failed = [];
  for (const item of items) {
    const action = item.action;
    if (!action?.safe || !action.run) continue;
    try {
      const ok = await action.run();
      (ok === false ? failed : fixed).push(item.title);
    } catch (error) {
      failed.push(`${item.title}: ${error.message}`);
    }
  }
  await Promise.allSettled([loadPanelInfo(), loadSourceStates(), loadServiceStates(), loadSettings()]);
  return { fixed, failed };
}

function Mend() {
  const health = useHealth();
  const app = useStore((s) => s.app);
  const [busy, setBusy] = useState("");
  const [done, setDone] = useState(null);
  const running = Boolean(app.available && !app.quit && !app.gone);
  const testing = Boolean(app.panel?.testing);
  const check = async () => {
    setBusy("fix");
    try {
      setDone(await fixAll(health.items));
    } finally {
      setBusy("");
    }
  };
  const restart = async () => {
    setBusy("restart");
    try {
      const ok = await restartPanel();
      toast(ok ? t("Libre Panel connects to the panel again.") : t("The panel could not be restarted."), { kind: ok ? "success" : "warn" });
    } catch (error) {
      toastError(error);
    } finally {
      setBusy("");
    }
  };
  const left = health.items.filter((item) => item.tone !== "info" && !item.action?.safe).length;
  return html`<section class="settings-card diagnose-mend" aria-labelledby="diagnose-mend-title">
    <div class="settings-card-head split">
      <div>
        <h2 id="diagnose-mend-title">${t("Check and fix")}</h2>
        <p>${t("One click checks everything and mends what Libre Panel can mend by itself: it connects the panel again, starts a service that stopped, takes the panel that is plugged in. What needs your rights or another program stays a click of its own.")}</p>
      </div>
    </div>
    <div class="diagnose-actions">
      <button type="button" class="btn btn-primary" data-diagnose="fix" disabled=${Boolean(busy) || !health.ready || testing} onClick=${check}>
        <${Icon} name="check" size=${15} /> ${busy === "fix" ? t("Checking …") : t("Check and fix")}
      </button>
      <button type="button" class="btn" data-diagnose="restart" disabled=${Boolean(busy) || !running || testing} onClick=${restart}
        title=${t("Libre Panel lets the panel go and connects it again, and sends it a whole picture.")}>
        <${Icon} name="refresh" size=${15} /> ${busy === "restart" ? t("Restarting …") : t("Restart the panel")}
      </button>
    </div>
    ${done
      ? html`<div class="diagnose-done" role="status">
          ${done.fixed.length
            ? html`<p><strong>${t("Mended:")}</strong> ${done.fixed.join(" · ")}</p>`
            : html`<p>${t("Nothing that Libre Panel could mend by itself.")}</p>`}
          ${done.failed.length ? html`<p class="diagnose-failed"><strong>${t("Not mended:")}</strong> ${done.failed.join(" · ")}</p>` : null}
          <p class="muted">${left ? t("{n} more need you: below, each with its next step.", { n: left }) : t("Nothing else needs you.")}</p>
        </div>`
      : null}
  </section>`;
}

// -- the panel test ------------------------------------------------------------------------------------

const EDGES = {
  top: () => t("At the top edge: the smallest number whose yellow bar you can see?"),
  bottom: () => t("At the bottom edge: the smallest number whose yellow bar you can see?"),
  left: () => t("At the left edge: the smallest number whose yellow bar you can see?"),
  right: () => t("At the right edge: the smallest number whose yellow bar you can see?"),
};

const CARD = () =>
  t("Is the white frame visible on all four edges, is UP at the top edge, is the text readable (not mirrored), and are RED, GREEN and BLUE the right colours?");

/** What a question of the doctor says, in the app's language (its own English words else). */
function questionText(question) {
  const { key } = question;
  if (key === "card-landscape") return [t("The panel shows a test picture, lying (landscape)."), CARD()];
  if (key === "card-portrait") return [t("The panel shows a test picture, standing (portrait)."), CARD()];
  if (key === "card-black") return [t("Did the panel stay black, without anything of the test picture?")];
  if (key === "card-jpeg") return [t("The panel shows the test picture once more, as the other usual kind of JPEG."), t("Do you see it now?")];
  if (key === "brightness-watch") return [t("Now the panel goes dark, then bright. Watch it and click Go on.")];
  if (key === "brightness") return [t("Did the panel go dark and then bright?")];
  if (key === "size") return [t("Its USB ids fit several panels. Which one is it?")];
  if (key === "gadget") return [t("A device has the USB ids of a Turing panel (rev. C), which other USB devices have too. Is it one of these panels?")];
  if (key.startsWith("ruler-") && EDGES[key.slice(6)]) {
    return [t("The panel shows yellow bars along its edges, each with its number: how many pixels deep it is. The frame of the panel may hide the thinnest."), EDGES[key.slice(6)]()];
  }
  return [question.text];
}

// the doctor's steps (doctor.py, in English for the report) as the app names them
const STEPS = {
  environment: () => t("Libre Panel and the system"),
  "temperature and power readings": () => t("Temperature and power readings"),
  "find and open a USB panel (VID 1CBE)": () => t("Find the USB panel"),
  "find and open a USB panel": () => t("Find the USB panel"),
  "find and open a USB serial panel (virtual COM port)": () => t("Find the USB serial panel"),
  "open a USB serial panel (virtual COM port)": () => t("Find the USB serial panel"),
  "find a panel": () => t("Find a panel"),
  "USB serial panel found": () => t("USB serial panel found"),
  "handshake (command 10)": () => t("Handshake"),
  "panel size": () => t("Panel size"),
  "test card, landscape": () => t("Test picture, lying"),
  "test card, portrait": () => t("Test picture, standing"),
  "hidden edges (ruler)": () => t("Edges the frame hides"),
  "brightness (command 14)": () => t("Brightness"),
  brightness: () => t("Brightness"),
  "close and reconnect": () => t("Close and connect again"),
  "JPEG layout": () => t("Kind of JPEG"),
  "video mode": () => t("Smooth motion"),
  "panel stopped answering": () => t("The panel stopped answering"),
};

function stepName(name) {
  const speed = /^speed \((\d+) full frames\)$/.exec(name);
  if (speed) return t("Speed ({n} whole pictures)", { n: speed[1] });
  return STEPS[name]?.() ?? name;
}

const MARKS = { ok: "check", fail: "warning", skip: "minus", info: "info" };

function Steps({ steps }) {
  if (!steps?.length) return null;
  return html`<ol class="doctor-steps" aria-label=${t("Steps")}>
    ${steps.map(
      (step, index) => html`<li key=${index} class=${`doctor-step ${step.result}`}>
        <span class="doctor-mark" aria-hidden="true"><${Icon} name=${MARKS[step.result] || "info"} size=${14} /></span>
        <span class="doctor-name">${stepName(step.name)}</span>
        ${step.detail ? html`<span class="doctor-detail">${step.detail}</span>` : null}
      </li>`,
    )}
  </ol>`;
}

/** The question the doctor asks, with the buttons that answer it. */
function Question({ question, onAnswer }) {
  const [no, setNo] = useState(false);
  const [note, setNote] = useState("");
  const [number, setNumber] = useState(""); // as typed: whole numbers only
  const lines = questionText(question);
  const head = html`<div class="doctor-question-text">${lines.map((line, index) => html`<p key=${index} class=${index === lines.length - 1 ? "doctor-ask" : ""}>${line}</p>`)}</div>`;
  if (question.kind === "confirm") {
    return html`<div class="doctor-question" data-question=${question.key}>
      ${head}
      ${no
        ? html`<label class="doctor-note">
              <span>${t("What do you see instead?")}</span>
              <${TextField} value=${note} onChange=${setNote} label=${t("What do you see instead?")} multiline rows=${2} />
            </label>
            <div class="diagnose-actions">
              <button type="button" class="btn btn-primary" onClick=${() => onAnswer({ yes: false, note })}>${t("Go on")}</button>
              <button type="button" class="link-btn" onClick=${() => setNo(false)}>${t("Back")}</button>
            </div>`
        : html`<div class="diagnose-actions">
            <button type="button" class="btn btn-primary" data-answer="yes" onClick=${() => onAnswer({ yes: true })}>${t("Yes, all right")}</button>
            <button type="button" class="btn" data-answer="no" onClick=${() => setNo(true)}>${t("No")}</button>
          </div>`}
    </div>`;
  }
  if (question.kind === "number") {
    return html`<div class="doctor-question" data-question=${question.key}>
      ${head}
      <div class="diagnose-actions">
        <input class="input mono doctor-number" type="text" inputmode="numeric" aria-label=${t("Number")} value=${number}
          onInput=${(event) => setNumber(event.currentTarget.value.replace(/[^0-9]/g, "").slice(0, 3))}
          onKeyDown=${(event) => event.key === "Enter" && number && onAnswer({ number: Number(number) })} />
        <button type="button" class="btn btn-primary" data-answer="number" disabled=${!number} onClick=${() => onAnswer({ number: Number(number) })}>${t("Go on")}</button>
        <button type="button" class="btn" data-answer="none" onClick=${() => onAnswer({})}>${t("I cannot tell")}</button>
      </div>
    </div>`;
  }
  if (question.kind === "choose") {
    return html`<div class="doctor-question" data-question=${question.key}>
      ${head}
      <div class="diagnose-actions">
        ${(question.options || []).map((label, index) => html`<button type="button" class="btn" key=${index} onClick=${() => onAnswer({ index })}>${label}</button>`)}
        ${question.key === "gadget" ? html`<button type="button" class="btn" data-answer="none" onClick=${() => onAnswer({})}>${t("None of them")}</button>` : null}
      </div>
    </div>`;
  }
  return html`<div class="doctor-question" data-question=${question.key}>
    ${head}
    <div class="diagnose-actions"><button type="button" class="btn btn-primary" data-answer="go" onClick=${() => onAnswer({})}>${t("Go on")}</button></div>
  </div>`;
}

function useTest() {
  const [status, setStatus] = useState(null);
  const load = async () => {
    try {
      setStatus(await endpoints.doctor());
    } catch {
      // the next look tells
    }
  };
  useEffect(() => {
    load();
  }, []);
  const busy = status && ["running", "asking"].includes(status.state);
  useEffect(() => {
    if (!busy) return undefined;
    const timer = setInterval(load, 600);
    return () => clearInterval(timer);
  }, [busy]);
  const act = async (action, answer) => {
    try {
      setStatus(await endpoints.doctorAction(action, answer));
    } catch (error) {
      toastError(error);
      load();
    }
  };
  return { status, act };
}

function Result({ report, again }) {
  const title = report.passed ? t("The panel works as it should") : report.failed ? t("Something is not right") : t("No problems found, but not every check was answered");
  const text = report.passed
    ? t("Every check passed. Tell the project: the next person with this panel then knows that it works.")
    : t("The steps say what. The report helps the project mend it: the form on GitHub opens with it filled in.");
  return html`<div class=${`doctor-result ${report.passed ? "ok" : report.failed ? "fail" : ""}`} role="status">
    <div class="doctor-result-head">
      <span class="doctor-badge" aria-hidden="true"><${Icon} name=${report.passed ? "check" : "warning"} size=${18} strokeWidth=${2.4} /></span>
      <div>
        <strong>${title}</strong>
        ${report.model ? html`<span class="muted">${report.model.label}</span>` : null}
      </div>
    </div>
    <p>${text}</p>
    <div class="diagnose-actions">
      <a class="btn btn-primary" data-diagnose="report" href=${report.form} target="_blank" rel="noopener noreferrer">
        ${report.passed ? t("Report the panel as working") : t("Report the problem")}<${Icon} name="external" size=${14} />
      </a>
      <button type="button" class="btn" onClick=${() => copyText(report.text)}>${t("Copy the report")}</button>
      <button type="button" class="link-btn" onClick=${again}>${t("Test again")}</button>
    </div>
    <p class="muted">${t("GitHub opens in your browser, and you send it yourself (with a GitHub account). The report has no serial numbers and no names of yours.")}</p>
    <details class="doctor-report"><summary>${t("The report")}</summary><pre>${report.text}</pre></details>
  </div>`;
}

function PanelTest() {
  const { status, act } = useTest();
  const state = status?.state || "idle";
  const running = state === "running" || state === "asking";
  const card = useRef(null);
  // a question or the result where the eye is: the card may be below the list of what needs you
  const shown = state === "asking" ? status.question?.id : state;
  useEffect(() => {
    if (state === "asking" || state === "done") card.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [shown]);
  let body;
  if (state === "asking" && status.question) {
    body = html`<${Question} key=${status.question.id} question=${status.question}
      onAnswer=${(answer) => act("answer", { ...answer, id: status.question.id })} />`;
  } else if (state === "running") {
    body = html`<p class="doctor-busy" role="status">${t("The test runs: watch the panel …")}</p>`;
  } else if (state === "done" && status.report) {
    body = html`<${Result} report=${status.report} again=${() => act("start")} />`;
  } else {
    const said = state === "cancelled" ? t("The test was ended. The panel shows its theme again.") : state === "failed" ? t("The test stopped: {reason}", { reason: status.error }) : "";
    body = html`${said ? html`<p class=${state === "failed" ? "diagnose-failed" : "muted"} role="status">${said}</p>` : null}
      <div class="diagnose-actions">
        <button type="button" class="btn btn-primary" data-diagnose="test" onClick=${() => act("start")}>
          <${Icon} name="play" size=${15} /> ${state === "idle" ? t("Start the test") : t("Test again")}
        </button>
      </div>`;
  }
  return html`<section class="settings-card diagnose-test" aria-labelledby="diagnose-test-title" ref=${card}>
    <div class="settings-card-head split">
      <div>
        <h2 id="diagnose-test-title">${t("Panel test")}</h2>
        <p>${t("Libre Panel shows test pictures on the panel and asks what you see: the frame, the colours, the edges, the brightness. Meanwhile the panel shows no theme; afterwards it does again. It takes about two minutes.")}</p>
      </div>
      ${running ? html`<button type="button" class="link-btn" data-diagnose="cancel" onClick=${() => act("cancel")}>${t("End the test")}</button>` : null}
    </div>
    <div class="diagnose-body">
      ${body}
      ${state !== "idle" && state !== "done" ? html`<${Steps} steps=${status?.steps} />` : null}
      ${state === "done" ? html`<${Steps} steps=${status?.steps} />` : null}
    </div>
  </section>`;
}

// -- the log -------------------------------------------------------------------------------------------

const LEVELS = () => [
  { value: "WARNING", label: t("Problems") },
  { value: "INFO", label: t("Everything") },
  { value: "DEBUG", label: t("With details") },
];

function LogCard() {
  const [level, setLevel] = useState("WARNING");
  const [data, setData] = useState(null);
  const box = useRef(null);
  const atEnd = useRef(true);
  const load = async () => {
    try {
      setData(await endpoints.logs(level));
    } catch (error) {
      setData({ problem: error.message, entries: [] });
    }
  };
  useEffect(() => {
    atEnd.current = true;
    load();
    const timer = setInterval(() => !document.hidden && load(), 4000);
    return () => clearInterval(timer);
  }, [level]);
  useEffect(() => {
    if (box.current && atEnd.current) box.current.scrollTop = box.current.scrollHeight;
  }, [data]);
  const entries = data?.entries || [];
  const asText = () => entries.map((e) => `${e.time} ${e.level} ${e.logger}: ${e.message}`).join("\n");
  const detailed = async (on) => {
    try {
      await endpoints.setDetailedLog(on);
      load();
    } catch (error) {
      toastError(error);
    }
  };
  return html`<section class="settings-card diagnose-log" aria-labelledby="diagnose-log-title">
    <div class="settings-card-head split">
      <div>
        <h2 id="diagnose-log-title">${t("Log")}</h2>
        <p>${t("What Libre Panel did and what went wrong. Copied and saved without the names of your folders.")}</p>
      </div>
    </div>
    <div class="diagnose-body">
      <div class="diagnose-log-bar">
        <${Segmented} label=${t("Which entries")} value=${level} onChange=${setLevel} options=${LEVELS()} />
        <label class="diagnose-detail">
          <${Toggle} label=${t("Write in detail")} checked=${Boolean(data?.verbose)} onChange=${detailed} />
          <span>${t("Write in detail")}</span>
        </label>
      </div>
      <p class="muted diagnose-hint">${t("In detail, every step goes into the log until Libre Panel starts again: switch it on before you try what goes wrong.")}</p>
      ${data?.problem ? html`<p class="diagnose-failed">${data.problem}</p>` : null}
      <div class="diagnose-log-box" role="log" tabindex="0" aria-label=${t("Log")} ref=${box}
        onScroll=${(event) => {
          const node = event.currentTarget;
          atEnd.current = node.scrollTop + node.clientHeight >= node.scrollHeight - 6;
        }}>
        ${data && !entries.length ? html`<p class="muted">${level === "WARNING" ? t("No problems in the log.") : t("The log is empty.")}</p>` : null}
        ${entries.map(
          (entry, index) => html`<div key=${index} class=${`log-entry ${entry.level.toLowerCase()}`}>
            <span class="log-time">${entry.time}</span>
            <span class="log-level">${entry.level}</span>
            <span class="log-message">${entry.message}</span>
          </div>`,
        )}
      </div>
      <div class="diagnose-actions">
        <button type="button" class="btn" disabled=${!entries.length} onClick=${() => copyText(asText())}>${t("Copy")}</button>
        <a class="btn" href=${endpoints.logsFile()} download="libre-panel-log.txt"><${Icon} name="download" size=${15} /> ${t("Save the whole log")}</a>
        <button type="button" class="link-btn" onClick=${() => openFolder("logs")}>${t("Open its folder")}</button>
      </div>
    </div>
  </section>`;
}

// -- the load ------------------------------------------------------------------------------------------

/** Whether the element of ``ref`` is on screen (and the page shown): the load is asked for only then. */
function useOnScreen(ref) {
  const [on, setOn] = useState(false);
  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    if (typeof IntersectionObserver === "undefined") {
      setOn(true);
      return undefined;
    }
    const watch = new IntersectionObserver((entries) => setOn(entries.some((entry) => entry.isIntersecting)));
    watch.observe(node);
    return () => watch.disconnect();
  }, []);
  return on;
}

/** The load, every two seconds while ``on`` (sooner while the first measurement is not in). */
function useLoad(on) {
  const [data, setData] = useState(null);
  useEffect(() => {
    if (!on) return undefined;
    let alive = true;
    let timer = 0;
    const tick = async () => {
      let next = 2000;
      if (!document.hidden) {
        try {
          const answer = await endpoints.load();
          if (!alive) return;
          setData(answer);
          if (!answer.ready) next = 700;
        } catch (error) {
          if (alive) setData((now) => ({ ...(now || {}), problem: error.message }));
        }
      }
      if (alive) timer = setTimeout(tick, next);
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [on]);
  return data;
}

const percent = (value) => (value === null || value === undefined ? "–" : `${formatNumber(value, 1, { fixed: true })} %`);
const megabytes = (bytes) => (bytes === null || bytes === undefined ? "–" : `${formatNumber(bytes / 1048576, 0)} MB`);
const millis = (ms) => `${formatNumber(ms, 1, { fixed: true })} ms`;

/** A process's row, in the app's language. */
function processName(row) {
  const many = row.count > 1 ? ` ×${row.count}` : "";
  const names = {
    main: () => t("Main process"),
    panel: () => (row.panel ? t("Panel {n}", { n: row.panel }) : t("A panel's process")),
    ffmpeg: () => (row.panel ? t("Video of panel {n} (ffmpeg)", { n: row.panel }) : t("Video (ffmpeg)")),
    presentmon: () => t("Frame rates (PresentMon)"),
    window: () => t("Window"),
    webview: () => t("Browser engine of the window"),
    service: () => t("Sensor service"),
  };
  return (names[row.role]?.() ?? (row.name || t("Other"))) + many;
}

/** A round of LibreHardwareMonitor: what it took, and its slowest parts. */
function Round({ title, done }) {
  const slowest = done.parts.slice(0, 3);
  return html`<div class="load-round">
    <div class="load-round-head"><span>${title}</span><strong class="mono">${millis(done.ms)}</strong></div>
    ${slowest.length
      ? html`<p class="muted">${t("Slowest:")} ${slowest.map((part, index) => html`<span key=${index} class="load-part" title=${part.kind}>${index ? " · " : ""}${part.name || part.kind} <span class="mono">${millis(part.ms)}</span></span>`)}</p>`
      : html`<p class="muted">${t("Nothing was read: no theme on show needs it.")}</p>`}
  </div>`;
}

function Rounds({ sensors }) {
  if (!sensors) return null;
  const where = sensors.mode === "service"
    ? t("Through the sensor service {version}", { version: sensors.version || "" }).trim()
    : sensors.mode === "inprocess" ? t("Inside Libre Panel") : t("From its web server");
  let body;
  if (sensors.mode === "webserver") body = html`<p class="muted">${t("LibreHardwareMonitor runs as a program of its own: what it costs is not counted here.")}</p>`;
  else if (sensors.error) body = html`<p class="muted">${t("Not read now: {reason}", { reason: sensors.error })}</p>`;
  else if (!sensors.reported) body = html`<p class="muted" data-load="unreported">${t("Not reported by this service version.")}</p>`;
  else if (!sensors.last && !sensors.full) body = html`<p class="muted">${t("No round yet.")}</p>`;
  else {
    body = html`${sensors.last ? html`<${Round} title=${sensors.last.full ? t("Last round, all hardware") : t("Last round (every second)")} done=${sensors.last} />` : null}
      ${sensors.full && !sensors.last?.full ? html`<${Round} title=${t("All hardware (now and then)")} done=${sensors.full} />` : null}`;
  }
  return html`<div class="load-sensors" data-load="sensors">
    <h3>${t("LibreHardwareMonitor's rounds")} <span class="muted">${where}</span></h3>
    ${body}
  </div>`;
}

function LoadCard() {
  const card = useRef(null);
  const on = useOnScreen(card);
  const data = useLoad(on);
  useStore((s) => s.lang);
  const ready = Boolean(data?.ready);
  const threads = ready ? data.threads : [];
  let body;
  if (!ready) {
    body = data?.problem ? html`<p class="diagnose-failed">${data.problem}</p>` : html`<p class="muted" role="status">${t("Measuring …")}</p>`;
  } else {
    body = html`<div class="load-headline">
        <div class="load-figure"><strong data-load="cpu">${percent(data.total.cpu_pc)}</strong><span>${t("of the PC")}</span></div>
        <div class="load-figure"><strong data-load="memory">${megabytes(data.total.private ?? data.total.rss)}</strong><span>${t("memory")}</span></div>
        <p class="muted">${t("{core} of one core · {n} logical processors", { core: percent(data.total.cpu_core), n: data.cpus })}</p>
        ${data.total.private !== undefined ? html`<p class="muted" data-load="working-set">${t("Working set: {size}", { size: megabytes(data.total.rss) })}</p>` : null}
      </div>
      <table class="load-table">
        <thead><tr><th>${t("Process")}</th><th>${t("Of the PC")}</th><th>${t("Of one core")}</th><th>${t("Memory")}</th><th>${t("Working set")}</th></tr></thead>
        <tbody>
          ${data.processes.map((row, index) => html`<tr key=${index} data-role=${row.role}>
            <td>${processName(row)}</td><td class="mono">${percent(row.cpu_pc)}</td><td class="mono">${percent(row.cpu_core)}</td><td class="mono" data-load="private">${megabytes(row.private)}</td><td class="mono" data-load="rss">${megabytes(row.rss)}</td>
          </tr>`)}
        </tbody>
      </table>
      ${data.service?.state === "not running" ? html`<p class="muted">${t("The sensor service does not run.")}</p>` : null}
      ${data.service?.found_by === "program name" ? html`<p class="muted">${t("The sensor service was found by its program's name: the Service Control Manager could not be asked.")}</p>` : null}
      ${threads.length
        ? html`<details class="load-threads">
            <summary>${t("Threads of the main process")}</summary>
            <ol>
              ${threads.map((thread, index) => html`<li key=${index}>
                <span class=${thread.name === "other" ? "" : "mono"}>${thread.name === "other" ? t("Other threads") : thread.name}${thread.count > 1 ? ` ×${thread.count}` : ""}</span>
                <span class="mono">${percent(thread.cpu_core)}</span>
              </li>`)}
            </ol>
            <p class="muted">${t("Of one core. Other threads: those without a name of Libre Panel's, and those that ended meanwhile.")}</p>
          </details>`
        : null}
      <${Rounds} sensors=${data.sensors} />`;
  }
  return html`<section class="settings-card diagnose-load" aria-labelledby="diagnose-load-title" ref=${card}>
    <div class="settings-card-head split">
      <div>
        <h2 id="diagnose-load-title">${t("Load on this PC")}</h2>
        <p>${t("What Libre Panel costs this PC now: each of its processes, the threads of the main one and the sensor service's rounds, measured while this card is on screen.")}</p>
      </div>
    </div>
    <div class="diagnose-body">
      ${body}
      <div class="diagnose-actions">
        <button type="button" class="btn" data-load="copy" disabled=${!ready} onClick=${() => copyText(data.text)}>${t("Copy as text")}</button>
      </div>
      <p class="muted diagnose-hint">${t("Per PC is what the Task Manager shows: the whole PC is 100 %. Of one core, a whole core is 100 %.")} ${t("Memory is each process's private memory, as the Task Manager's Memory column shows it (on its Details tab: Memory (active private working set)); – where Windows does not let it be read. The working set also counts what processes share, once for each.")}${ready ? ` ${t("Measuring took {ms}.", { ms: millis(data.sample_ms) })}` : ""}</p>
    </div>
  </section>`;
}

// -- the view ------------------------------------------------------------------------------------------

export function DiagnoseView({ switcher }) {
  return html`<div class="diagnose">
    ${switcher}
    <div class="diagnose-head">
      <h2>${t("Diagnose")}</h2>
      <p class="muted">${t("Find out what keeps the panel from working, and mend it.")}</p>
    </div>
    <div class="diagnose-grid">
      <div class="diagnose-column">
        <${Mend} />
        <${HealthCard} />
        <${PanelTest} />
      </div>
      <div class="diagnose-column">
        <${AtAGlance} />
        <${LoadCard} />
        <${LogCard} />
      </div>
    </div>
  </div>`;
}
