// Loads the script of the page whose address (/plugins/<id>/) is in the window.
// Another built-in page is a line in this table (and its module in libre_panel/pages).
import { el, loadTexts, t } from "/static/kit.js";

const PAGES = { control: "control.js", autopilot: "autopilot.js" };

await loadTexts();
const id = decodeURIComponent(location.pathname.split("/")[2] || "");
const root = document.getElementById("page");
try {
  if (!Object.hasOwn(PAGES, id)) throw new Error(id);
  const page = await import(`./${PAGES[id]}`);
  await page.start(root);
} catch (error) {
  root.replaceChildren(el("p", { class: "note error" }, t("The page did not load: {error}", { error: error.message })));
}
