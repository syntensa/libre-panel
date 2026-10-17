// Ctrl+K by the words a person uses: every feature of Libre Panel as a command, also those that
// live in a card of Start or Settings (how the panel is mounted, its pages, the start screen,
// smooth motion, the notices, the weather's place, the folders, help), and more words for the
// commands other parts of the app register. A command that changes a setting writes it the way
// its card does (config.toml through /api/settings, a page through /api/pages); one that has
// more to it than a switch leads to its card, which Settings scrolls into view.

import { refreshPages } from "../core/actions.js";
import { endpoints } from "../core/api.js";
import { appAction } from "../core/app.js";
import { describeCommand, registerCommands, registerProvider } from "../core/commands.js";
import { loadHardware } from "../core/hardware.js";
import { t } from "../core/i18n.js";
import { mounted } from "../core/orientation.js";
import { enableService, loadAbout, openFolder, saveSettings, setting } from "../core/settings.js";
import { setPref, store } from "../core/state.js";
import { openVolumeLook, showVolume, volumeNotice } from "../core/volume.js";
import { toast, toastError } from "../ui/toast.js";
import { openHelp } from "./help.js";
import { pageName } from "./panel-popover.js";
import { navigate } from "./views.js";

const config = () => store.get().settings?.config ?? null;
const appRuns = () => store.get().app.available && !store.get().app.quit;
const onWindows = () => store.get().unlockInfo?.platform === "win32" || /Windows/.test(navigator.userAgent);

/** Settings, at one of its cards ("panel/start-screen"): it scrolls the card into view. */
const toCard = (path) => () => navigate("settings", path);

/** Open one of the project's pages (the help's links) in the browser, as its links do. */
function openLink(href) {
  const link = document.createElement("a");
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  document.body.append(link);
  link.click();
  link.remove();
}

/** The project's links (/api/about) come once, when the palette first searches. */
let askedAbout = false;
const links = () => {
  if (!store.get().aboutInfo && !askedAbout) {
    askedAbout = true;
    loadAbout();
  }
  return store.get().aboutInfo?.links ?? null;
};

// -- how the panel is mounted, upside down -------------------------------------------------------

async function mount(way) {
  if (!(await saveSettings({ "device.orientation": way }))) return;
  toast(
    way === "portrait"
      ? t("Every screen now shows in its version for a panel on end.")
      : t("Every screen now shows in its version for a panel on its side."),
    { kind: "success" },
  );
}

/** Upside down: the running app's switch, or config.toml's while none runs (as Settings does). */
const upsideDown = () => (appRuns() ? Boolean(store.get().app.upside_down) : setting("device.rotate") === 180);

function turnUpsideDown(on) {
  if (appRuns()) return appAction("upside_down", on);
  return saveSettings({ "device.rotate": on ? 180 : 0 });
}

// -- the pages the panel switches to by itself ----------------------------------------------------

const PAGES = {
  game: {
    on: () => t("Game mode: on"),
    off: () => t("Game mode: off"),
    hint: () => t("the game screen while a game runs"),
    keywords: () => t("game games gaming play fps steam game screen"),
  },
  media: {
    on: () => t("Music page: on"),
    off: () => t("Music page: off"),
    hint: () => t("while music plays"),
    keywords: () => t("music song track playing now spotify media player"),
  },
  idle: {
    on: () => t("Idle page: on"),
    off: () => t("Idle page: off"),
    hint: () => t("when nobody has touched the PC for a while"),
    keywords: () => t("idle away clock screensaver inactive nobody"),
  },
  report: {
    on: () => t("Round report: on"),
    off: () => t("Round report: off"),
    hint: () => t("after a game"),
    keywords: () => t("round report after a game summary match stats"),
  },
};

async function switchPage(id, on) {
  try {
    store.set({ pages: await endpoints.setPage(id, { on }) });
    toast(on ? t("The page {page} is on.", { page: pageName(id) }) : t("The page {page} is off.", { page: pageName(id) }), {
      kind: "success",
    });
  } catch (error) {
    toastError(error);
    refreshPages();
  }
}

/** Design with its Pages open, as Start's "Change the pages" does. */
function openPages() {
  setPref("panel", "pages");
  navigate("design");
}

