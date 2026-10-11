// The Autopilot page: edit the rules of the autopilot service (config.toml) and see what it does.
import { $, api, button, el, every, field, note, say, section, send, t } from "./common.js";

// What a condition is called and how it is edited (the service knows these and no others).
const CONDITIONS = {
  music: { label: () => t("Music plays"), kind: "flag", start: true },
  microphone: { label: () => t("Microphone in use"), kind: "flag", start: true },
  cpu_over: { label: () => t("CPU load from %"), kind: "load", start: 60 },
  gpu_over: { label: () => t("GPU load from %"), kind: "load", start: 60 },
  apps: { label: () => t("Program in front"), kind: "list", start: [] },
  time: { label: () => t("Time of day"), kind: "time", start: "22:00-06:00" },
};
const LOAD_WINDOW_S = 20; // the service's window for a load when a rule names none

const SETTINGS = [
  ["hold_s", () => t("Hold a new choice for (s)")],
  ["min_s", () => t("Keep a theme at least (s)")],
  ["quiet_after_boot_s", () => t("Ignore loads after start-up for (s)")],
];

let model = null; // what is edited: { enabled, options, rules }
let served = null; // what the server gave last: the themes, the transitions
let problems = new Map(); // rule -> what the server found wrong with it when it was read
let holding = null; // the name of the rule the service follows now
let dirty = false;

const copy = (value) => JSON.parse(JSON.stringify(value));

