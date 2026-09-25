/* oddlyunspecific — writer console.
 *
 * No framework, no build step, no dependency: one file the CSP can allow by
 * name (script-src 'self'). Everything it talks to is /write/api/*, same
 * origin, session-authenticated, CSRF token from the page's meta tag — the
 * token lives in the session server-side, so there is no cookie to read here.
 */
(function () {
  "use strict";

  var CSRF = document.querySelector('meta[name="csrf-token"]').content;
  var USER = document.querySelector('meta[name="boot-user"]').content;
  var ADMIN = document.querySelector('meta[name="admin-path"]').content;

  var els = {
    title: document.getElementById("title"),
    body: document.getElementById("body"),
    cmd: document.getElementById("cmd"),
    log: document.getElementById("log"),
    bufname: document.getElementById("bufname"),
    bufstate: document.getElementById("bufstate"),
    dirty: document.getElementById("dirty"),
    side: document.getElementById("side"),
    sidetitle: document.getElementById("sidetitle"),
    sidebody: document.getElementById("sidebody"),
    sideclose: document.getElementById("sideclose"),
    previewtab: document.getElementById("previewtab"),
    home: document.getElementById("home")
  };

  /* Label the shortcuts the way this keyboard actually spells them. */
  var MOD = /Mac|iPhone|iPad|iPod/.test(
    (navigator.userAgentData && navigator.userAgentData.platform) ||
    navigator.platform || navigator.userAgent
  ) ? "⌘" : "ctrl+";

  /* Current buffer. kind is "post" or "journal"; null means nothing open. */
  var buf = null;
  var dirty = false;
  var history = [];
  var historyIndex = 0;

  /* ---------------------------------------------------------------- output */

  function esc(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function line(text, kind) {
    var el = document.createElement("div");
    el.className = "log__line log__line--" + (kind || "ok");
    el.innerHTML = text;
    els.log.appendChild(el);
    els.log.scrollTop = els.log.scrollHeight;
    return el;
  }

  function say(text, kind) { return line(esc(text), kind); }
  function ok(text) { return say(text, "good"); }
  function warn(text) { return say(text, "warn"); }
  function err(text) { return say(text, "err"); }

  function table(rows) {
    var html = "<table>";
    rows.forEach(function (row) {
      html += "<tr>";
      row.forEach(function (cell) { html += "<td>" + esc(cell) + "</td>"; });
      html += "</tr>";
    });
    line(html + "</table>", "ok");
  }

  /* ------------------------------------------------------------------- api */

  function api(path, options) {
    options = options || {};
    var init = {
      method: options.method || "GET",
      headers: { "X-CSRFToken": CSRF, "X-Requested-With": "fetch" },
      credentials: "same-origin"
    };
    if (options.json !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.json);
    }
    if (options.form) { init.body = options.form; }

    return fetch("/write/api/" + path, init).then(function (response) {
      if (response.status === 403 || response.redirected) {
        throw new Error("session expired — reload and authenticate again");
      }
      return response.json().catch(function () {
        throw new Error("server returned " + response.status);
      }).then(function (data) {
        if (!response.ok || data.ok === false) {
          throw new Error(data.error || "request failed (" + response.status + ")");
        }
        return data;
      });
    });
  }

  function fail(error) { err("! " + error.message); }

  /* ---------------------------------------------------------------- buffer */

  function setDirty(value) {
    dirty = value;
    els.dirty.hidden = !value;
  }

  function paint() {
    if (!buf) {
      els.bufname.textContent = "— no buffer —";
      els.bufstate.textContent = "";
      els.bufstate.removeAttribute("data-status");
      els.title.value = "";
      els.body.value = "";
      els.title.disabled = els.body.disabled = true;
      setDirty(false);
      closePreview();
      return;
    }
    els.title.disabled = els.body.disabled = false;
    els.title.value = buf.title || "";
    els.body.value = buf.body || "";
    els.bufname.textContent =
      (buf.kind === "journal" ? "private/" : "posts/") + (buf.slug || buf.id);
    var status = buf.kind === "journal" ? "private" : buf.status;
    els.bufstate.textContent = "[" + status + "]";
    els.bufstate.setAttribute("data-status", status);
    setDirty(false);
    // A different buffer was loaded: refresh the pane if it is open.
    schedulePreview();
  }

  function requireBuffer() {
    if (!buf) { warn("no buffer open — `new`, `open <id>` or `ls`"); return false; }
    return true;
  }

  function pull() {
    if (!buf) { return; }
    buf.title = els.title.value;
    buf.body = els.body.value;
  }

  function save() {
    if (!requireBuffer()) { return Promise.resolve(); }
    pull();
    var path = buf.kind === "journal" ? "journal/" + buf.id + "/" : "posts/" + buf.id + "/";
    var payload = buf.kind === "journal"
      ? { title: buf.title, body: buf.body }
      : { title: buf.title, body: buf.body, slug: buf.slug, summary: buf.summary };
    return api(path, { method: "PUT", json: payload }).then(function (data) {
      var record = data.post || data.entry;
      buf = Object.assign(buf, record);
      paint();
      ok("saved · " + (record.words !== undefined ? record.words + " words" : record.entry_date));
    }).catch(fail);
  }

  /* -------------------------------------------------------------- commands */

  var commands = {};

  function define(names, help, run) {
    names.forEach(function (name, index) {
      commands[name] = { run: run, help: index === 0 ? help : null, name: name };
    });
  }

  define(["help", "?"], "this list", function () {
    var rows = [];
    Object.keys(commands).forEach(function (key) {
      if (commands[key].help) { rows.push([key, commands[key].help]); }
    });
    table(rows);
    line("", "ok");
    table([
      [MOD + "s", "save"],
      [MOD + "enter", "save and publish"],
      [MOD + "p", "toggle the live preview"],
      [MOD + "k", "focus the command line"],
      ["esc", "move between command line and editor"],
      [MOD + "b", "bold — wraps the selection, press again to remove"],
      [MOD + "i", "italic"],
      [MOD + "e", "inline code"],
      ["drop / paste", "upload an image and insert it at the cursor"],
      ["preview ⤢ icon", "tap an image's corner icon to cycle its size — or `resize <px>`"]
    ]);
  });

  define(["ls"], "list posts — `ls drafts` / `ls published`", function (args) {
    var query = "";
    if (args[0] === "drafts") { query = "?status=draft"; }
    if (args[0] === "published") { query = "?status=published"; }
    return api("posts/" + query).then(function (data) {
      if (!data.posts.length) { return warn("no posts"); }
      table(data.posts.map(function (post) {
        return [
          "#" + post.id,
          post.status === "published" ? "pub" : "drf",
          (post.published_at || "").slice(0, 10) || "—",
          post.title
        ];
      }));
    }).catch(fail);
  });

  define(["new"], "new draft — `new <title>`", function (args) {
    return api("posts/", {
      method: "POST",
      json: { title: args.join(" ") || "untitled", body: "" }
    }).then(function (data) {
      buf = Object.assign({ kind: "post" }, data.post);
      paint();
      els.body.focus();
      ok("created #" + buf.id + " · " + buf.slug);
    }).catch(fail);
  });

  define(["open", "o"], "load a post — `open <id>`", function (args) {
    if (!args[0]) { return warn("open what? `ls` first"); }
    return api("posts/" + encodeURIComponent(args[0]) + "/").then(function (data) {
      buf = Object.assign({ kind: "post" }, data.post);
      paint();
      els.body.focus();
      ok("opened #" + buf.id);
    }).catch(fail);
  });

  define(["w", "save"], "save the buffer", save);

  define(["pub", "publish"], "publish the open post", function () {
    if (!requireBuffer()) { return; }
    if (buf.kind === "journal") { return warn("journal entries are never published"); }
    return save().then(function () {
      return api("posts/" + buf.id + "/publish/", { method: "POST" });
    }).then(function (data) {
      buf = Object.assign(buf, data.post);
      paint();
      ok("published → " + data.post.url);
    }).catch(fail);
  });

  define(["unpub", "unpublish"], "move the open post back to draft", function () {
    if (!requireBuffer() || buf.kind === "journal") { return; }
    return api("posts/" + buf.id + "/unpublish/", { method: "POST" }).then(function (data) {
      buf = Object.assign(buf, data.post);
      paint();
      warn("unpublished — it is a draft again");
    }).catch(fail);
  });

  define(["slug"], "set the URL slug — `slug my-post`", function (args) {
    if (!requireBuffer() || buf.kind === "journal") { return; }
    if (!args[0]) { return say("slug: " + buf.slug); }
    buf.slug = args[0];
    setDirty(true);
    say("slug → " + buf.slug + " (unsaved)");
  });

  define(["summary"], "set the feed summary — `summary <text>`", function (args) {
    if (!requireBuffer() || buf.kind === "journal") { return; }
    buf.summary = args.join(" ");
    setDirty(true);
    say("summary set (unsaved)");
  });

  define(["resize", "img"], "resize the image at the cursor — `resize 480` / `resize full`",
    function (args) {
      if (!requireBuffer()) { return; }
      var arg = (args[0] || "").toLowerCase();
      var width = null;
      if (arg && arg !== "full" && arg !== "orig" && arg !== "0") {
        width = parseInt(args[0], 10);
        if (!(width > 0)) { return warn("width must be a positive number of px, or `full`"); }
      }
      if (!setImageWidth(width)) {
        return warn("no image at the cursor — put the caret on an image line first");
      }
      ok(width ? "width → " + width + "px" : "width cleared — natural size");
    });

  define(["tags"], "set tags — `tags rust, notes`", function (args) {
    if (!requireBuffer() || buf.kind === "journal") { return; }
    var names = args.join(" ").split(",").map(function (s) { return s.trim(); })
      .filter(Boolean);
    return api("posts/" + buf.id + "/", { method: "PUT", json: { tags: names } })
      .then(function (data) {
        buf = Object.assign(buf, data.post);
        ok("tags · " + (buf.tags.join(", ") || "none"));
      }).catch(fail);
  });

  define(["rm"], "delete a post — `rm <id> !`", function (args) {
    if (args[1] !== "!") { return warn("add ! to confirm: `rm " + (args[0] || "<id>") + " !`"); }
    return api("posts/" + encodeURIComponent(args[0]) + "/", { method: "DELETE" })
      .then(function () {
        if (buf && buf.kind === "post" && String(buf.id) === String(args[0])) {
          buf = null; paint();
        }
        warn("deleted #" + args[0]);
      }).catch(fail);
  });

  /* Journal lives behind `j <subcommand>` so nothing private is ever one
     mistyped character away from a public command. */
  define(["j"], "journal — `j ls | j new | j open <id> | j rm <id> !`", function (args) {
    var sub = args.shift();
    if (sub === "ls" || sub === undefined) {
      return api("journal/").then(function (data) {
        if (!data.entries.length) { return warn("journal is empty"); }
        table(data.entries.map(function (entry) {
          return ["#" + entry.id, entry.pinned ? "★" : " ", entry.entry_date, entry.title];
        }));
      }).catch(fail);
    }
    if (sub === "new") {
      return api("journal/", { method: "POST", json: { title: args.join(" "), body: "" } })
        .then(function (data) {
          buf = Object.assign({ kind: "journal" }, data.entry);
          paint();
          els.body.focus();
          ok("private entry #" + buf.id + " · encrypted at rest");
        }).catch(fail);
    }
    if (sub === "open" || sub === "o") {
      return api("journal/" + encodeURIComponent(args[0]) + "/").then(function (data) {
        buf = Object.assign({ kind: "journal" }, data.entry);
        paint();
        els.body.focus();
        ok("opened private #" + buf.id);
      }).catch(fail);
    }
    if (sub === "pin") {
      if (!requireBuffer() || buf.kind !== "journal") { return warn("open an entry first"); }
      return api("journal/" + buf.id + "/", { method: "PUT", json: { pinned: !buf.pinned } })
        .then(function (data) {
          buf = Object.assign(buf, data.entry);
          ok(buf.pinned ? "pinned" : "unpinned");
        }).catch(fail);
    }
    if (sub === "date") {
      if (!requireBuffer() || buf.kind !== "journal") { return warn("open an entry first"); }
      return api("journal/" + buf.id + "/", { method: "PUT", json: { entry_date: args[0] } })
        .then(function (data) { buf = Object.assign(buf, data.entry); ok("date → " + buf.entry_date); })
        .catch(fail);
    }
    if (sub === "rm") {
      if (args[1] !== "!") { return warn("add ! to confirm: `j rm " + (args[0] || "<id>") + " !`"); }
      return api("journal/" + encodeURIComponent(args[0]) + "/", { method: "DELETE" })
        .then(function () {
          if (buf && buf.kind === "journal" && String(buf.id) === String(args[0])) {
            buf = null; paint();
          }
          warn("deleted private #" + args[0]);
        }).catch(fail);
    }
    warn("unknown: j " + sub);
  });

  define(["prev", "preview"], "toggle the live preview pane", function () {
    if (!els.side.hidden) {
      closePreview();
      return;
    }
    if (!requireBuffer()) { return; }
    return renderPreview().catch(fail);
  });

  define(["view"], "open the live page in a new tab", function () {
    if (!requireBuffer()) { return; }
    window.open(buf.url, "_blank", "noopener");
  });

  define(["stat"], "counts", function () {
    return api("state/").then(function (data) {
      table([
        ["user", data.user],
        ["posts", String(data.posts)],
        ["drafts", String(data.drafts)],
        ["private", String(data.entries)],
        ["now", data.now]
      ]);
    }).catch(fail);
  });

  define(["admin"], "open the Django admin", function () {
    window.open(ADMIN, "_blank", "noopener");
  });

  define(["close"], "close the buffer without saving", function () {
    buf = null; paint(); say("buffer closed");
  });

  define(["clear"], "clear this log", function () { els.log.innerHTML = ""; });

  define(["whoami"], "who is authenticated", function () {
    say(USER + " · staff · second factor verified");
  });

  define(["logout"], "end the session", function () {
    var form = document.createElement("form");
    form.method = "post";
    form.action = "/write/logout/";
    var field = document.createElement("input");
    field.type = "hidden"; field.name = "csrfmiddlewaretoken"; field.value = CSRF;
    form.appendChild(field);
    document.body.appendChild(form);
    form.submit();
  });

  /* ------------------------------------------------------------- dispatch */

  function run(input) {
    var text = input.trim();
    if (!text) { return; }
    line("▸ " + esc(text), "in");
    history.push(text);
    historyIndex = history.length;

    var parts = text.split(/\s+/);
    var name = parts.shift();
    var command = commands[name];
    if (!command) { return warn("unknown command: " + name + " — try `help`"); }
    try {
      command.run(parts);
    } catch (error) {
      err("! " + error.message);
    }
  }

  /* ---------------------------------------------------------------- upload */

  function upload(file) {
    var form = new FormData();
    form.append("file", file);
    var pending = say("uploading " + file.name + "…");
    return api("upload/", { method: "POST", form: form }).then(function (data) {
      pending.remove();
      insertAtCursor("\n![](" + data.url + ")\n");
      ok("inserted " + data.url + " · resize with `resize <px>`, e.g. resize 480");
    }).catch(function (error) { pending.remove(); fail(error); });
  }

  /* ---------------------------------------------------------------- preview */

  var previewTimer = null;

  function renderPreview() {
    if (!buf) { return Promise.resolve(); }
    pull();
    return api("preview/", { method: "POST", json: { body: buf.body } })
      .then(function (data) {
        els.sidetitle.textContent = "preview · live";
        /* Server-rendered and already through the same sanitiser the stored
           HTML goes through, so what is shown here is exactly what would be
           published — bypasses included, which is the point of previewing. */
        var scrolled = els.sidebody.scrollTop;
        els.sidebody.innerHTML = data.html;
        // Number the images in render order (so a drag maps an <img> back to
        // its markdown token by position), turn OFF the browser's native image
        // drag — which otherwise drags the file to Finder — and give each a
        // corner handle to grab for resizing on both mouse and touch.
        var imgs = els.sidebody.querySelectorAll("img");
        for (var i = 0; i < imgs.length; i++) {
          var pimg = imgs[i];
          pimg.dataset.imageIndex = i;
          pimg.draggable = false;
          var wrap = document.createElement("span");
          wrap.className = "imgwrap";
          pimg.parentNode.insertBefore(wrap, pimg);
          wrap.appendChild(pimg);
          var handle = document.createElement("button");
          handle.type = "button";
          handle.className = "imghandle";
          handle.setAttribute("aria-label", "resize the image");
          handle.title = "resize (full → 66% → 50% → 33%)";
          handle.dataset.imageIndex = i;
          // Two diagonal expand arrows. Inline SVG (no script) — CSP-safe.
          handle.innerHTML =
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
            '<polyline points="14 4 20 4 20 10"></polyline>' +
            '<polyline points="10 20 4 20 4 14"></polyline>' +
            '<line x1="20" y1="4" x2="13" y2="11"></line>' +
            '<line x1="4" y1="20" x2="11" y2="13"></line></svg>';
          wrap.appendChild(handle);
        }
        // Re-rendering replaces the whole subtree; without this the pane jumps
        // back to the top on every keystroke.
        els.sidebody.scrollTop = scrolled;
        els.side.hidden = false;
        syncPreviewTab();
      });
  }

  function closePreview() {
    clearTimeout(previewTimer);
    els.side.hidden = true;
    syncPreviewTab();
  }

  /* The tab and the pane are mutually exclusive: whichever is not showing, the
   * other one is. */
  function syncPreviewTab() {
    els.previewtab.hidden = !els.side.hidden;
  }

  /* Debounced: one render per pause in typing, not one per keystroke. The
   * markdown is rendered server-side, so each refresh is a round trip. */
  function schedulePreview() {
    if (els.side.hidden) { return; }
    clearTimeout(previewTimer);
    previewTimer = setTimeout(function () {
      renderPreview().catch(function () { /* keep typing; the log stays quiet */ });
    }, 400);
  }

  /* The buffer changed. setRangeText and direct .value writes do not fire an
   * input event, so every mutation path calls this instead. */
  function touched() {
    setDirty(true);
    schedulePreview();
  }

  /* ------------------------------------------------------------- formatting */

  /* Toggle a markdown marker around the selection. Handles both shapes the
   * markers can take after an earlier toggle: inside the selection (**sel**
   * all highlighted) and outside it (sel highlighted, ** sitting either side).
   * setRangeText is used rather than rewriting .value so the browser's own
   * undo stack survives — cmd+z still works. */
  function toggleWrap(marker) {
    var field = els.body;
    var start = field.selectionStart;
    var end = field.selectionEnd;
    var value = field.value;
    var selected = value.slice(start, end);
    var len = marker.length;

    var wrappedInside =
      selected.length >= len * 2 &&
      selected.slice(0, len) === marker &&
      selected.slice(-len) === marker;
    var wrappedOutside =
      value.slice(start - len, start) === marker &&
      value.slice(end, end + len) === marker;

    if (wrappedInside) {
      field.setRangeText(selected.slice(len, selected.length - len), start, end, "select");
    } else if (wrappedOutside) {
      field.setRangeText(selected, start - len, end + len, "select");
    } else {
      field.setRangeText(marker + selected + marker, start, end, "select");
      if (start === end) {
        // Nothing was selected: drop the caret between the two markers so the
        // next keystroke lands inside them.
        field.selectionStart = field.selectionEnd = start + len;
      }
    }
    touched();
  }

  /* Resize by setting a display width through the attr_list syntax the renderer
   * already understands: ![alt](url){: width=480 }. The stored file is never
   * touched — one canonical image, scaled by the browser, still capped at the
   * column by max-width:100%. A null width strips the attribute back to the
   * image's natural size. Two entry points share the same rewrite: the `resize`
   * command (the image at the caret) and a drag in the preview (the Nth image).
   */
  var IMAGE_RE = "!\\[[^\\]]*\\]\\([^)]*\\)(\\s*\\{:?[^}]*\\})?";

  function imageTokens(value) {
    var out = [], m, re = new RegExp(IMAGE_RE, "g");
    while ((m = re.exec(value))) {
      out.push({ start: m.index, end: m.index + m[0].length, whole: m[0], block: m[1] || "" });
    }
    return out;
  }

  function rebuildToken(tok, width) {
    // base = ![alt](url) without any trailing attr_list block.
    var base = tok.block ? tok.whole.slice(0, tok.whole.length - tok.block.length) : tok.whole;
    var inner = tok.block.replace(/^\s*\{:?\s*/, "").replace(/\s*\}\s*$/, "");
    var parts = inner ? inner.split(/\s+/) : [];
    parts = parts.filter(function (t) { return !/^width=/.test(t); });
    if (width) { parts.push("width=" + width); }
    return parts.length ? base + "{: " + parts.join(" ") + " }" : base;
  }

  function writeToken(tok, width) {
    // setRangeText keeps the browser's own undo stack, so cmd+z still works;
    // "preserve" leaves the caret where it was — the drag never touches it.
    els.body.setRangeText(rebuildToken(tok, width), tok.start, tok.end, "preserve");
    touched();
  }

  /* Command path: the image at, or nearest above, the caret. */
  function setImageWidth(width) {
    if (els.body.disabled) { return false; }
    var caret = els.body.selectionStart;
    var toks = imageTokens(els.body.value);
    var target = null;
    for (var i = 0; i < toks.length; i++) {
      if (toks[i].start <= caret) { target = toks[i]; } else { break; }
    }
    if (!target) { return false; }
    writeToken(target, width);
    return true;
  }


  function insertAtCursor(text) {
    var field = els.body;
    if (field.disabled) { return; }
    var start = field.selectionStart;
    var end = field.selectionEnd;
    field.value = field.value.slice(0, start) + text + field.value.slice(end);
    field.selectionStart = field.selectionEnd = start + text.length;
    touched();
    field.focus();
  }

  /* ----------------------------------------------------------------- wire */

  els.cmd.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      run(els.cmd.value);
      els.cmd.value = "";
      event.preventDefault();
    } else if (event.key === "ArrowUp") {
      if (historyIndex > 0) { historyIndex -= 1; els.cmd.value = history[historyIndex]; }
      event.preventDefault();
    } else if (event.key === "ArrowDown") {
      historyIndex = Math.min(historyIndex + 1, history.length);
      els.cmd.value = history[historyIndex] || "";
      event.preventDefault();
    } else if (event.key === "Escape") {
      els.body.focus();
      event.preventDefault();
    } else if (event.key === "Tab") {
      var prefix = els.cmd.value;
      var matches = Object.keys(commands).filter(function (key) {
        return key.indexOf(prefix) === 0;
      });
      if (matches.length === 1) { els.cmd.value = matches[0] + " "; }
      else if (matches.length > 1) { say(matches.join("  ")); }
      event.preventDefault();
    }
  });

  [els.title, els.body].forEach(function (field) {
    field.addEventListener("input", function () { touched(); });
    field.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { els.cmd.focus(); event.preventDefault(); }
    });
  });

  var WRAPPERS = { b: "**", i: "*", e: "`" };

  document.addEventListener("keydown", function (event) {
    var meta = event.ctrlKey || event.metaKey;
    if (!meta) { return; }

    // Formatting only applies in the body, and only swallows the browser's own
    // shortcut when it is actually going to do something.
    if (WRAPPERS[event.key] && document.activeElement === els.body && !els.body.disabled) {
      event.preventDefault();
      toggleWrap(WRAPPERS[event.key]);
      return;
    }

    if (event.key === "s") { event.preventDefault(); save(); }
    else if (event.key === "k") { event.preventDefault(); els.cmd.focus(); }
    else if (event.key === "p") { event.preventDefault(); run("preview"); }
    else if (event.key === "Enter") { event.preventDefault(); run("pub"); }
  });

  els.sideclose.addEventListener("click", closePreview);
  els.previewtab.addEventListener("click", function () { run("preview"); });

  /* Click-to-resize in the preview. Each image carries a corner button; a click
   * or tap cycles its width through a ladder of fractions of the image's own
   * natural size — full → 66% → 50% → 33% → full. Same gesture on mouse and
   * touch, no dragging, and never upscaled past the original. */
  var LADDER = [1, 0.66, 0.5, 0.33];

  function tokenWidth(tok) {
    var m = /(?:^|\s)width=(\d+)/.exec(tok.block || "");
    return m ? parseInt(m[1], 10) : null;
  }

  // Belt to the draggable=false brace: never let an image start a native drag.
  els.sidebody.addEventListener("dragstart", function (event) {
    if (event.target && event.target.tagName === "IMG") { event.preventDefault(); }
  });

  els.sidebody.addEventListener("click", function (event) {
    var handle = event.target.closest && event.target.closest(".imghandle");
    if (!handle || !buf) { return; }
    event.preventDefault();
    var img = handle.parentNode.querySelector("img");
    if (!img) { return; }
    var toks = imageTokens(els.body.value);
    var idx = parseInt(handle.dataset.imageIndex, 10);
    if (idx < 0 || idx >= toks.length) { return; }

    var natural = img.naturalWidth || Math.round(img.getBoundingClientRect().width);
    var frac = (tokenWidth(toks[idx]) || natural) / natural;
    // Snap the current width to the nearest ladder rung, then step to the next.
    var at = 0, best = Infinity;
    for (var k = 0; k < LADDER.length; k++) {
      var d = Math.abs(LADDER[k] - frac);
      if (d < best) { best = d; at = k; }
    }
    var next = LADDER[(at + 1) % LADDER.length];
    var width = next >= 0.999 ? null : Math.round(natural * next);
    writeToken(toks[idx], width);
    ok(width ? "width → " + Math.round(next * 100) + "% (" + width + "px)" : "width → full");
  });

  /* The logo doubles as "back to the site". Leaving with unsaved work would
   * lose it, so save first and only follow the link once the save has landed —
   * if it fails (e.g. the session expired) we stay put and say so. */
  els.home.addEventListener("click", function (event) {
    if (!dirty || !buf) { return; }        // clean buffer: let the link navigate
    event.preventDefault();
    save().then(function () {
      if (dirty) { warn("save failed — staying so nothing is lost"); return; }
      window.location.href = els.home.getAttribute("href");
    });
  });

  els.body.addEventListener("dragover", function (event) {
    event.preventDefault();
    els.body.classList.add("dropping");
  });
  els.body.addEventListener("dragleave", function () {
    els.body.classList.remove("dropping");
  });
  els.body.addEventListener("drop", function (event) {
    event.preventDefault();
    els.body.classList.remove("dropping");
    var files = Array.prototype.slice.call(event.dataTransfer.files);
    files.filter(function (file) { return file.type.indexOf("image/") === 0; })
      .forEach(upload);
  });
  els.body.addEventListener("paste", function (event) {
    var items = Array.prototype.slice.call(event.clipboardData.items);
    var images = items.filter(function (item) { return item.type.indexOf("image/") === 0; });
    if (!images.length) { return; }
    event.preventDefault();
    images.forEach(function (item) { upload(item.getAsFile()); });
  });

  window.addEventListener("beforeunload", function (event) {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  });

  /* Autosave: a writing tool that loses a paragraph is not a writing tool. */
  setInterval(function () { if (dirty && buf) { save(); } }, 20000);

  /* ------------------------------------------------------------------ boot */

  els.previewtab.textContent = MOD + "p  preview";
  syncPreviewTab();
  paint();
  line("<span class=\"log__line--good\">" + esc(document.title) + "</span>", "ok");
  say(USER + " authenticated · second factor verified · session is https-only");
  say("`help` for commands · `ls` for posts · `j ls` for the private journal");
  els.cmd.focus();
})();
