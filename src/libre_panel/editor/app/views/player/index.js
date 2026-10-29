// Player (#/player): videos, animated pictures and pictures of the Player's folder, played on a
// panel instead of its theme (player.py, /api/player). The library as tiles, each of which plays
// the library from it; files added by choosing or dropping them (each written into the folder
// as it comes, /api/player/media); the panel's own picture while it plays, and the controls:
// the one before, play or pause, the next, stop, where it stands (a slider that seeks), on
// which panel, how it fills it, what comes after the last, in another order, and how long a
// picture shows. While nothing plays the choices are this window's preferences; while it plays
// they change the playback. Below them: the panel as a monitor (monitor.js).

import { withToken } from "/static/token.js";
import { endpoints } from "../../core/api.js";
import { registerCommands } from "../../core/commands.js";
import { useLivePicture } from "../../core/console.js";
import { t } from "../../core/i18n.js";
import { setPref, store, useStore } from "../../core/state.js";
import { html, useEffect, useRef, useState } from "../../lib/preact.js";
import { navigate, registerView } from "../../shell/views.js";
import { IconButton, Segmented, Toggle } from "../../ui/controls.js";
import { confirmDialog } from "../../ui/dialog.js";
import { Icon } from "../../ui/icons.js";
import { toast, toastError } from "../../ui/toast.js";
import { MonitorSection } from "./monitor.js";

const SUFFIXES = [".mp4", ".webm", ".mov", ".m4v", ".mkv", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"];
const ACCEPT = `video/*,image/*,${SUFFIXES.join(",")}`;
const PICTURE_TIMES = [5, 10, 20, 30, 60];
const POLL_MS = 1000;

// -- talking to the server -------------------------------------------------------------------

/** What /api/player says, kept in the store (`player`) with the moment it came. */
async function load() {
  try {
    store.set({ player: { ...(await endpoints.player()), at: performance.now() } });
  } catch (error) {
    if (!error.quiet) store.set({ player: { error: error.message, at: performance.now() } });
  }
}

async function act(action, more = {}) {
  try {
    store.set({ player: { ...(await endpoints.playerAction({ action, ...more })), at: performance.now() } });
    return true;
  } catch (error) {
    toastError(error);
    return false;
  }
}

/** The choices for the next playback, kept in this window. */
const choices = (prefs) => ({
  panels: Array.isArray(prefs.playerPanels) && prefs.playerPanels.length ? prefs.playerPanels : [1],
  fit: prefs.playerFit || "cover",
  loop: prefs.playerLoop || "all",
  shuffle: Boolean(prefs.playerShuffle),
  picture_s: prefs.playerPictureS || 10,
});

/** Play the library from ``start`` (its first where none is given). */
export function playFrom(start) {
  const { player, prefs } = store.get();
  const names = (player?.library || []).map((item) => item.name);
  if (!names.length) return Promise.resolve(false);
  return act("play", { names, start: start || names[0], ...choices(prefs) });
}

/** A name the folder takes: what Windows and the server refuse in a file's name made a dash. */
function nameFor(file) {
  const dot = file.name.lastIndexOf(".");
  const suffix = dot > 0 ? file.name.slice(dot).toLowerCase() : "";
  let stem = (dot > 0 ? file.name.slice(0, dot) : file.name).replace(/[\\/:*?"<>|\u0000-\u001f]+/g, "-");
  stem = stem.trim().replace(/^\.+/, "").replace(/\.+$/, "").slice(0, 100) || "file";
  return SUFFIXES.includes(suffix) ? `${stem}${suffix}` : null;
}

/** Each file into the library, one after the other, with how far it is. */
async function upload(files, onProgress) {
  let added = 0;
  for (const file of files) {
    const name = nameFor(file);
    if (!name) {
      toast(t("{name} is no video or picture the Player plays.", { name: file.name }), { kind: "warn" });
      continue;
    }
    try {
      const answer = await endpoints.playerUpload(name, file, (share) => onProgress({ name, share }));
      store.set({ player: { ...answer, at: performance.now() } });
      added += 1;
    } catch (error) {
      toastError(error);
    }
  }
  onProgress(null);
  if (added) toast(added === 1 ? t("Added to the Player.") : t("{count} added to the Player.", { count: added }), { kind: "success" });
}

// -- the parts -------------------------------------------------------------------------------

const clock = (seconds) => {
  const whole = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = String(whole % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
};

const kindLabel = (kind) => ({ video: t("Video"), animation: t("Animated picture"), picture: t("Picture") })[kind] || kind;

function Tile({ item, playing, onRemove }) {
  const src = withToken(`/api/player/picture?name=${encodeURIComponent(item.name)}&w=320&v=${item.bytes}`);
  const now = playing?.name === item.name;
  return html`<li class=${`player-tile${now ? " is-playing" : ""}`}>
    <button type="button" class="player-tile-play" onClick=${() => playFrom(item.name)}
      title=${t("Play from here")} aria-label=${t("Play {name}", { name: item.name })}>
      <span class="player-thumb">
        <img src=${src} alt="" loading="lazy" />
        <span class="player-thumb-play"><${Icon} name=${now ? "pulse" : "play"} size=${20} /></span>
      </span>
      <span class="player-tile-name" title=${item.name}>${item.name}</span>
      <span class="player-tile-meta">
        <${Icon} name=${item.kind === "video" ? "film" : "image"} size=${12} />
        ${kindLabel(item.kind)}${item.kind !== "picture" && item.length ? ` · ${clock(item.length)}` : ""}
      </span>
    </button>
    <${IconButton} icon="trash" size=${14} class="player-tile-remove" label=${t("Remove {name}", { name: item.name })}
      onClick=${() => onRemove(item)} />
  </li>`;
}

/** Where the item on show stands, moving on between the server's answers. */
function usePosition(playing, at) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!playing || playing.paused) return undefined;
    const timer = setInterval(() => tick((n) => n + 1), 250);
    return () => clearInterval(timer);
  }, [playing, playing?.paused]);
  if (!playing) return 0;
  const moved = playing.paused ? 0 : (performance.now() - at) / 1000;
  const position = playing.position + moved;
  return playing.seconds > 0 ? Math.min(position, playing.seconds) : position;
}

