const MINE = "__mine__";

const userSelect = document.getElementById("user-select");
const syncBtn = document.getElementById("sync-btn");
const stopBtn = document.getElementById("stop-btn");
const pullBtn = document.getElementById("pull-btn");
const logoutBtn = document.getElementById("logout-btn");
const logsBtn = document.getElementById("logs-btn");
const statusTextEl = document.getElementById("status-text");
const statusIconEl = document.getElementById("status-icon");
const progressEl = document.getElementById("sync-progress");
const myPlaylistsView = document.getElementById("my-playlists-view");
const myPlaylistsList = document.getElementById("my-playlists-list");
const peerView = document.getElementById("peer-view");
const playlistsList = document.getElementById("playlists-list");
const tracksHeading = document.getElementById("tracks-heading");
const tracksTableBody = document.querySelector("#tracks-table tbody");
const player = document.getElementById("player");
const volumeSlider = document.getElementById("volume-slider");
const handleInput = document.getElementById("handle-input");

// WebView2's native <audio controls> volume slider is effectively just a mute toggle
// on this platform - no gradual adjustment - so this slider drives volume directly
// instead of relying on it.
player.volume = Number(volumeSlider.value);
volumeSlider.addEventListener("input", () => {
  player.volume = Number(volumeSlider.value);
});

// icon: "idle" | "syncing" | "waiting" | "done" | "error"
function setStatus(text, icon = "idle") {
  statusTextEl.textContent = text;
  statusIconEl.className = icon;
}

function formatDuration(seconds) {
  if (seconds == null) return "";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

async function loadUsers() {
  const users = await fetch("/api/users").then((r) => r.json());
  const previous = userSelect.value;
  userSelect.innerHTML = "";

  const mineOpt = document.createElement("option");
  mineOpt.value = MINE;
  mineOpt.textContent = "My Playlists";
  userSelect.appendChild(mineOpt);

  for (const user of users) {
    if (user.is_self) continue;
    const opt = document.createElement("option");
    opt.value = user.id;
    opt.textContent = user.handle;
    userSelect.appendChild(opt);
  }

  if ([...userSelect.options].some((o) => o.value === previous)) {
    userSelect.value = previous;
  }
}

let myPlaylistsData = [];
let activeMyPlaylistsTab = "synced";

const tabButtons = document.querySelectorAll(".tab-btn");
for (const btn of tabButtons) {
  btn.addEventListener("click", () => {
    activeMyPlaylistsTab = btn.dataset.tab;
    renderMyPlaylists();
  });
}

const rekordboxErrorEl = document.getElementById("rekordbox-error");
const rekordboxErrorMessageEl = document.getElementById("rekordbox-error-message");
const browseRekordboxBtn = document.getElementById("browse-rekordbox-btn");
const myPlaylistsTabsEl = document.querySelector("#my-playlists-view .tabs");

async function loadMyPlaylists() {
  myPlaylistsView.hidden = false;
  peerView.hidden = true;

  const response = await fetch("/api/my-playlists");
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail || {};
    rekordboxErrorMessageEl.textContent =
      detail.error_code === "DbNotFound"
        ? "Couldn't automatically find your Rekordbox library."
        : `Couldn't read your Rekordbox library: ${detail.message || "unknown error"}`;
    rekordboxErrorEl.hidden = false;
    myPlaylistsTabsEl.hidden = true;
    myPlaylistsList.innerHTML = "";
    return;
  }

  rekordboxErrorEl.hidden = true;
  myPlaylistsTabsEl.hidden = false;
  myPlaylistsData = await response.json();
  renderMyPlaylists();
}

browseRekordboxBtn.addEventListener("click", async () => {
  browseRekordboxBtn.disabled = true;
  try {
    const result = await fetch("/api/settings/browse-rekordbox-db", { method: "POST" }).then((r) => r.json());
    if (result.rekordbox_db_path) {
      await loadMyPlaylists();
    }
  } catch (err) {
    setStatus("Failed to open file picker - click Logs for details.", "error");
  } finally {
    browseRekordboxBtn.disabled = false;
  }
});