// -- the commands ---------------------------------------------------------------------------------

export function registerFeatureCommands() {
  const panel = () => t("Panel");
  const pages = () => t("Pages");
  const settings = () => t("Settings");
  const help = () => t("Help");

  registerProvider(() => {
    const way = mounted(); // "" until config.toml is read, or while it names none
    return [
      way !== "portrait" && {
        id: "panel.mounted.portrait",
        title: () => t("Panel mounted: vertical"),
        hint: () => t("for a panel that stands on end"),
        keywords: () => t("portrait upright on end standing vertical rotate turn orientation how the panel is mounted"),
        group: panel,
        icon: "portrait",
        rank: 14,
        suggest: way ? null : 6,
        run: () => mount("portrait"),
      },
      way !== "landscape" && {
        id: "panel.mounted.landscape",
        title: () => t("Panel mounted: horizontal"),
        hint: () => t("for a panel that lies on its side"),
        keywords: () => t("landscape lying on its side wide horizontal orientation how the panel is mounted"),
        group: panel,
        icon: "landscape",
        rank: 14,
        suggest: way ? null : 7,
        run: () => mount("landscape"),
      },
      (appRuns() || config()) && {
        id: "app.upside-down",
        title: () => (upsideDown() ? t("Upside down: off") : t("Upside down: on")),
        hint: () => t("for a panel mounted the other way round"),
        keywords: () => t("rotate 180 flip turn around the other way round head"),
        group: panel,
        icon: "swap",
        rank: 33,
        run: () => turnUpsideDown(!upsideDown()),
      },
    ].filter(Boolean);
  });

  registerCommands([
    {
      id: "panel.brightness",
      title: () => t("Brightness"),
      hint: () => t("Settings → Panel"),
      keywords: () => t("dim bright darker lighter backlight light"),
      group: panel,
      icon: "sun",
      rank: 15,
      suggest: 4,
      run: toCard("panel/panel-display"),
    },
    {
      id: "panel.smooth",
      title: () => t("Smooth motion (video mode)"),
      hint: () => t("up to 50 pictures a second"),
      keywords: () => t("video fps frames per second fluid stutter standby clip frame rate"),
      group: panel,
      icon: "pulse",
      rank: 16,
      suggest: 8,
      run: toCard("panel/panel-video"),
    },
    {
      id: "panel.dark",
      title: () => t("Dark when Libre Panel quits"),
      hint: () => t("the panel's light off when it quits"),
      keywords: () => t("dark off black quit close exit shutdown sleep screen off backlight"),
      group: panel,
      icon: "power",
      rank: 16,
      run: toCard("panel/panel-display"),
    },
    {
      id: "panel.motion",
      title: () => t("Motion on the panel"),
      hint: () => t("calm for a slow panel"),
      keywords: () => t("calm animation movement still slow serial gliding fades"),
      group: panel,
      icon: "pulse",
      rank: 16,
      run: toCard("panel/panel-video"),
    },
    {
      id: "panel.start-screen",
      title: () => t("Start screen: boot logo and standby clip"),
      hint: () => t("what the panel shows by itself"),
      keywords: () => t("boot logo startup splash power on factory logo"),
      group: panel,
      icon: "power",
      rank: 17,
      suggest: 5,
      run: toCard("panel/start-screen"),
    },
    {
      id: "panel.found",
      title: () => t("Which panel is connected"),
      hint: () => t("Settings → Panel"),
      keywords: () => t("model device detect connected size another panel usb serial port driver"),
      group: panel,
      icon: "landscape",
      rank: 30,
      run: toCard("panel/panel-found"),
    },
    {
      id: "pages.change",
      title: () => t("What the panel shows when"),
      hint: () => t("the pages: normal, in game, music, idle"),
      keywords: () => t("pages change the pages game music idle round report themes for pages"),
      group: pages,
      icon: "pages",
      rank: 18,
      suggest: 3,
      run: openPages,
    },
    {
      id: "settings.place",
      title: () => t("Weather for your place"),
      hint: () => t("also for the sun and the moon"),
      keywords: () => t("place location city town where weather sun moon sunrise forecast"),
      group: settings,
      icon: "weather",
      rank: 26,
      run: toCard("weather/place"),
    },
    {
      id: "settings.symbols",
      title: () => t("Look of the weather symbols"),
      hint: () => t("classic or vivid, the moon too"),
      keywords: () => t("weather symbols icons moon sun rain snow look style pack animation classic vivid"),
      group: settings,
      icon: "weather",
      rank: 26,
      run: toCard("weather/symbols"),
    },
    {
      id: "settings.games",
      title: () => t("Which programs are games"),
      hint: () => t("game mode"),
      keywords: () => t("game detect detected not recognised exe steam list ignore"),
      group: settings,
      icon: "game",
      rank: 27,
      run: toCard("services/service-game-title"),
    },
    {
      id: "settings.language",
      title: () => t("Language"),
      hint: () => t("the app, the tray menu and the panel"),
      keywords: () => t("language english german deutsch translation"),
      group: settings,
      icon: "globe",
      rank: 79,
      run: toCard("system/start"),
    },
    {
      id: "settings.hotkeys",
      title: () => t("Keyboard shortcuts for the whole PC"),
      hint: () => t("pause, brightness and the app from any window"),
      keywords: () => t("hotkey shortcut keyboard keys global pause brightness"),
      group: settings,
      icon: "keyboard",
      rank: 81,
      run: toCard("system/hotkeys"),
    },
    {
      id: "settings.updates",
      title: () => t("New versions"),
      hint: () => t("look once a day whether one is out"),
      keywords: () => t("update upgrade new version release download latest"),
      group: settings,
      icon: "download",
      rank: 80,
      run: toCard("system/start"),
    },
    {
      id: "folder.config",
      title: () => t("Open settings folder"),
      hint: () => t("config.toml, plugins and the rest"),
      keywords: () => t("folder directory explorer files config"),
      group: settings,
      icon: "file",
      rank: 88,
      run: () => openFolder("config"),
    },
    {
      id: "folder.themes",
      title: () => t("Open the folder of your themes"),
      hint: () => t("the themes you made or imported"),
      keywords: () => t("folder directory explorer files themes"),
      group: settings,
      icon: "file",
      rank: 88,
      run: () => openFolder("themes"),
    },
    {
      id: "folder.logs",
      title: () => t("Open log"),
      hint: () => t("what Libre Panel did, for finding a problem"),
      keywords: () => t("log logs folder error problem what happened"),
      group: settings,
      icon: "file",
      rank: 88,
      run: () => openFolder("logs"),
    },
    {
      id: "help.shortcuts",
      title: () => t("Help and keyboard shortcuts"),
      keywords: () => t("help guide manual faq questions documentation keys shortcuts keyboard"),
      group: help,
      icon: "keyboard",
      keys: "?",
      rank: 9,
      suggest: 20,
      run: () => openHelp(),
    },
    {
      id: "help.report-panel",
      title: () => t("Report a panel"),
      hint: () => t("not supported, or it behaves oddly"),
      keywords: () => t("unsupported not supported new panel doctor support problem bug"),
      group: help,
      icon: "landscape",
      rank: 89,
      run: toCard("about/about-report"),
    },
  ]);

  // what needs to be known first: the pages, the notices' switch, the project's links, the fans
  registerProvider(() => {
    const list = [];
    for (const page of store.get().pages?.pages || []) {
      const words = PAGES[page.id];
      // the round report's page is there while the memory service makes one, as on Start
      if (!words || (page.id === "report" && !page.theme)) continue;
      list.push({
        id: `page.switch.${page.id}`,
        title: page.on ? words.off : words.on,
        hint: words.hint,
        keywords: words.keywords,
        group: pages,
        icon: { game: "game", media: "music", idle: "clock", report: "list" }[page.id],
        rank: 19,
        run: () => switchPage(page.id, !page.on),
      });
    }
    const enabled = config()?.services?.enabled;
    if (Array.isArray(enabled)) {
      const on = enabled.includes("notices");
      list.push({
        id: "settings.notices",
        title: () => (on ? t("Notices: off") : t("Notices: on")),
        hint: () => t("the volume, a new track or a warning, briefly over the screen"),
        keywords: () => t("notices notifications messages popups volume warnings alerts"),
        group: settings,
        icon: "info",
        rank: 25,
        run: () => enableService("notices", !on),
      });
      // the volume over every screen, and the look it has there (Look → Notices → Volume)
      const volume = volumeNotice(config()).on;
      list.push({
        id: "settings.volume-notice",
        title: () => (volume ? t("Show the volume: off") : t("Show the volume: on")),
        hint: () => t("when you turn it, on every screen but the music page's"),
        keywords: () => t("volume overlay show volume bar loudness sound speaker osd"),
        group: settings,
        icon: "volume",
        rank: 24,
        run: () => showVolume(!volume),
      });
      list.push({
        id: "look.volume",
        title: () => t("How the volume looks"),
        hint: () => t("a bar, levels, a ring or a large number"),
        keywords: () => t("volume look style bar levels ring large overlay"),
        group: settings,
        icon: "volume",
        rank: 40,
        run: openVolumeLook,
      });
    }
    const info = store.get().unlockInfo;
    if (info?.platform === "win32" && info.available && !info.service) {
      list.push({
        id: "settings.unlock",
        title: () => t("Unlock all sensors"),
        hint: () => t("temperatures, power and fans need it"),
        keywords: () => t("temperatures missing sensors administrator service unlock cpu gpu mainboard"),
        group: settings,
        icon: "sensor",
        rank: 24,
        run: toCard("sensors/sensor-service"),
      });
    }
    if (onWindows()) {
      list.push({
        id: "settings.elevated",
        title: () => t("Start with administrator rights"),
        hint: () => t("Settings → Start and system"),
        keywords: () => t("administrator admin rights elevated highest"),
        group: settings,
        icon: "lock",
        rank: 87,
        run: toCard("system/start"),
      });
    }
    if (store.get().hardware === null) loadHardware();
    if (store.get().hardware?.fan_look?.looks?.length) {
      list.push({
        id: "settings.fan-looks",
        title: () => t("How the fans turn"),
        hint: () => t("the look of the fans on the panel"),
        keywords: () => t("fan fans rotor spin animation look rpm"),
        group: settings,
        icon: "fan",
        rank: 86,
        run: toCard("sensors/fan-looks"),
      });
    }
    const project = links();
    if (project) {
      list.push(
        {
          id: "help.faq",
          title: () => t("Questions and answers"),
          hint: () => t("the guide on the project page"),
          keywords: () => t("help faq guide manual questions answers documentation readme"),
          group: help,
          icon: "info",
          rank: 90,
          run: () => openLink(`${project.project}#faq`),
        },
        {
          id: "help.issue",
          title: () => t("Report a problem or an idea"),
          hint: () => t("on the project page"),
          keywords: () => t("report a bug problem error issue idea wish feedback"),
          group: help,
          icon: "warning",
          rank: 85,
          run: () => openLink(project.issues),
        },
      );
    }
    return list;
  });

  // more words for the commands of Start, Look, Design and Settings
  const more = {
    "home.sequence": () => t("screens in turn one after another rotate cycle slideshow alternate"),
    "home.empty": () => t("new blank empty theme"),
    "settings.panel": () => t("panel brightness video smooth motion start screen mounted upside down"),
    "settings.sensors": () => t("sensors readings temperatures missing sources hardware names fans"),
    "settings.services": () => t("services game mode round report notices cooling downloads autopilot devices"),
    "settings.weather": () => t("weather place location city sun moon"),
    "settings.system": () => t("autostart start with windows login administrator language window update new version"),
    "settings.about": () => t("about version licence help report folders"),
    "settings.hardware-names": () => t("hardware processor cpu graphics card gpu memory ram name rename fan names"),
    "look.light": () => t("glow light shine lighting"),
    "look.grid": () => t("grid background lines pattern"),
    "look.dust": () => t("dust particles depth stars"),
  };
  for (const [id, keywords] of Object.entries(more)) describeCommand(id, { keywords });
  for (const finish of ["lit", "facet", "acrylic", "flat"]) {
    describeCommand(`look.finish.${finish}`, { keywords: () => t("finish material glossy glass matte flat style") });
  }
  describeCommand("home.themes", { keywords: () => t("themes gallery choose switch screens designs"), suggest: 2 });
}