function Seek({ playing, position }) {
  const [held, setHeld] = useState(null);
  const length = playing.seconds || 0;
  if (length <= 0) return html`<div class="player-seek"><span class="mono">${clock(position)}</span></div>`;
  const shown = held ?? position;
  return html`<div class="player-seek">
    <span class="mono">${clock(shown)}</span>
    <input type="range" min="0" max=${length} step="0.1" value=${shown} aria-label=${t("Where it stands")}
      onInput=${(event) => setHeld(Number(event.currentTarget.value))}
      onChange=${async (event) => {
        await act("seek", { seconds: Number(event.currentTarget.value) });
        setHeld(null);
      }} />
    <span class="mono">${clock(length)}</span>
  </div>`;
}

function NowPlaying({ data, position }) {
  const playing = data.playing;
  const panel = playing?.panels?.[0] || 1;
  const live = useLivePicture({ panel, every: 200, on: Boolean(playing) });
  const name = (n) => data.panels?.find((p) => p.number === n)?.name || t("Panel {number}", { number: n });
  return html`<section class="player-now">
    <div class="player-screen">
      ${playing && live.src
        ? html`<img src=${live.src} alt=${t("What the panel shows")} />`
        : html`<div class="player-screen-empty">
            <${Icon} name="film" size=${28} />
            <span>${playing ? t("The panel's picture comes…") : t("Nothing plays. Choose something in the library.")}</span>
          </div>`}
    </div>
    <div class="player-title">
      <strong title=${playing?.name || ""}>${playing ? playing.name : t("Player")}</strong>
      <span class="muted">
        ${playing
          ? `${kindLabel(playing.kind)} · ${playing.index + 1} / ${playing.items.length} · ${playing.panels.map(name).join(", ")}`
          : t("Videos, animated pictures and pictures on the panel, instead of its theme")}
      </span>
    </div>
    ${playing ? html`<${Seek} playing=${playing} position=${position} />` : null}
    <div class="player-transport">
      <${IconButton} icon="previous" size=${18} label=${t("The one before")} disabled=${!playing} onClick=${() => act("previous")} />
      <button type="button" class="btn btn-primary player-main"
        disabled=${!data.available || (!playing && !data.library?.length)}
        onClick=${() => (playing ? act("toggle") : playFrom())}
        aria-label=${playing && !playing.paused ? t("Pause") : t("Play")}>
        <${Icon} name=${playing && !playing.paused ? "pause" : "play"} size=${18} />
        <span>${playing && !playing.paused ? t("Pause") : t("Play")}</span>
      </button>
      <${IconButton} icon="next" size=${18} label=${t("The next")} disabled=${!playing} onClick=${() => act("next")} />
      <${IconButton} icon="stop" size=${18} label=${t("Stop: the theme again")} disabled=${!playing} onClick=${() => act("stop")} />
    </div>
  </section>`;
}

