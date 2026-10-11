// Libre Panel's website: clips that play while they are in view (and not at all for those who
// ask for less motion), a pause button on each, and the download buttons pointed at the files
// of the latest release. Without this script the page works as it is: the GIFs move, the
// buttons lead to the release.
"use strict";

(() => {
  const still = window.matchMedia("(prefers-reduced-motion: reduce)");

  function setup(box) {
    const video = box.querySelector("video");
    const button = box.querySelector(".toggle");
    if (!video) return;
    let wanted = !still.matches; // what the visitor wants: moving unless they asked otherwise
    let seen = false;

    const show = (playing) => {
      if (!button) return;
      button.dataset.playing = String(playing);
      button.setAttribute("aria-label", playing ? button.dataset.pause : button.dataset.play);
    };
    const play = () => {
      const started = video.play();
      if (started && started.catch) started.catch(() => show(false));
    };
    const wake = () => {
      if (!seen && video.dataset.poster) video.poster = video.dataset.poster;
      seen = true;
      if (wanted) play();
    };

    video.addEventListener("play", () => show(true));
    video.addEventListener("pause", () => show(false));
    if (button) {
      button.addEventListener("click", () => {
        wanted = video.paused;
        if (wanted) {
          if (video.preload === "none") video.preload = "auto";
          play();
        } else {
          video.pause();
        }
      });
    }
    if (!wanted) {
      video.removeAttribute("autoplay");
      video.pause();
    }
    show(!video.paused);

    if (!("IntersectionObserver" in window)) {
      wake();
      return;
    }
    new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) wake();
          else if (!video.paused) video.pause();
        }
      },
      { rootMargin: "300px 0px" },
    ).observe(box);
  }

  document.querySelectorAll(".media").forEach(setup);

  // The download buttons: straight to the file of the latest release, with its version.
  const patterns = {
    setup: /-windows-x64-setup\.exe$/,
    zip: /-windows-x64-portable\.zip$/,
    appimage: /-x86_64\.AppImage$/,
    tarball: /-linux-x64\.tar\.gz$/,
  };
  const links = document.querySelectorAll("[data-asset]");
  if (!links.length || !window.fetch) return;
  fetch("https://api.github.com/repos/syntensa/libre-panel/releases/latest", {
    headers: { Accept: "application/vnd.github+json" },
  })
    .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
    .then((release) => {
      const assets = Array.isArray(release.assets) ? release.assets : [];
      links.forEach((link) => {
        const pattern = patterns[link.dataset.asset];
        const asset = pattern && assets.find((item) => pattern.test(item.name));
        if (asset) link.href = asset.browser_download_url;
      });
      if (typeof release.tag_name === "string") {
        document.querySelectorAll("[data-version]").forEach((element) => {
          element.textContent = element.dataset.version.replace("{v}", release.tag_name);
        });
      }
    })
    .catch(() => {}); // the buttons keep leading to the release page
})();