export async function start(root) {
  document.title = t("Autopilot");
  root.replaceChildren(
    el("h1", { text: t("Autopilot") }),
    el("div", { id: "notes" }),
    el(
      "div",
      { class: "switch-row" },
      el("label", { class: "check" }, el("input", { type: "checkbox", id: "enabled" }), el("b", { text: t("Autopilot on") })),
      el("span", { id: "now" }),
    ),
    el("div", { class: "chips", id: "signals" }),
    section(t("Timing"), el("div", { class: "fields", id: "options" })),
    section(
      t("Rules"),
      el("p", { class: "muted", id: "explain" }),
      el("div", { id: "rules" }),
      el("div", { class: "row actions" }, button(t("Add a rule"), addRule), button(t("Save"), save, { primary: true }), button(t("Revert"), revert), el("span", { id: "dirty" })),
    ),
  );
  $("enabled").addEventListener("change", () => {
    model.enabled = $("enabled").checked;
    markDirty();
  });
  window.addEventListener("beforeunload", (event) => {
    if (dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  load(await api("settings"));
  every(1500, async () => drawStatus(await api("status")));
}

function load(answer) {
  served = answer;
  // a rule that is no table (written by hand) becomes an empty one, which the server then refuses
  const table = (rule) => (rule && typeof rule === "object" && !Array.isArray(rule) ? rule : { name: String(rule), theme: "", when: {} });
  model = { enabled: answer.enabled, options: copy(answer.options), rules: copy(answer.rules).map(table) };
  problems = new Map(model.rules.map((rule, index) => [rule, answer.problems[index]]));
  dirty = false;
  $("enabled").checked = model.enabled;
  $("explain").textContent = t("The first rule whose conditions all hold decides. A new choice must hold for a while, and a theme then stays for a minimum time, so nothing flickers.");
  drawOptions();
  drawRules();
  drawDirty();
  drawStatus(answer);
}

function markDirty() {
  dirty = true;
  drawDirty();
}

function drawDirty() {
  $("dirty").textContent = dirty ? t("unsaved changes") : "";
  $("dirty").className = dirty ? "warn-text" : "muted";
}

// -- what the service does ----------------------------------------------------------------

function drawStatus(answer) {
  const running = answer.state === "running";
  const notes = [];
  if (!answer.background) {
    notes.push(note("warn", t("This editor does not run the panel: what the autopilot is doing shows only in the editor of the running Libre Panel. You can edit and save the rules here all the same.")));
  } else if (answer.state === "failed") {
    notes.push(note("error", t("The autopilot did not start: {reason}", { reason: answer.detail })));
  } else if (!running && model.enabled) {
    notes.push(note("warn", t("The autopilot is not running yet. It starts when you save.")));
  } else if (!model.enabled) {
    notes.push(note("", t("The autopilot is off: the rules do nothing until it is switched on.")));
  }
  $("notes").replaceChildren(...notes);

  const status = answer.status;
  holding = status && !status.resting ? status.rule : null;
  let now = "";
  if (status) {
    if (status.resting) now = t("It rests while a game mode is on; its choice shows again afterwards.");
    else if (status.rule) now = t("Rule “{rule}” shows {theme}.", { rule: status.rule, theme: status.theme });
    else now = t("No rule holds: the theme of config.toml shows.");
    if (status.candidate) now += ` ${t("It switches to {theme} if that holds.", { theme: status.candidate })}`;
  }
  $("now").textContent = now;
  drawSignals(status);
  document.querySelectorAll(".rule").forEach((card) => card.classList.toggle("holds", holding !== null && card.dataset.name === holding));
}

function drawSignals(status) {
  if (!status) {
    $("signals").replaceChildren();
    return;
  }
  const s = status.signals;
  const load = (text, value) => el("span", { class: "chip", text: value == null ? t("{name} load –", { name: text }) : t("{name} load {n} %", { name: text, n: Math.round(value) }) });
  $("signals").replaceChildren(
    el("span", { class: `chip${s.music ? " on" : ""}`, text: s.music ? t("Music is playing") : t("No music") }),
    el("span", { class: `chip${s.microphone ? " on" : ""}`, text: s.microphone ? t("Microphone in use") : t("Microphone off") }),
    el("span", { class: "chip", text: s.app ? t("In front: {app}", { app: s.app }) : t("In front: –") }),
    load(t("CPU"), s.cpu),
    load(t("GPU"), s.gpu),
  );
}

// -- timing -------------------------------------------------------------------------------

function drawOptions() {
  const inputs = SETTINGS.map(([key, label]) => {
    const input = el("input", { type: "number", min: 0, step: "any" });
    input.value = model.options[key];
    input.addEventListener("input", () => {
      if (input.value !== "") model.options[key] = Number(input.value);
      markDirty();
    });
    return field(label(), input);
  });
  const transition = el("select");
  for (const name of served.transitions) transition.append(el("option", { value: name, text: name || t("the transition of config.toml") }));
  transition.value = model.options.transition;
  transition.addEventListener("change", () => {
    model.options.transition = transition.value;
    markDirty();
  });
  $("options").replaceChildren(...inputs, field(t("How the theme changes"), transition));
}

// -- rules --------------------------------------------------------------------------------

function touch(rule) {
  problems.delete(rule); // what was wrong is for the next save to say
  markDirty();
}

function drawRules() {
  const cards = model.rules.map((rule, index) => ruleCard(rule, index));
  $("rules").replaceChildren(...(cards.length ? cards : [note("", t("No rules: the theme of config.toml always shows."))]));
}

function themeSelect(rule) {
  const select = el("select", { "aria-label": t("Theme to show") });
  const known = served.themes.includes(rule.theme);
  for (const id of known || !rule.theme ? served.themes : [...served.themes, rule.theme]) {
    select.append(el("option", { value: id, text: served.themes.includes(id) ? id : t("{id} (not installed)", { id }) }));
  }
  select.value = rule.theme ?? "";
  select.addEventListener("change", () => {
    rule.theme = select.value;
    touch(rule);
    drawRules();
  });
  return select;
}

function ruleCard(rule, index) {
  rule.when = rule.when && typeof rule.when === "object" ? rule.when : {};
  const on = el("input", { type: "checkbox", title: t("The rule is on") });
  on.checked = rule.enabled !== false;
  on.addEventListener("change", () => {
    if (on.checked) delete rule.enabled;
    else rule.enabled = false;
    touch(rule);
  });
  const name = el("input", { type: "text", class: "name", "aria-label": t("Name of the rule") });
  name.value = rule.name ?? "";
  name.addEventListener("input", () => {
    rule.name = name.value;
    touch(rule);
  });
  const move = (by) => () => {
    const to = index + by;
    if (to < 0 || to >= model.rules.length) return;
    [model.rules[index], model.rules[to]] = [model.rules[to], model.rules[index]];
    markDirty();
    drawRules();
  };
  const remove = () => {
    model.rules.splice(index, 1);
    markDirty();
    drawRules();
  };
  const problem = problems.get(rule);
  const card = el(
    "div",
    { class: `rule${problem ? " invalid" : ""}`, "data-name": rule.name ?? "" },
    el(
      "div",
      { class: "rule-head" },
      on,
      name,
      el("span", { class: "muted", text: t("shows") }),
      themeSelect(rule),
      el("span", { class: "spacer" }),
      el("button", { type: "button", class: "small", title: t("Higher: it is looked at sooner"), text: "↑", onclick: move(-1) }),
      el("button", { type: "button", class: "small", title: t("Lower: it is looked at later"), text: "↓", onclick: move(1) }),
      el("button", { type: "button", class: "small danger", title: t("Delete the rule"), text: "✕", onclick: remove }),
    ),
    el("div", { class: "conditions" }, ...Object.keys(rule.when).filter((key) => key !== "for_s").map((key) => condition(rule, key)), loadWindow(rule), conditionAdder(rule)),
    el("div", { class: "rule-head" }, immediate(rule), holdTime(rule)),
    problem ? el("p", { class: "warn-text", text: problem }) : "",
  );
  return card;
}

function immediate(rule) {
  const box = el("input", { type: "checkbox" });
  box.checked = rule.immediate === true;
  box.addEventListener("change", () => {
    if (box.checked) rule.immediate = true;
    else delete rule.immediate;
    touch(rule);
  });
  return el("label", { class: "check small", title: t("The rule may take over before the theme has stayed its minimum time") }, box, el("span", { text: t("at once") }));
}

function holdTime(rule) {
  const input = el("input", { type: "number", min: 0, step: "any", placeholder: t("as above"), "aria-label": t("Hold time of this rule (s)") });
  input.value = rule.hold_s ?? "";
  input.addEventListener("input", () => {
    if (input.value === "") delete rule.hold_s;
    else rule.hold_s = Number(input.value);
    touch(rule);
  });
  return el("label", { class: "inline" }, el("span", { class: "muted", text: t("Hold time (s)") }), input);
}

// A condition: its words and the way to set it, and a button to take it out.
function condition(rule, key) {
  const known = CONDITIONS[key];
  const box = el("span", { class: "condition" });
  if (!known) {
    box.append(`${key}: ${JSON.stringify(rule.when[key])}`);
  } else {
    box.append(known.label());
    const set = (value) => {
      rule.when[key] = value;
      touch(rule);
    };
    if (known.kind === "flag") {
      const input = el("input", { type: "checkbox" });
      input.checked = rule.when[key] === true;
      input.addEventListener("change", () => set(input.checked));
      box.append(input);
    } else if (known.kind === "load") {
      const input = el("input", { type: "number", min: 0, max: 100, step: "any" });
      input.value = rule.when[key];
      input.addEventListener("input", () => input.value !== "" && set(Number(input.value)));
      box.append(input);
    } else if (known.kind === "list") {
      const input = el("input", { type: "text", placeholder: t("program.exe, other.exe") });
      input.value = (rule.when[key] || []).join(", ");
      input.addEventListener("input", () => set(input.value.split(",").map((n) => n.trim()).filter(Boolean)));
      box.append(input);
    } else if (known.kind === "time") {
      const [from, to] = String(rule.when[key]).split("-");
      const first = el("input", { type: "time" });
      const second = el("input", { type: "time" });
      first.value = from ?? "";
      second.value = to ?? "";
      const join = () => set(`${first.value}-${second.value}`);
      first.addEventListener("input", join);
      second.addEventListener("input", join);
      box.append(first, " – ", second);
    }
  }
  box.append(
    el("button", {
      type: "button",
      class: "icon-btn",
      title: t("Remove the condition"),
      text: "×",
      onclick: () => {
        delete rule.when[key];
        if (!("cpu_over" in rule.when || "gpu_over" in rule.when)) delete rule.when.for_s;
        touch(rule);
        drawRules();
      },
    }),
  );
  return box;
}

// How long a load is averaged over: one setting for the rule's load conditions.
function loadWindow(rule) {
  if (!("cpu_over" in rule.when || "gpu_over" in rule.when)) return "";
  const input = el("input", { type: "number", min: 1, step: "any" });
  input.value = rule.when.for_s ?? LOAD_WINDOW_S;
  input.addEventListener("input", () => {
    if (input.value !== "") rule.when.for_s = Number(input.value);
    touch(rule);
  });
  return el("span", { class: "condition" }, t("averaged over (s)"), input);
}

function conditionAdder(rule) {
  const free = Object.keys(CONDITIONS).filter((key) => !(key in rule.when));
  if (!free.length) return "";
  const select = el("select", { "aria-label": t("Add a condition") }, el("option", { value: "", text: t("+ condition") }), ...free.map((key) => el("option", { value: key, text: CONDITIONS[key].label() })));
  select.addEventListener("change", () => {
    if (!select.value) return;
    rule.when[select.value] = copy(CONDITIONS[select.value].start);
    touch(rule);
    drawRules();
  });
  return select;
}

function addRule() {
  let name = t("New rule");
  for (let n = 2; model.rules.some((rule) => rule.name === name); n += 1) name = t("New rule {n}", { n });
  // switched off, so that it does nothing until it is set up
  model.rules.push({ name, theme: served.themes[0] ?? "", enabled: false, when: { music: true } });
  markDirty();
  drawRules();
}

async function save() {
  const answer = await send("settings", { enabled: model.enabled, options: model.options, rules: model.rules });
  if (!answer) return;
  load(answer);
  say(t("Saved to config.toml. The autopilot restarts by itself."));
}

async function revert() {
  if (dirty && !confirm(t("Discard the changes?"))) return;
  load(await api("settings"));
}
