// Settings → Game mode and more → AI activity, below the service: whether Claude Code and Codex are there
// and set up (their hooks, and Claude Code's status line), with Set up and Remove. Setting up
// shows first what Libre Panel adds to their settings file (/api/ai/setup, agents.py), keeps a
// copy of the file, and takes out again only what it added. Claude Code's cloud sessions come
// by a relay (agents_cloud.py): their row gives the cloud environment's setup script.

import { get, post } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { saveSettings } from "../../core/settings.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { Toggle } from "../../ui/controls.js";
import { openDialog } from "../../ui/dialog.js";
import { toast, toastError } from "../../ui/toast.js";
import { SettingRow, Status, copyText, usePolling } from "./parts.js";

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
  return html`<${SettingRow} stacked class="agents-row" data-agent=${agent} title=${NAMES[agent]}
      hint=${agent === "claude"
        ? t("Its hooks say when it works and when it waits for you; its status line brings the plan's usage while a session runs.")
        : t("Its hooks say when it works and when it waits for you.")}>
    <span class="settings-row-status">${status}</span>
    ${state.hooks && state.heard == null && !moved
      ? html`<span class="settings-row-hint" data-agents-waiting>${t("It reports from its next session on this PC: in a terminal, an editor or the desktop app. Sessions in the cloud come by their own row below.")}</span>`
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

/** What to do in the cloud environment, with its setup script; ``first``: set up on Done. */
function cloudSteps(plan, first) {
  const title = t("Claude Code in the cloud");
  return openDialog(
    (close) => html`
      <h2 class="dialog-title">${title}</h2>
      <ol class="dialog-text agents-cloud-steps">
        <li>${t("On claude.ai/code, open the settings of the environment your sessions run in.")}</li>
        <li>${t("Network access: Custom, with the default list, and {domain} added.", { domain: plan.domain })}</li>
        <li>${t("Setup script: add the script below.")}</li>
        <li>${t("Start a session in it: it reports from then on.")}</li>
      </ol>
      <pre class="command agents-preview" tabindex="0" data-agents-cloud-script>${plan.script}</pre>
      <p class="dialog-text muted">
        ${t("The relay at {domain} passes on whether a session works, waits for you or is done, and what it is called where that is on; never a prompt or a file. Its address is a secret: whoever knows it can read along. ntfy.sh passes some 250 messages a day.", { domain: plan.domain })}
      </p>
      <div class="dialog-actions">
        <button type="button" class="btn" onClick=${() => copyText(plan.script)}>${t("Copy the script")}</button>
        ${first
          ? html`<button type="button" class="btn" onClick=${() => close(false)}>${t("Cancel")}</button>
              <button type="button" class="btn btn-primary" autofocus onClick=${() => close(true)}>${t("Set up")}</button>`
          : html`<button type="button" class="btn btn-primary" autofocus onClick=${() => close(true)}>${t("Close")}</button>`}
      </div>
    `,
    { label: title, wide: true },
  ).then(Boolean);
}

function CloudRow({ cloud, reload }) {
  const show = async (first) => {
    try {
      const plan = await post("/api/ai/setup", { agent: "cloud", action: "preview" });
      if (!(await cloudSteps(plan, first)) || !first) return;
      if (await saveSettings({ "services.agents.cloud": plan.relay })) {
        toast(t("Libre Panel listens to the relay of the cloud sessions."), { kind: "success" });
        reload();
      }
    } catch (error) {
      toastError(error);
    }
  };
  const remove = async () => {
    if (await saveSettings({ "services.agents.cloud": null })) {
      toast(t("Libre Panel no longer listens to the cloud sessions; take the script out of the environment too."), { kind: "success" });
      reload();
    }
  };
  const status = cloud.problem
    ? html`<${Status} tone="error">${cloud.problem}<//>`
    : cloud.relay
      ? cloud.heard != null
        ? html`<${Status} tone="ok">${t("Set up; its last report came {ago} ago", { ago: ago(cloud.heard) })}<//>`
        : html`<${Status} tone="warn">${t("Set up; no report from it yet")}<//>`
      : html`<${Status}>${t("Not set up yet")}<//>`;
  return html`<${SettingRow} stacked class="agents-row" data-agent="cloud" title=${t("Claude Code in the cloud")}
      hint=${t("Its sessions on claude.ai/code cannot reach this PC: a hook in the cloud environment says by a relay when they work and when they wait for you.")}>
    <span class="settings-row-status">${status}</span>
    <div class="command-row">
      ${cloud.relay
        ? html`<button type="button" class="btn" onClick=${() => show(false)}>${t("Show the setup script")}</button>
            <button type="button" class="btn" onClick=${remove}>${t("Remove")}</button>`
        : html`<button type="button" class="btn btn-primary" onClick=${() => show(true)}>${t("Set up")}</button>`}
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
    ${info.cloud ? html`<${CloudRow} cloud=${info.cloud} reload=${reload} />` : null}
  </div>`;
}
