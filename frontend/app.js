const USE_MOCK = false;

// ─── Mock data ────────────────────────────────────────────────────────
const MOCK_RESULT = {
  taste_profile: {
    preferred_genres: ["Action RPG", "Roguelike", "Metroidvania"],
    preferred_mechanics: ["Deep progression", "Precision combat", "Atmospheric exploration"],
    vibes: ["Atmospheric", "Challenging", "Rewarding"],
    wildcard_picks: ["Precision platformer (mastery curve fits your aim-trainer mindset)", "Rhythm action game", "Tactical puzzle game"],
    summary: "You favour tightly-designed action games with meaningful progression and atmospheric worlds. Your completion rate in this genre is well above average, and you consistently gravitate toward challenging, rewarding experiences over casual or multiplayer titles.",
  },
  recommendations: [
    {
      rank: 1, score: 96,
      rationale: "Near-perfect alignment with your action RPG and open-world taste. The high Metacritic signal confirms a quality ceiling that matches your threshold. Your wishlist placement and playtime pattern in similar titles make this the clearest buy.",
      game: { appid: 1245620, name: "ELDEN RING", source: "wishlist", metacritic_score: 96, genres: ["Action", "RPG"], tags: ["Open World", "Souls-like", "Difficult", "Single-player"], playtime_minutes: 0, price_cents: 5999, currency: "USD" },
    },
    {
      rank: 2, score: 89,
      rationale: "Strong overlap with your metroidvania history. You own it and have only 2 hours logged — a clear backlog gem with high completion probability given your precision-combat preference.",
      game: { appid: 367520, name: "Hollow Knight", source: "library", metacritic_score: 87, genres: ["Action", "Indie"], tags: ["Metroidvania", "Atmospheric", "Difficult", "Single-player"], playtime_minutes: 120, price_cents: 1499, currency: "USD" },
    },
    {
      rank: 3, score: 88,
      rationale: "Critically acclaimed roguelike that maps directly onto your progression and challenge preferences. Shorter session length suits your current habits.",
      game: { appid: 2379780, name: "Balatro", source: "wishlist", metacritic_score: 90, genres: ["Indie", "Strategy"], tags: ["Roguelike", "Card Game", "Difficult", "Single-player"], playtime_minutes: 0, price_cents: 1499, currency: "USD" },
    },
    {
      rank: 4, score: 85,
      rationale: "Highest-rated RPG on your library that you haven't started. Your preference for narrative depth and authored worlds makes this an unusually strong fit despite the different genre.",
      game: { appid: 632470, name: "Disco Elysium", source: "library", metacritic_score: 97, genres: ["RPG", "Adventure"], tags: ["Narrative", "Story Rich", "Single-player", "Detective"], playtime_minutes: 0, price_cents: 3999, currency: "USD" },
    },
    {
      rank: 5, score: 82,
      rationale: "You played 74h of Hades — this is a rediscovery candidate. Still in your top genre and you may have left mid-run.",
      game: { appid: 1145360, name: "Hades", source: "library", metacritic_score: 93, genres: ["Action", "Roguelike"], tags: ["Roguelike", "Fast-Paced", "Story Rich", "Single-player"], playtime_minutes: 4440, price_cents: 2499, currency: "USD" },
    },
    {
      rank: 6, score: 79,
      rationale: "Precision platformer with atmospheric depth. Matches your tolerance for difficulty and your preference for polished indie titles with a strong narrative backbone.",
      game: { appid: 504230, name: "Celeste", source: "library", metacritic_score: 92, genres: ["Indie", "Platformer"], tags: ["Difficult", "Atmospheric", "Story Rich", "Single-player"], playtime_minutes: 80, price_cents: 1999, currency: "USD" },
    },
  ],
  games_considered: 47,
  cache_hits: 45,
  cache_misses: 2,
  run_seconds: 18.4,
};

// ─── Entry point ──────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  const user = await checkAuth();
  if (!user) {
    document.getElementById("login-screen").classList.remove("hidden");
    document.querySelector("nav.navbar").classList.add("hidden");
    document.querySelector("main").classList.add("hidden");
    document.getElementById("btn-manual-login").addEventListener("click", onManualLogin);
    document.getElementById("manual-steam-id").addEventListener("keydown", e => {
      if (e.key === "Enter") onManualLogin();
    });
    return;
  }

  // Show nav user info
  const navUser = document.getElementById("nav-user");
  navUser.classList.remove("hidden");
  document.getElementById("nav-steam-id").textContent = `Steam ID: ${user.steam_id}`;
  document.getElementById("btn-logout").addEventListener("click", onLogout);

  setGreeting();
  setupMoodPills();
  setupNav();
  document.getElementById("btn-recommend").addEventListener("click", onAskGaben);
});

async function onManualLogin() {
  const input = document.getElementById("manual-steam-id");
  const errEl = document.getElementById("login-error");
  const steamId = input.value.trim();
  if (!steamId) return;

  errEl.classList.add("hidden");
  const res = await fetch("/auth/manual", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ steam_id: steamId }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    errEl.textContent = body.detail || "Invalid Steam ID";
    errEl.classList.remove("hidden");
    return;
  }
  location.reload();
}