function Choices({ data }) {
  const prefs = useStore((s) => s.prefs);
  const playing = data.playing;
  const chosen = playing ? { ...choices(prefs), ...playing } : choices(prefs);
  // while it plays a choice changes the playback (and is kept for the next one), else only kept
  const choose = (key, pref, value) => {
    setPref(pref, value);
    if (playing) act("set", { [key]: value });
  };
  const panels = data.panels || [];
  return html`<section class="player-choices">
    ${panels.length > 1
      ? html`<div class="player-choice">
          <span class="player-choice-label">${t("On")}</span>
          <div class="player-panels" role="group" aria-label=${t("The panels it plays on")}>
            ${panels.map((panel) => {
              const on = (playing ? playing.panels : chosen.panels).includes(panel.number);
              return html`<button type="button" class=${`chip${on ? " is-on" : ""}`} aria-pressed=${String(on)}
                disabled=${Boolean(playing)}
                onClick=${() => {
                  const next = on ? chosen.panels.filter((n) => n !== panel.number) : [...chosen.panels, panel.number];
                  setPref("playerPanels", next.length ? next.sort((a, b) => a - b) : [panel.number]);
                }}>${panel.number} · ${panel.name || t("Panel")}</button>`;
            })}
          </div>
        </div>`
      : null}
    <div class="player-choice">
      <span class="player-choice-label">${t("Fills the panel")}</span>
      <${Segmented} label=${t("How it fills the panel")} value=${chosen.fit}
        onChange=${(value) => choose("fit", "playerFit", value)}
        options=${[
          { value: "cover", label: t("To the edges") },
          { value: "contain", label: t("Whole") },
        ]} />
    </div>
    <div class="player-choice">
      <span class="player-choice-label">${t("After the last")}</span>
      <${Segmented} label=${t("What comes after the last")} value=${chosen.loop}
        onChange=${(value) => choose("loop", "playerLoop", value)}
        options=${[
          { value: "all", label: t("From the first"), icon: "repeat" },
          { value: "one", label: t("The same again") },
          { value: "off", label: t("The theme") },
        ]} />
    </div>
    <div class="player-choice">
      <span class="player-choice-label">${t("A picture shows")}</span>
      <${Segmented} label=${t("How long a picture shows")} value=${chosen.picture_s}
        onChange=${(value) => choose("picture_s", "playerPictureS", value)}
        options=${PICTURE_TIMES.map((s) => ({ value: s, label: t("{seconds} s", { seconds: s }) }))} />
    </div>
    <div class="player-choice">
      <span class="player-choice-label"><${Icon} name="shuffle" size=${14} /> ${t("In another order")}</span>
      <${Toggle} label=${t("In another order")} checked=${chosen.shuffle}
        onChange=${(on) => choose("shuffle", "playerShuffle", on)} />
    </div>
  </section>`;
}

