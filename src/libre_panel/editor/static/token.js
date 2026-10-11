// The access token of this run of Libre Panel (editor/access.py). The server answers only
// requests that carry it, so no other user of this PC can talk to it.
//
// It comes in the address, after a `#` (`/#/design&token=...`): a browser never sends that part
// to a server, and the server's log never has it. It is taken out of the address at once and
// kept for this tab, also for the plugins' pages in frames, which share the tab's storage.
// A tab opened without it (a bookmark) has none: the server says so, and what to do.
//
//   <script type="module" src="/static/token.js"></script>   (before the page's own scripts)
//   import { authHeaders, withToken } from "/static/token.js";

const KEY = "libre-panel-token";
const IN_ADDRESS = /(^|[&?])token=([A-Za-z0-9_-]{16,200})(?=&|$)/;

let current = "";
let settled = ""; // the fragment of the address as it was left: the app's route

function stored() {
  try {
    return sessionStorage.getItem(KEY) || "";
  } catch {
    return ""; // blocked storage: the token lasts as long as the page
  }
}

function keep(token) {
  current = token;
  try {
    sessionStorage.setItem(KEY, token);
  } catch {
    // see stored()
  }
}

// A token in the address replaces the one kept, and leaves the address without it. It comes
// also later: the window brings a new one, with the route, when Libre Panel started anew; one
// that comes alone leaves the route where it was.
function take() {
  const fragment = location.hash.slice(1);
  const found = IN_ADDRESS.exec(fragment);
  if (!found) {
    settled = fragment;
    return;
  }
  keep(found[2]);
  settled = fragment.replace(found[0], "").replace(/^[&?]/, "") || settled;
  history.replaceState(history.state, "", location.pathname + location.search + (settled ? `#${settled}` : ""));
}

take();
addEventListener("hashchange", take);

/** The token of this run ("" in a tab that was opened without it). */
export const token = () => current || stored();

/** The header every request to the server carries. */
export const authHeaders = () => ({ "X-Libre-Panel": token() });

/** An address with the token in its query: for what the browser asks for by itself, where no
 * header can be set (`<img src>`). Use it for pictures only. */
export function withToken(url) {
  return `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token())}`;
}
