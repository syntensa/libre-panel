// The hardware's names on the panel (cpu.name, gpu.name, mem.name): the sensors
// detect them, and a text of the user's own takes their place (config.toml
// [hardware], for every theme; the running panel follows). store.get().hardware is
// null until asked, false where the server has no /api/hardware, else
// {cpu, gpu, memory}, each {detected, custom}.

import { endpoints } from "./api.js";
import { t } from "./i18n.js";
import { requestRender } from "./preview.js";
import { store } from "./state.js";

/** The parts, in the order they are shown: their names and the sensor that shows them. */
export const hardwareParts = () => [
  { id: "cpu", label: t("Processor"), sensor: "cpu.name" },
  { id: "gpu", label: t("Graphics card"), sensor: "gpu.name" },
  { id: "memory", label: t("Memory"), sensor: "mem.name" },
];

/** The part whose name `sensor` shows, if it is one of them. */
export const partOfSensor = (sensor) => hardwareParts().find((part) => part.sensor === sensor) || null;

const usable = (data) => Boolean(data) && typeof data === "object" && hardwareParts().every((part) => typeof data[part.id] === "object");

let asking = null;

/** Ask the server for the names (once; `again` asks anew). */
export function loadHardware(again = false) {
  if (asking && !again) return asking;
  asking = endpoints.hardware().then(
    (data) => {
      store.set({ hardware: usable(data) ? data : false });
      return store.get().hardware;
    },
    (error) => {
      // no such route: a server without the feature; anything else: ask again later
      if (error.status === 404 || error.status === 405) store.set({ hardware: false });
      else asking = null;
      return store.get().hardware;
    },
  );
  return asking;
}

/** Use `text` as a part's name ("" goes back to the detected one). */
export async function setHardwareName(part, text) {
  const value = String(text ?? "").trim();
  const current = store.get().hardware?.[part];
  if (!current || value === (current.custom || "")) return true;
  const data = await endpoints.setHardware({ [part]: value });
  if (usable(data)) store.set({ hardware: data });
  else await loadHardware(true);
  requestRender(0); // the preview shows the name too
  return true;
}
