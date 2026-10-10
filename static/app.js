(() => {
  "use strict";

  const KEY = "phonebridge_token";
  const $ = (id) => document.getElementById(id);
  let token = localStorage.getItem(KEY) || "";
  let cwd = "";

  // ---------- helpers ----------
  function toast(msg, isErr) {
    const t = $("toast");
    t.textContent = msg;
    t.className = "toast" + (isErr ? " err" : "");
    t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => (t.hidden = true), isErr ? 5000 : 2500);
  }

  function apiUrl(path, params) {
    const q = new URLSearchParams(params || {});
    q.set("token", token);
    return path + "?" + q.toString();
  }

  class AuthError extends Error {}

  function onUnauthorized() {
    token = "";
    localStorage.removeItem(KEY);
    setStatus(false);
    showTokenForm(true);
    toast("Token missing or wrong. Paste the token shown in a-Shell.", true);
  }

  async function api(path, opts) {
    opts = opts || {};
    if (!token) {
      showTokenForm(true);
      throw new AuthError("no token");
    }
    const res = await fetch(apiUrl(path, opts.params), {
      method: opts.method || "GET",
      headers: opts.json !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: opts.json !== undefined ? JSON.stringify(opts.json) : undefined,
    });
    if (res.status === 401) {
      onUnauthorized();
      throw new AuthError("unauthorized");
    }
    let data = {};
    try { data = await res.json(); } catch (e) { /* not json */ }
    if (!res.ok) throw new Error(data.error || "HTTP " + res.status);
    return data;
  }

  function fail(e) {
    if (e instanceof AuthError) return;
    toast(e.message || String(e), true);
  }

  function fmtSize(n) {
    if (n < 1024) return n + " B";
    const u = ["KB", "MB", "GB"];
    let i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
    return n.toFixed(n < 10 ? 1 : 0) + " " + u[i];
  }

  function fmtTime(sec) {
    const d = new Date(sec * 1000);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) +
      " " + d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }

  function join(a, b) { return a ? a + "/" + b : b; }

  function setStatus(on) {
    const s = $("status");
    s.className = "dot " + (on ? "on" : "off");
    s.title = on ? "Connected" : "Not connected";
  }

  // ---------- token ----------
  function showTokenForm(show) {
    $("tokenForm").hidden = !show;
    $("tokenEdit").hidden = show;
    if (show) setTimeout(() => $("tokenInput").focus(), 0);
  }

  $("tokenForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("tokenInput").value.trim();
    if (!v) return;
    token = v;
    localStorage.setItem(KEY, token);
    $("tokenInput").value = "";
    showTokenForm(false);
    connect();
  });

  $("tokenEdit").addEventListener("click", () => showTokenForm(true));

  // ---------- tabs ----------
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => {
        const on = b === btn;
        b.classList.toggle("active", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });
      $("files").hidden = btn.dataset.tab !== "files";
      $("clip").hidden = btn.dataset.tab !== "clip";
    });
  });

  // ---------- files ----------
  function renderCrumbs() {
    const c = $("crumbs");
    c.textContent = "";
    const parts = cwd ? cwd.split("/") : [];
    const add = (label, path, isLast) => {
      if (isLast) {
        const s = document.createElement("span");
        s.className = "here";
        s.textContent = label;
        c.appendChild(s);
      } else {
        const a = document.createElement("a");
        a.textContent = label;
        a.href = "#";
        a.addEventListener("click", (e) => { e.preventDefault(); loadFiles(path); });
        c.appendChild(a);
      }
    };
    add("Documents", "", parts.length === 0);
    parts.forEach((p, i) => {
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.textContent = "/";
      c.appendChild(sep);
      add(p, parts.slice(0, i + 1).join("/"), i === parts.length - 1);
    });
  }

  function renderList(entries) {
    const list = $("list");
    list.textContent = "";
    if (!entries.length) {
      const e = document.createElement("div");
      e.className = "empty";
      e.textContent = "This folder is empty. Drop files here or use Upload.";
      list.appendChild(e);
      return;
    }
    for (const it of entries) {
      const row = document.createElement("div");
      row.className = "row";
      row.tabIndex = 0;
      const path = join(cwd, it.name);

      const icon = document.createElement("span");
      icon.className = "icon";
      icon.textContent = it.is_dir ? "\u{1F4C1}" : "\u{1F4C4}";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = it.name;
      name.title = it.name;
      const size = document.createElement("span");
      size.className = "size";
      size.textContent = it.is_dir ? "" : fmtSize(it.size);
      const time = document.createElement("span");
      time.className = "time";
      time.textContent = fmtTime(it.mtime);
      const del = document.createElement("button");
      del.className = "del";
      del.type = "button";
      del.title = "Delete";
      del.setAttribute("aria-label", "Delete " + it.name);
      del.textContent = "\u2715";

      const open = () => {
        if (it.is_dir) loadFiles(path);
        else download(path);
      };
      row.addEventListener("click", open);
      row.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        remove(path, it.name, it.is_dir);
      });

      row.append(icon, name, size, time, del);
      list.appendChild(row);
    }
  }

  async function loadFiles(path) {
    try {
      const data = await api("/api/files", { params: { path: path || "" } });
      cwd = data.path || "";
      setStatus(true);
      renderCrumbs();
      renderList(data.entries);
    } catch (e) {
      fail(e);
    }
  }

  function download(path) {
    const a = document.createElement("a");
    a.href = apiUrl("/api/file", { path: path, dl: "1" });
    a.download = path.split("/").pop();
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async function remove(path, name, isDir) {
    if (!confirm("Delete " + (isDir ? "folder" : "file") + " \u201C" + name + "\u201D from the phone?")) return;
    try {
      await api("/api/file", { method: "DELETE", params: { path: path } });
      toast("Deleted " + name);
      loadFiles(cwd);
    } catch (e) {
      fail(e);
    }
  }

  function upload(files) {
    files = Array.from(files || []);
    if (!files.length) return;
    if (!token) { showTokenForm(true); return; }
    const total = files.reduce((n, f) => n + f.size, 0);
    if (total > 200 * 1024 * 1024) {
      toast("That's over 200 MB. Upload fewer files at a time.", true);
      return;
    }
    const form = new FormData();
    files.forEach((f) => form.append("file", f, f.name));

    const bar = $("progressBar");
    const text = $("progressText");
    $("progress").hidden = false;
    bar.style.width = "0%";
    text.textContent = "Uploading " + files.length + " file" + (files.length > 1 ? "s" : "") + "\u2026";

    const xhr = new XMLHttpRequest();
    xhr.open("POST", apiUrl("/api/upload", { path: cwd }));
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((e.loaded / e.total) * 100);
      bar.style.width = pct + "%";
      text.textContent = pct + "%  (" + fmtSize(e.loaded) + " of " + fmtSize(e.total) + ")";
    };
    xhr.onload = () => {
      $("progress").hidden = true;
      if (xhr.status === 401) return onUnauthorized();
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (e) { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        toast("Uploaded " + data.saved.map((s) => s.name).join(", "));
      } else {
        toast("Upload failed: " + (data.error || "HTTP " + xhr.status), true);
      }
      loadFiles(cwd);
    };
    xhr.onerror = () => {
      $("progress").hidden = true;
      toast("Upload failed: lost connection to the phone", true);
    };
    xhr.send(form);
  }

  $("picker").addEventListener("change", (e) => {
    upload(e.target.files);
    e.target.value = "";
  });

  const drop = $("drop");
  let depth = 0;
  const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
  drop.addEventListener("dragenter", (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth++; drop.classList.add("over"); });
  drop.addEventListener("dragover", (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
  drop.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) drop.classList.remove("over"); });
  drop.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    drop.classList.remove("over");
    upload(e.dataTransfer.files);
  });
  // don't let a missed drop open the file in the tab
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());

  // ---------- clipboard ----------
  function markSynced(what) {
    $("synced").textContent = what + " at " + new Date().toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit" });
  }

  $("pull").addEventListener("click", async () => {
    try {
      const data = await api("/api/clipboard");
      $("clipText").value = data.text;
      markSynced("Pulled");
    } catch (e) {
      fail(e);
    }
  });

  $("push").addEventListener("click", async () => {
    try {
      await api("/api/clipboard", { method: "POST", json: { text: $("clipText").value } });
      markSynced("Pushed");
      toast("Phone clipboard updated");
    } catch (e) {
      fail(e);
    }
  });

  // ---------- startup ----------
  $("refresh").addEventListener("click", () => connect());

  async function connect() {
    if (!token) {
      setStatus(false);
      showTokenForm(true);
      return;
    }
    showTokenForm(false);
    try {
      const ip = await api("/api/ip");
      $("url").textContent = ip.url;
      setStatus(true);
    } catch (e) {
      setStatus(false);
      fail(e);
      return;
    }
    loadFiles(cwd);
  }

  $("url").textContent = location.origin;
  renderCrumbs();
  connect();
})();