async function checkAuth() {
  try {
    const res = await fetch("/api/me");
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

async function onLogout() {
  await fetch("/auth/logout", { method: "POST" });
  location.reload();
}

function setGreeting() {
  const h = new Date().getHours();
  const salutation = h < 12 ? "Good morning." : h < 17 ? "Good afternoon." : "Good evening.";
  document.getElementById("greeting").textContent = salutation;
  document.getElementById("subheading").textContent = "Ready to find your next game.";
}

function setupMoodPills() {
  document.querySelectorAll(".mood-pill").forEach(pill => {
    pill.addEventListener("click", () => {
      document.querySelectorAll(".mood-pill").forEach(p => p.classList.remove("active"));
      pill.classList.add("active");
    });
  });
}

function setupNav() {
  document.querySelectorAll(".nav-link.stub").forEach(link => {
    link.addEventListener("click", e => {
      e.preventDefault();
      showError("This section is coming in a future step. The Command Deck is live.");
    });
  });
}

// ─── Main flow ────────────────────────────────────────────────────────
async function onAskGaben() {
  const btn = document.getElementById("btn-recommend");
  if (btn.classList.contains("busy")) return;

  btn.classList.add("busy");
  btn.querySelector(".btn-spinner").classList.remove("hidden");
  btn.querySelector(".btn-label").textContent = "Asking…";

  clearError();
  show("loading");
  hide("results");

  const steps = ["Fetching your Steam library…", "Reading wishlist…", "Enriching game data…", "Running agent…"];
  let stepIdx = 0;
  const msgEl = document.querySelector(".loading-msg");
  const stepTimer = setInterval(() => {
    stepIdx = (stepIdx + 1) % steps.length;
    msgEl.textContent = steps[stepIdx];
  }, 1800);

  try {
    const result = await fetchResult();
    clearInterval(stepTimer);
    render(result);
    document.getElementById("subheading").textContent =
      `Your agent found ${result.recommendations.length} games worth your attention.`;
  } catch (err) {
    clearInterval(stepTimer);
    showError(`Agent error: ${err.message}`);
  } finally {
    hide("loading");
    btn.classList.remove("busy");
    btn.querySelector(".btn-spinner").classList.add("hidden");
    btn.querySelector(".btn-label").textContent = "Ask Gaben";
  }
}

async function fetchResult() {
  if (USE_MOCK) {
    await new Promise(r => setTimeout(r, 3200));
    return MOCK_RESULT;
  }
  const res = await fetch("/api/recommend", { method: "POST" });
  if (!res.ok) {
    const body = await res.text().catch(() => res.statusText);
    throw new Error(body);
  }
  return res.json();
}

// ─── Render ───────────────────────────────────────────────────────────
function render(result) {
  renderHero(result.recommendations[0]);
  renderTasteBanner(result.taste_profile);
  renderRecList(result.recommendations.slice(1));
  renderRunStats(result);
  show("results");
  // Kick off score bar animations after a tick so CSS transitions fire.
  requestAnimationFrame(() => requestAnimationFrame(animateScoreBars));
}

function renderHero(rec) {
  const g = rec.game;
  const imgSrc = headerImg(g.appid);
  document.getElementById("hero-section").innerHTML = `
    <div class="hero-card">
      <img class="hero-art" src="${imgSrc}" alt="" loading="eager" onerror="this.style.opacity=0">
      <div class="hero-overlay"></div>
      <div class="hero-body">
        <div class="hero-eyebrow">✦ Tonight's Pick</div>
        <div class="hero-title">${esc(g.name)}</div>
        <div class="hero-tagline">${tagLine(g)}</div>
        <div class="hero-signals">
          <div class="hero-score-wrap">
            <div class="hero-score-label">Taste Match</div>
            <div class="hero-score-row">
              <span class="hero-score-val score-bar-fill" data-score="${rec.score}" style="background:none;color:${scoreColor(rec.score)};width:auto">${rec.score}%</span>
            </div>
          </div>
          ${g.metacritic_score != null ? `<div>${mcBadge(g.metacritic_score)}</div>` : ""}
          <div>${sourceBadge(g.source)}</div>
          ${priceTag(g)}
        </div>
        <div class="hero-rationale">${esc(rec.rationale)}</div>
        <div class="hero-actions">
          <a href="${g.store_url}" target="_blank" rel="noopener" class="btn-primary">Play Now ↗</a>
          <button class="btn-ghost btn-stub">Compare</button>
          <button class="btn-ghost btn-stub">Ask Why</button>
        </div>
      </div>
    </div>`;
}

function renderTasteBanner(profile) {
  document.getElementById("taste-banner").innerHTML = `
    <div class="taste-banner">
      <div class="taste-label">◈ Your Taste Fingerprint</div>
      <p class="taste-summary-text">${esc(profile.summary)}</p>
      <div class="taste-chip-row">
        <span class="taste-section-label">Genres</span>
        ${profile.preferred_genres.map(g => `<span class="chip">${esc(g)}</span>`).join("")}
      </div>
      <div class="taste-chip-row">
        <span class="taste-section-label">Vibes</span>
        ${profile.vibes.map(v => `<span class="chip vibe">${esc(v)}</span>`).join("")}
        <span class="taste-section-label" style="margin-left:.5rem">Try different</span>
        ${profile.wildcard_picks.map(a => `<span class="chip anti" style="background:rgba(214,162,58,0.12);color:var(--warning);border-color:rgba(214,162,58,0.2)">${esc(a)}</span>`).join("")}
      </div>
    </div>`;
}

function renderRecList(recs) {
  document.getElementById("recs-count").textContent = `${recs.length} more`;
  document.getElementById("recs-list").innerHTML = recs.map(renderRecCard).join("");
}

function renderRecCard(rec) {
  const g = rec.game;
  const imgSrc = headerImg(g.appid);
  const playtimeStr = g.playtime_minutes >= 60
    ? `${Math.round(g.playtime_minutes / 60)}h played`
    : g.playtime_minutes > 0 ? `${g.playtime_minutes}m played` : "";
  return `
    <div class="rec-card">
      <img class="rec-thumb" src="${imgSrc}" alt="${esc(g.name)}" loading="lazy" onerror="this.outerHTML='<div class=\'rec-thumb rec-thumb-placeholder\'>◈</div>'">
      <div class="rec-body">
        <div class="rec-top">
          <span class="rec-name">${esc(g.name)}</span>
          <span class="rec-rank">#${rec.rank}</span>
        </div>
        <div class="rec-tags">
          ${g.tags.slice(0, 4).map(t => `<span class="rec-tag">${esc(t)}</span>`).join("")}
          ${playtimeStr ? `<span class="rec-tag" style="color:var(--accent)">${playtimeStr}</span>` : ""}
        </div>
        <div class="rec-signals">
          <div class="score-wrap">
            <div class="score-label">Taste Match</div>
            <div class="score-row">
              <div class="score-bar">
                <div class="score-bar-fill" data-score="${rec.score}" style="background:${scoreColor(rec.score)}"></div>
              </div>
              <span class="score-pct" style="color:${scoreColor(rec.score)}">${rec.score}%</span>
            </div>
          </div>
          ${g.metacritic_score != null ? mcBadge(g.metacritic_score) : `<span class="mc-badge none">No MC</span>`}
          ${sourceBadge(g.source)}
        </div>
        <p class="rec-rationale">${esc(rec.rationale)}</p>
        <div class="rec-actions">
          <a href="${g.store_url}" target="_blank" rel="noopener" class="btn-primary">View on Steam ↗</a>
          <button class="btn-ghost btn-stub">Not Interested</button>
          <button class="btn-ghost btn-stub">Add to Queue</button>
        </div>
      </div>
    </div>`;
}

function renderRunStats(result) {
  const s = result;
  document.getElementById("run-stats").innerHTML = `
    <div class="stat-item">Games considered <span>${s.games_considered}</span></div>
    <div class="stat-item">Cache hits <span>${s.cache_hits}</span></div>
    <div class="stat-item">Cache misses <span>${s.cache_misses}</span></div>
    <div class="stat-item">Run time <span>${s.run_seconds.toFixed(1)}s</span></div>`;
}

// ─── Score bar animation ──────────────────────────────────────────────
function animateScoreBars() {
  document.querySelectorAll(".score-bar-fill[data-score]").forEach(el => {
    if (el.style.width === "" || el.style.width === "0px" || el.style.width === "0%") {
      el.style.width = el.dataset.score + "%";
    }
  });
}

// ─── Helpers ─────────────────────────────────────────────────────────
function headerImg(appid) {
  return `https://cdn.cloudflare.steamstatic.com/steam/apps/${appid}/header.jpg`;
}

function scoreColor(score) {
  if (score >= 88) return "var(--success)";
  if (score >= 72) return "var(--accent)";
  if (score >= 55) return "var(--warning)";
  return "var(--muted)";
}

function mcBadge(score) {
  const cls = score >= 85 ? "great" : score >= 70 ? "good" : "fair";
  return `<span class="mc-badge ${cls}" title="Metacritic">MC ${score}</span>`;
}

function sourceBadge(source) {
  return `<span class="source-badge ${source}">${source === "library" ? "In library" : "On wishlist"}</span>`;
}

function tagLine(g) {
  const parts = g.genres.slice(0, 2);
  if (g.metacritic_score) parts.push(`Metacritic ${g.metacritic_score}`);
  return parts.join(" · ");
}

function priceTag(g) {
  if (!g.price_cents || g.source === "library") return "";
  const dollars = (g.price_cents / 100).toFixed(2);
  return `<span style="color:var(--muted);font-size:.8rem">$${dollars}</span>`;
}

function esc(str) {
  return String(str ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function show(id) { document.getElementById(id)?.classList.remove("hidden"); }
function hide(id) { document.getElementById(id)?.classList.add("hidden"); }

function showError(msg) {
  const el = document.getElementById("error-banner");
  el.textContent = msg;
  el.classList.remove("hidden");
}
function clearError() {
  document.getElementById("error-banner").classList.add("hidden");
}
