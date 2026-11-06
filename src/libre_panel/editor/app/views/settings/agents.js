// Settings → Game mode and more → AI activity, below the service: whether Claude Code and Codex are there
// and set up (their hooks, and Claude Code's status line), with Set up and Remove. Setting up
// shows first what Libre Panel adds to their settings file (/api/ai/setup, agents.py), keeps a
// copy of the file, and takes out again only what it added. Only the agents on this PC count.

import { get, post } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { Toggle } from "../../ui/controls.js";
import { openDialog } from "../../ui/dialog.js";
import { toast, toastError } from "../../ui/toast.js";
import { SettingRow, Status, usePolling } from "./parts.js";

const NAMES = { claude: "Claude Code", codex: "Codex" };

/** "12 s", "5 min", "3 h": how long ago, short. */
const ago = (seconds) =>
  seconds < 60
    ? t("{n} s", { n: Math.round(seconds) })
    : seconds < 3600
      ? t("{n} min", { n: Math.round(seconds / 60) })
      : t("{n} h", { n: Math.round(seconds / 3600) });

/** What setting up adds, to be confirmed: the settings file as it will be. */
function preview(agent, plan) {
  return openDialog(
    (close) => html`
      <h2 class="dialog-title">${t("Set up {name}?", { name: NAMES[agent] })}</h2>
      <p class="dialog-text">
        ${t("Libre Panel adds this to {file}. A copy of the file as it is now is kept beside it.", { file: plan.path })}
      </p>
      <pre class="command agents-preview" tabindex="0" data-agents-preview>${plan.after}</pre>
      ${agent === "codex"
        ? html`<p class="dialog-text">${t("Codex asks you once to trust the new hooks.")}</p>`
        : null}
      <div class="dialog-actions">
        <button type="button" class="btn" onClick=${() => close(false)}>${t("Cancel")}</button>
        <button type="button" class="btn btn-primary" autofocus onClick=${() => close(true)}>${t("Set up")}</button>
      </div>
    `,
    { label: t("Set up {name}?", { name: NAMES[agent] }), wide: true },
  ).then(Boolean);
}

/** Where the plan's usage last came from, or why none came: [tone, text]; null where the agent
 * is neither on this PC nor set up. */
function usageText(agent, state) {
  const usage = state.usage;
  if (!usage || !(state.program || state.hooks)) return null;
  const name = NAMES[agent];
  if (usage.source === "status")
    return ["ok", t("The plan's usage came {ago} ago, from its status line.", { ago: ago(usage.ago) })];
  if (usage.source === "background")
    return ["ok", t("The plan's usage came {ago} ago, asked of {name} in the background.", { ago: ago(usage.ago), name })];
  if (usage.reason === "missing")
    return ["warn", t("No plan's usage yet: Libre Panel finds no {name} program on this PC's PATH to ask in the background.", { name })];
  if (usage.reason === "old")
    return ["warn", t("No plan's usage yet: Claude Code {version} is too old to be asked in the background; 2.1.293 or later answers (claude update).", { version: usage.version || "?" })];
  if (usage.reason === "signed_out")
    return ["warn", agent === "codex"
      ? t("No plan's usage yet: Codex reports no limits. Sign in to Codex with ChatGPT.")
      : t("No plan's usage yet: Claude Code gave none in the background. Sign in to it again, or update it.")];
  if (usage.reason === "no_limits")
    return ["warn", t("No plan's usage: the account reports no limits (with an API key there are none).")];
  if (usage.error)
    return ["warn", t("No plan's usage yet: asking {name} in the background did not work ({error}).", { name, error: usage.error })];
  if (!usage.asked)
    return ["off", t("No plan's usage yet: asking {name} in the background is off (the service's settings above).", { name })];
  return ["off", t("No plan's usage yet: {name} is asked every few minutes while a theme shows the AI activity.", { name })];
}