function Library({ data }) {
  const [over, setOver] = useState(false);
  const [progress, setProgress] = useState(null);
  const input = useRef(null);
  const library = data.library || [];
  const add = (files) => {
    if (files?.length) upload([...files], setProgress);
  };
  const remove = async (item) => {
    const sure = await confirmDialog({
      title: t("Remove {name}?", { name: item.name }),
      message: t("It is deleted from the Player's folder."),
      confirm: t("Remove"),
      danger: true,
    });
    if (sure) act("remove", { name: item.name });
  };
  return html`<section class=${`player-library${over ? " is-over" : ""}`}
    onDragOver=${(event) => {
      event.preventDefault();
      setOver(true);
    }}
    onDragLeave=${() => setOver(false)}
    onDrop=${(event) => {
      event.preventDefault();
      setOver(false);
      add(event.dataTransfer?.files);
    }}>
    <header class="player-library-head">
      <h3 class="section-title">${t("Library")}</h3>
      <div class="player-library-actions">
        <button type="button" class="btn" onClick=${() => input.current?.click()}>
          <${Icon} name="upload" size=${15} /> ${t("Add files…")}
        </button>
        <button type="button" class="btn" onClick=${() => act("folder")} title=${data.folder}>
          <${Icon} name="file" size=${15} /> ${t("Open the folder")}
        </button>
        <input ref=${input} type="file" multiple accept=${ACCEPT} hidden
          onChange=${(event) => {
            add(event.currentTarget.files);
            event.currentTarget.value = "";
          }} />
      </div>
    </header>
    ${progress
      ? html`<div class="player-progress" role="status">
          <span>${t("Adding {name}…", { name: progress.name })}</span>
          <progress max="1" value=${progress.share}></progress>
        </div>`
      : null}
    ${library.length
      ? html`<ul class="player-grid">
          ${library.map((item) => html`<${Tile} key=${item.name} item=${item} playing=${data.playing} onRemove=${remove} />`)}
        </ul>`
      : html`<div class="player-empty">
          <${Icon} name="upload" size=${28} />
          <p>${t("Drop videos, GIFs and pictures here, or add them with Add files…")}</p>
          <p class="muted">${t("They are kept in the Player's folder; what you put there yourself plays too.")}</p>
        </div>`}
  </section>`;
}

function PlayerView() {
  const data = useStore((s) => s.player);
  useEffect(() => {
    let alive = true;
    let timer = 0;
    const next = async () => {
      if (!alive) return;
      if (!document.hidden) await load();
      if (alive) timer = setTimeout(next, POLL_MS);
    };
    next();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);
  const position = usePosition(data?.playing, data?.at);
  if (!data) return html`<div class="player"><p class="muted player-loading">${t("Loading…")}</p></div>`;
  if (data.error && !data.library) return html`<div class="player"><p class="player-note">${data.error}</p></div>`;
  return html`<div class="player">
    <div class="player-main-col">
      ${!data.available
        ? html`<p class="player-note">
            <${Icon} name="info" size=${16} />
            ${t("The Player plays on the panels of the running app. Start Libre Panel and open the Player from it.")}
          </p>`
        : null}
      ${!data.ffmpeg
        ? html`<p class="player-note">
            <${Icon} name="warning" size=${16} />
            ${t("Videos need ffmpeg, which the downloads of Libre Panel carry; pictures play without it.")}
          </p>`
        : null}
      <${NowPlaying} data=${data} position=${position} />
      <${Choices} data=${data} />
      <${MonitorSection} />
    </div>
    <${Library} data=${data} />
  </div>`;
}

registerView({
  id: "player",
  title: () => t("Player"),
  order: 45,
  icon: "film",
  component: PlayerView,
});

const playing = () => Boolean(store.get().player?.playing);

registerCommands([
  {
    id: "player.toggle",
    title: () => (store.get().player?.playing?.paused === false ? t("Pause the Player") : t("Play in the Player")),
    keywords: () => t("player video play pause media"),
    group: () => t("Player"),
    icon: "play",
    run: async () => {
      if (!store.get().player) await load();
      return playing() ? act("toggle") : playFrom();
    },
  },
  {
    id: "player.next",
    title: () => t("The next in the Player"),
    keywords: () => t("player skip forward next video"),
    group: () => t("Player"),
    icon: "next",
    when: playing,
    run: () => act("next"),
  },
  {
    id: "player.stop",
    title: () => t("Stop the Player"),
    keywords: () => t("player stop theme back"),
    group: () => t("Player"),
    icon: "stop",
    when: playing,
    run: () => act("stop"),
  },
  {
    id: "player.open",
    title: () => t("Player"),
    hint: () => t("videos and pictures on the panel"),
    keywords: () => t("player media video movie gif pictures photos slideshow"),
    group: () => t("Go to"),
    icon: "film",
    run: () => navigate("player"),
  },
]);