function renderMyPlaylists() {
  const synced = myPlaylistsData.filter((p) => p.whitelisted);
  const unsynced = myPlaylistsData.filter((p) => !p.whitelisted);

  document.getElementById("synced-count").textContent = synced.length;
  document.getElementById("unsynced-count").textContent = unsynced.length;
  for (const btn of tabButtons) {
    btn.classList.toggle("active", btn.dataset.tab === activeMyPlaylistsTab);
  }

  const playlists = activeMyPlaylistsTab === "synced" ? synced : unsynced;
  myPlaylistsList.innerHTML = "";

  if (playlists.length === 0) {
    const li = document.createElement("li");
    li.className = "empty-hint";
    li.textContent = activeMyPlaylistsTab === "synced" ? "Nothing synced yet." : "Everything is synced.";
    myPlaylistsList.appendChild(li);
    return;
  }

  for (const playlist of playlists) {
    const li = document.createElement("li");
    const label = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = playlist.whitelisted;
    checkbox.addEventListener("change", async () => {
      checkbox.disabled = true;
      await fetch(`/api/whitelist/${playlist.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ whitelisted: checkbox.checked }),
      });
      scheduleAutoSync();
      // Refresh immediately so the playlist moves to the other tab right away,
      // rather than waiting for the debounced sync to finish - that's the point of
      // splitting synced/unsynced into tabs, to get a toggled item out of the way.
      await loadMyPlaylists();
    });
    const span = document.createElement("span");
    span.textContent = playlist.name;
    label.appendChild(checkbox);
    label.appendChild(span);
    li.appendChild(label);

    const count = document.createElement("span");
    count.className = "track-count";
    count.textContent = `${playlist.track_count} tracks`;
    li.appendChild(count);

    myPlaylistsList.appendChild(li);
  }
}

async function loadPeerPlaylists(userId) {
  myPlaylistsView.hidden = true;
  peerView.hidden = false;
  playlistsList.innerHTML = "<li>Loading...</li>";
  tracksHeading.textContent = "Select a playlist";
  tracksTableBody.innerHTML = "";

  const playlists = await fetch(`/api/users/${userId}/playlists`).then((r) => r.json());
  playlistsList.innerHTML = "";
  for (const playlist of playlists) {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = playlist.parent_name ? `${playlist.parent_name} / ${playlist.name}` : playlist.name;
    li.appendChild(label);
    const count = document.createElement("span");
    count.className = "track-count";
    count.textContent = `${playlist.track_count} tracks`;
    li.appendChild(count);

    li.addEventListener("click", () => {
      for (const other of playlistsList.children) other.classList.remove("selected");
      li.classList.add("selected");
      loadTracks(userId, playlist.id, playlist.name);
    });
    playlistsList.appendChild(li);
  }
}

async function loadTracks(userId, playlistId, playlistName) {
  tracksHeading.textContent = playlistName;
  tracksTableBody.innerHTML = "<tr><td colspan=\"6\">Loading...</td></tr>";

  const tracks = await fetch(`/api/users/${userId}/playlists/${playlistId}/tracks`).then((r) => r.json());
  tracksTableBody.innerHTML = "";
  for (const track of tracks) {
    const row = document.createElement("tr");

    const playCell = document.createElement("td");
    const playBtn = document.createElement("button");
    playBtn.className = "play-btn";
    playBtn.textContent = "▶";
    playBtn.addEventListener("click", () => {
      player.src = `/api/preview/${userId}/${track.id}`;
      player.play();
    });
    playCell.appendChild(playBtn);
    row.appendChild(playCell);

    for (const value of [track.title, track.artist, track.bpm ?? "", track.key ?? "", formatDuration(track.duration_sec)]) {
      const cell = document.createElement("td");
      cell.textContent = value ?? "";
      row.appendChild(cell);
    }
    tracksTableBody.appendChild(row);
  }
}

function setMyPlaylistsInteractive(interactive) {
  for (const checkbox of myPlaylistsList.querySelectorAll("input[type=checkbox]")) {
    checkbox.disabled = !interactive;
  }
}

function renderView() {
  if (userSelect.value === MINE) {
    loadMyPlaylists();
  } else {
    loadPeerPlaylists(userSelect.value);
  }
}

userSelect.addEventListener("change", renderView);

let syncInFlight = false;
let autoSyncTimer = null;
let statusPollTimer = null;

const AUTO_SYNC_DEBOUNCE_MS = 2000;
const STATUS_POLL_INTERVAL_MS = 400;

function startStatusPolling() {
  progressEl.hidden = false;
  progressEl.value = 0;
  clearInterval(statusPollTimer);
  statusPollTimer = setInterval(pollSyncStatus, STATUS_POLL_INTERVAL_MS);
  pollSyncStatus();
}

function stopStatusPolling() {
  clearInterval(statusPollTimer);
  statusPollTimer = null;
  progressEl.hidden = true;
}

async function pollSyncStatus() {
  const s = await fetch("/api/sync/status").then((r) => r.json());
  if (s.waiting_for_auth) {
    setStatus("Waiting for you to sign in to Google (check your browser)...", "waiting");
  } else if (s.total > 0) {
    progressEl.max = s.total;
    progressEl.value = s.done;
    setStatus(`Syncing ${s.done}/${s.total} tracks...`, "syncing");
  }
}

async function triggerSync() {
  if (syncInFlight) return;
  syncInFlight = true;
  syncBtn.disabled = true;
  stopBtn.disabled = false;
  setMyPlaylistsInteractive(false);
  setStatus("Syncing...", "syncing");
  startStatusPolling();
  try {
    const response = await fetch("/api/sync", { method: "POST" });
    if (!response.ok) throw new Error("sync failed");
    const result = await response.json();
    if (result.cancelled) {
      setStatus("Sync stopped.", "idle");
    } else {
      setStatus(`Synced ${result.tracks_published} tracks across ${result.whitelisted_playlists} playlists.`, "done");
    }
  } catch (err) {
    setStatus("Sync failed - click Logs for details.", "error");
  } finally {
    syncInFlight = false;
    syncBtn.disabled = false;
    stopBtn.disabled = true;
    stopStatusPolling();
    // loadMyPlaylists() re-renders fresh (enabled) checkboxes when applicable; the
    // explicit re-enable below only matters if the user switched to the peer view
    // mid-sync, since that view's re-render is skipped in that case.
    setMyPlaylistsInteractive(true);
    if (userSelect.value === MINE) loadMyPlaylists();
  }
}

function scheduleAutoSync() {
  clearTimeout(autoSyncTimer);
  autoSyncTimer = setTimeout(triggerSync, AUTO_SYNC_DEBOUNCE_MS);
}

syncBtn.addEventListener("click", () => {
  clearTimeout(autoSyncTimer);
  triggerSync();
});

stopBtn.addEventListener("click", () => {
  stopBtn.disabled = true;
  fetch("/api/sync/stop", { method: "POST" });
});

let pullInFlight = false;
const AUTO_PULL_INTERVAL_MS = 60000; // background refresh of peers while the window stays open

async function triggerPull() {
  if (pullInFlight) return;
  pullInFlight = true;
  pullBtn.disabled = true;
  setStatus("Pulling...", "syncing");
  try {
    const result = await fetch("/api/pull", { method: "POST" }).then((r) => r.json());
    setStatus(`Found ${result.users_found} users (${result.users_updated} updated).`, "done");
    await loadUsers();
    renderView();
  } catch (err) {
    setStatus("Pull failed - click Logs for details.", "error");
  } finally {
    pullInFlight = false;
    pullBtn.disabled = false;
  }
}

pullBtn.addEventListener("click", triggerPull);
setInterval(triggerPull, AUTO_PULL_INTERVAL_MS);

logsBtn.addEventListener("click", () => {
  fetch("/api/logs/open", { method: "POST" });
});

logoutBtn.addEventListener("click", async () => {
  logoutBtn.disabled = true;
  try {
    await fetch("/api/settings/logout", { method: "POST" });
    setStatus("Signed out of Google - next sync/pull will ask you to sign in again.", "idle");
  } catch (err) {
    setStatus("Failed to sign out - click Logs for details.", "error");
  } finally {
    logoutBtn.disabled = false;
  }
});

let currentHandle = "";

async function loadHandle() {
  const settings = await fetch("/api/settings").then((r) => r.json());
  currentHandle = settings.handle || "";
  handleInput.value = currentHandle;
}

async function commitHandleChange() {
  const newHandle = handleInput.value.trim();
  if (!newHandle || newHandle === currentHandle) {
    handleInput.value = currentHandle;
    return;
  }
  handleInput.disabled = true;
  setStatus("Updating handle...", "syncing");
  try {
    const response = await fetch("/api/settings/handle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ handle: newHandle }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "handle update failed");
    currentHandle = result.handle;
    handleInput.value = currentHandle;
    triggerSync();
  } catch (err) {
    setStatus(err.message || "Failed to update handle - click Logs for details.", "error");
    handleInput.value = currentHandle;
  } finally {
    handleInput.disabled = false;
  }
}

handleInput.addEventListener("blur", commitHandleChange);
handleInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    handleInput.blur();
  } else if (event.key === "Escape") {
    handleInput.value = currentHandle;
    handleInput.blur();
  }
});

(async function init() {
  await loadUsers();
  renderView();
  loadHandle();
  triggerPull(); // refresh peers as soon as the app opens, not just on the first interval tick
})();