function AgentRow({ agent, info, reload }) {
  const [line, setLine] = useState(true);
  const state = info[agent];
  const setUp = async () => {
    try {
      const plan = await post("/api/ai/setup", { agent, action: "preview", status_line: line });
      if (!(await preview(agent, plan))) return;
      await post("/api/ai/setup", { agent, action: "install", status_line: line });
      toast(t("{name} is set up. It reports from its next session on.", { name: NAMES[agent] }), { kind: "success" });
      reload();
    } catch (error) {
      toastError(error);
    }
  };
  const remove = async () => {
    try {
      await post("/api/ai/setup", { agent, action: "remove" });
      toast(t("Libre Panel's hooks are out of {name}'s settings.", { name: NAMES[agent] }), { kind: "success" });
      reload();
    } catch (error) {
      toastError(error);
    }
  };
  // set up for the address Libre Panel had then: it listens on another now (its port was taken)
  const moved = Boolean(state.hooks && state.port && info.port && state.port !== info.port);
  const status = state.problem
    ? html`<${Status} tone="error">${state.problem}<//>`
    : moved
      ? html`<${Status} tone="warn">${t("Set up for another address: set it up again")}<//>`
      : state.hooks
        ? state.heard != null
          ? html`<${Status} tone="ok">${t("Set up; its last report came {ago} ago", { ago: ago(state.heard) })}<//>`
          : html`<${Status} tone="warn">${t("Set up; no report from it yet")}<//>`
        : html`<${Status}>${state.program ? t("Not set up yet") : t("Not found on this PC")}<//>`;
  const usage = usageText(agent, state);
  // Claude Code's status line brings the usage only where it runs: in a terminal, as its own
  const lineHint = agent === "claude" && state.hooks && state.usage?.source !== "status"
    ? state.other_status_line
      ? t("Its status line is another program's: the plan's usage comes only by asking Claude Code in the background.")
      : state.status_line
        ? t("Its status line sends the plan's usage only while Claude Code runs in a terminal; the desktop app and editors show none.")
        : t("Set up without its status line: the plan's usage comes only by asking Claude Code in the background.")
    : null;
  return html`<${SettingRow} stacked class="agents-row" data-agent=${agent} title=${NAMES[agent]}
      hint=${agent === "claude"
        ? t("Its hooks say when it works and when it waits for you; its status line brings the plan's usage while a session runs.")
        : t("Its hooks say when it works and when it waits for you.")}>
    <span class="settings-row-status">${status}</span>
    ${usage
      ? html`<span class="agents-usage" data-agents-usage=${state.usage.source || state.usage.reason || "none"}>
          <${Status} tone=${usage[0]}>${usage[1]}<//>
        </span>`
      : null}
    ${lineHint ? html`<span class="settings-row-hint" data-agents-line-hint>${lineHint}</span>` : null}
    ${state.hooks && state.heard == null && !moved
      ? html`<span class="settings-row-hint" data-agents-waiting>${t("It reports from its next session on this PC: in a terminal, an editor or the desktop app.")}</span>`
      : null}
    ${agent === "claude" && !state.hooks
      ? state.other_status_line
        ? html`<span class="settings-row-hint">${t("Claude Code has a status line of its own: Libre Panel leaves it as it is.")}</span>`
        : html`<div class="agents-line">
            <${Toggle} id="agents-status-line" label=${t("Also its status line, with the plan's usage")} checked=${line} onChange=${setLine} />
            <label for="agents-status-line">${t("Also its status line, with the plan's usage")}</label>
          </div>`
      : null}
    <div class="command-row">
      ${moved ? html`<button type="button" class="btn btn-primary" onClick=${setUp}>${t("Set up again")}</button>` : null}
      ${state.hooks
        ? html`<button type="button" class="btn" onClick=${remove}>${t("Remove")}</button>`
        : html`<button type="button" class="btn btn-primary" disabled=${Boolean(state.problem)} onClick=${setUp}>${t("Set up")}</button>`}
    </div>
  <//>`;
}

export function AgentsSetup() {
  const [info, setInfo] = useState(null);
  const reload = () => get("/api/ai/setup").then(setInfo).catch(toastError);
  useEffect(() => {
    reload();
  }, []);
  usePolling(() => get("/api/ai/setup").then(setInfo).catch(() => {}), 5000); // its last report
  if (!info) return null;
  return html`<div class="agents-setup" data-agents-setup>
    ${["claude", "codex"].map((agent) => html`<${AgentRow} key=${agent} agent=${agent} info=${info} reload=${reload} />`)}
  </div>`;
}
