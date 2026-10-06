(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const isPages = location.hostname.endsWith("github.io");
  let savedBase = null;
  try {
    savedBase = localStorage.getItem("drop-api-base");
  } catch {
    /* Private browsing may disable storage. */
  }
  let apiBase =
    savedBase ??
    (window.DROP_CONFIG?.apiBase || (isPages ? "http://127.0.0.1:8000" : ""));
  apiBase = apiBase.replace(/\/$/, "");
  let info = null;
  let busy = false;
  let lookupTimer;
  let lookupVersion = 0;
  let healthVersion = 0;
  let ttlMinutes = 30;
  let healthy = false;
  let ffmpegReady = false;

  function showNotice(message, error = false) {
    $("notice").textContent = message;
    $("notice").classList.toggle("error", error);
    $("notice").classList.toggle("d-none", !message);
  }

  function setBusy(value, message) {
    busy = value;
    $("download-button").disabled = value;
    $("video-url").disabled = value;
    $("quality").disabled = value;
    $("paste-button").disabled = value;
    document.querySelectorAll('input[name="format"]').forEach((input) => {
      input.disabled = value;
    });
    $("button-spinner").classList.toggle("d-none", !value);
    $("download-button")
      .querySelector(".icon")
      .classList.toggle("d-none", value);
    $("download-button-text").textContent =
      message || (format() === "mp4" ? "Get my video" : "Get my audio");
  }

  function format() {
    return document.querySelector('input[name="format"]:checked').value;
  }

  function qualityOptions() {
    const oldValue = $("quality").value;
    const audio = format() === "mp3";
    const choices = audio
      ? ["320", "256", "192", "128"]
      : ["best", ...(info?.qualities || []).slice().reverse()];
    $("quality").replaceChildren(
      ...choices.map((value) => {
        let label = audio
          ? `${value} kbps`
          : value === "best"
            ? "Best available"
            : `${value}p`;
        if (!audio && value === "2160") label += " · 4K";
        if (!audio && value === "1080") label += " · Full HD";
        return new Option(label, value);
      }),
    );
    if (choices.includes(oldValue)) $("quality").value = oldValue;
    $("quality-label").textContent = audio ? "Audio quality" : "Video quality";
    if (!busy)
      $("download-button-text").textContent = audio
        ? "Get my audio"
        : "Get my video";
  }

  async function api(path, data, timeoutMs = 60000) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(apiBase + path, {
        method: data === undefined ? "GET" : "POST",
        headers:
          data === undefined ? {} : { "Content-Type": "application/json" },
        body: data === undefined ? undefined : JSON.stringify(data),
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
      });
      let result;
      try {
        result = await response.json();
      } catch {
        throw new Error(
          "This address did not return a valid download server response. Check your connection settings.",
        );
      }
      if (!response.ok) {
        const detail = result.detail;
        throw new Error(
          Array.isArray(detail)
            ? detail
                .map((item) => item.msg.replace(/^Value error, /, ""))
                .join(" ")
            : detail || "Something went wrong. Please try again.",
        );
      }
      return result;
    } catch (error) {
      if (error.name === "AbortError")
        throw new Error(
          "The server took too long to respond. Please try again.",
        );
      if (error instanceof TypeError)
        throw new Error(
          "Couldn’t reach your download server. Start it on your computer and check the connection settings above. Your browser may need permission to access localhost.",
        );
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }

  async function checkHealth() {
    const version = ++healthVersion;
    $("connection-label").textContent = "Checking connection";
    $("status-dot").className = "status-dot";
    try {
      const result = await api("/api/health", undefined, 6000);
      if (version !== healthVersion) return;
      if (result.status !== "ok" || typeof result.ffmpeg !== "boolean")
        throw new Error("Invalid server");
      healthy = true;
      ffmpegReady = result.ffmpeg;
      ttlMinutes = Math.round(result.job_ttl / 60) || 30;
      $("connection-label").textContent = result.ffmpeg
        ? "Server connected"
        : "Server needs FFmpeg";
      $("status-dot").classList.add(
        result.ffmpeg ? "connected" : "disconnected",
      );
      document.querySelector(
        ".converter-footnote span:last-child",
      ).textContent =
        `Processed by your download server. Files are automatically cleared after ${ttlMinutes} minutes.`;
    } catch {
      if (version !== healthVersion) return;
      healthy = ffmpegReady = false;
      $("connection-label").textContent = "Connect your server";
      $("status-dot").classList.add("disconnected");
    }
  }

  function validUrl(value) {
    try {
      const url = new URL(value);
      if (
        !["http:", "https:"].includes(url.protocol) ||
        url.username ||
        url.password ||
        (url.port && !["80", "443"].includes(url.port))
      )
        return false;
      const parts = url.pathname.replace(/^\/|\/$/g, "").split("/");
      const host = url.hostname.toLowerCase();
      let id;
      if (host === "youtu.be" && parts.length === 1) id = parts[0];
      else if (
        [
          "youtube.com",
          "www.youtube.com",
          "m.youtube.com",
          "music.youtube.com",
        ].includes(host)
      ) {
        if (url.pathname === "/watch") id = url.searchParams.get("v");
        else if (
          parts.length === 2 &&
          ["shorts", "embed", "live"].includes(parts[0])
        )
          id = parts[1];
      }
      return /^[A-Za-z0-9_-]{11}$/.test(id || "");
    } catch {
      return false;
    }
  }

  function duration(seconds) {
    const s = Math.floor(seconds || 0);
    const hours = Math.floor(s / 3600);
    const mins = Math.floor((s % 3600) / 60);
    return hours
      ? `${hours}:${String(mins).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`
      : `${mins}:${String(s % 60).padStart(2, "0")}`;
  }

  function displayInfo(result, inputUrl) {
    info = { ...result, inputUrl };
    $("preview-title").textContent = result.title;
    $("preview-author").textContent = result.author;
    $("preview-duration").textContent = duration(result.duration);
    $("preview-image").src = result.thumbnail;
    $("video-preview").classList.remove("d-none");
    qualityOptions();
  }

  async function lookup() {
    const url = $("video-url").value.trim();
    if (!validUrl(url) || busy) return;
    const version = ++lookupVersion;
    showNotice("Finding your video and its available qualities…");
    try {
      const result = await api("/api/info", { url });
      if (version !== lookupVersion || $("video-url").value.trim() !== url)
        return;
      displayInfo(result, url);
      showNotice("");
    } catch (error) {
      if (version === lookupVersion) showNotice(error.message, true);
    }
  }

  function clearResults() {
    $("ready-panel").classList.add("d-none");
    $("progress-panel").classList.add("d-none");
    $("save-file").removeAttribute("href");
  }

  $("video-url").addEventListener("input", () => {
    clearTimeout(lookupTimer);
    lookupVersion++;
    info = null;
    $("video-url").removeAttribute("aria-invalid");
    $("video-preview").classList.add("d-none");
    clearResults();
    qualityOptions();
    showNotice("");
    if (validUrl($("video-url").value.trim()))
      lookupTimer = setTimeout(lookup, 650);
  });
  document.querySelectorAll('input[name="format"]').forEach((input) =>
    input.addEventListener("change", () => {
      qualityOptions();
      clearResults();
    }),
  );
  $("quality").addEventListener("change", clearResults);

  $("paste-button").addEventListener("click", async () => {
    try {
      $("video-url").value = (await navigator.clipboard.readText()).trim();
      $("video-url").dispatchEvent(new Event("input"));
      $("video-url").focus();
    } catch {
      showNotice(
        "Paste your link directly into the field with Ctrl+V (or ⌘+V).",
      );
      $("video-url").focus();
    }
  });

  const dropField = document.querySelector(".url-field");
  dropField.addEventListener("dragover", (event) => {
    event.preventDefault();
    if (!busy) dropField.classList.add("drag-active");
  });
  dropField.addEventListener("dragleave", () =>
    dropField.classList.remove("drag-active"),
  );
  dropField.addEventListener("drop", (event) => {
    event.preventDefault();
    dropField.classList.remove("drag-active");
    if (busy) return;
    const value = (
      event.dataTransfer.getData("text/uri-list") ||
      event.dataTransfer.getData("text/plain")
    )
      .split(/\r?\n/)
      .find((line) => line.trim() && !line.startsWith("#"));
    if (value) {
      $("video-url").value = value.trim();
      $("video-url").dispatchEvent(new Event("input"));
      $("video-url").focus();
    }
  });

  $("download-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const url = $("video-url").value.trim();
    if (!validUrl(url)) {
      showNotice(
        "Paste a valid YouTube video or Shorts link to get started.",
        true,
      );
      $("video-url").setAttribute("aria-invalid", "true");
      $("video-url").focus();
      return;
    }
    clearTimeout(lookupTimer);
    lookupVersion++;
    const chosenFormat = format();
    const chosenQuality = $("quality").value;
    clearResults();
    showNotice("");
    setBusy(true, "Finding your video…");
    try {
      await checkHealth();
      if (!healthy)
        throw new Error(
          "Start your local download server, then connect it using the connection button above.",
        );
      if (!ffmpegReady)
        throw new Error(
          "FFmpeg is missing from your server. Run scripts/setup.ps1 to install it.",
        );
      if (!info || info.inputUrl !== url)
        displayInfo(await api("/api/info", { url }), url);
      const job = await api("/api/download", {
        url,
        format: chosenFormat,
        quality: chosenQuality,
      });
      $("progress-panel").classList.remove("d-none");
      setBusy(true, "Working on it…");
      const start = Date.now();
      let failedPolls = 0;
      while (true) {
        let state;
        try {
          state = await api(
            "/api/jobs/" + encodeURIComponent(job.id),
            undefined,
            10000,
          );
          failedPolls = 0;
        } catch (error) {
          if (++failedPolls >= 3) throw error;
          await new Promise((resolve) => setTimeout(resolve, 2000));
          continue;
        }
        const percent = Math.min(100, Math.max(0, state.progress || 0));
        $("progress-message").textContent = state.message;
        $("progress-percent").textContent = `${Math.round(percent)}%`;
        $("progress-bar").style.width = `${percent}%`;
        $("progress-track").setAttribute("aria-valuenow", String(percent));
        if (state.status === "error") throw new Error(state.message);
        if (state.status === "ready") {
          if (state.download_url !== "/api/files/" + job.id)
            throw new Error("The server returned an invalid file link.");
          $("save-file").href = apiBase + state.download_url;
          $("ready-description").textContent =
            `Your ${chosenFormat.toUpperCase()} is ready. Save it within ${ttlMinutes} minutes.`;
          $("ready-panel").classList.remove("d-none");
          $("progress-panel").classList.add("d-none");
          $("save-file").focus();
          break;
        }
        if (Date.now() - start > 30 * 60 * 1000)
          throw new Error(
            "This download is taking too long. Try a shorter video or lower quality.",
          );
        await new Promise((resolve) => setTimeout(resolve, 1100));
      }
    } catch (error) {
      $("progress-panel").classList.add("d-none");
      showNotice(error.message, true);
    } finally {
      setBusy(false);
    }
  });

  $("server-modal").addEventListener("show.bs.modal", () => {
    $("server-url").value = apiBase;
  });
  $("server-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = $("server-url").value.trim().replace(/\/$/, "");
    if (busy) {
      $("server-notice").textContent =
        "Wait for your current download to finish before switching servers.";
      $("server-notice").classList.remove("d-none");
      return;
    }
    try {
      if (value) {
        const parsed = new URL(value);
        if (
          !["http:", "https:"].includes(parsed.protocol) ||
          parsed.username ||
          parsed.password ||
          parsed.search ||
          parsed.hash ||
          parsed.pathname !== "/"
        )
          throw new Error(
            "Enter a server origin, for example http://127.0.0.1:8000.",
          );
        const local = ["localhost", "127.0.0.1", "[::1]"].includes(
          parsed.hostname,
        );
        if (
          location.protocol === "https:" &&
          parsed.protocol === "http:" &&
          !local
        )
          throw new Error("Use HTTPS for a remote download server.");
      }
      apiBase = value;
      try {
        localStorage.setItem("drop-api-base", apiBase);
      } catch {
        /* Still works for this visit. */
      }
      info = null;
      lookupVersion++;
      $("video-preview").classList.add("d-none");
      clearResults();
      qualityOptions();
      showNotice("");
      $("server-notice").classList.add("d-none");
      bootstrap.Modal.getOrCreateInstance($("server-modal")).hide();
      await checkHealth();
      if (healthy && validUrl($("video-url").value.trim())) lookup();
    } catch (error) {
      $("server-notice").textContent = error.message;
      $("server-notice").classList.remove("d-none");
    }
  });
  qualityOptions();
  checkHealth();
})();
