"use strict";

const POLL_MS = 2500;
let currentSnapshot = null;
let openModalPuuid = null;

const el = (id) => document.getElementById(id);

let pollTimer = null;

async function poll() {
    // Exactly one live timer: overlapping poll loops would multiply the
    // number of requests sent to the server.
    if (pollTimer) {
        clearTimeout(pollTimer);
        pollTimer = null;
    }
    try {
        const res = await fetch("/api/match", { cache: "no-store" });
        const data = await res.json();
        currentSnapshot = data;
        render(data);
    } catch (e) {
        setStatus("off", "Server unreachable");
    } finally {
        // Do not poll the server while the tab sits in the background.
        const delay = document.hidden ? POLL_MS * 4 : POLL_MS;
        pollTimer = setTimeout(poll, delay);
    }
}

function setStatus(kind, text) {
    el("statusDot").className = "status-dot " + kind;
    el("statusText").textContent = text;
}

function render(data) {
    const state = data.state;

    if (state === "DISCONNECTED") {
        setStatus("off", "VALORANT not detected");
    } else if (state === "MENU") {
        setStatus("wait", "In the menu");
    } else if (state === "PREGAME") {
        setStatus("on", "Agent Select");
    } else if (state === "INGAME") {
        setStatus("on", "In match");
    } else {
        setStatus("wait", "Connecting...");
    }

    el("metaMode").textContent = data.mode || "—";
    el("metaMap").textContent = data.map || "—";

    const players = data.players || [];
    const teams = el("teams");
    const placeholder = el("placeholder");

    if (!players.length) {
        teams.hidden = true;
        placeholder.hidden = false;
        let msg = "Waiting for a match...";
        if (state === "DISCONNECTED") msg = data.error || "Start VALORANT to begin.";
        else if (state === "MENU") msg = "In the menu. Start a match to see the loadouts.";
        else if (state === "PREGAME") msg = "Agent select in progress... (loadouts appear once the match starts)";
        el("placeholderText").textContent = msg;
        return;
    }

    placeholder.hidden = true;
    teams.hidden = false;

    const allies = players.filter((p) => p.team === "ally");
    const enemies = players.filter((p) => p.team === "enemy");

    // Deathmatch / FFA / solo: no enemy team, so use one multi-column grid
    if (!enemies.length) {
        el("enemyCol").style.display = "none";
        el("teams").classList.add("solo");
        el("allyCol").querySelector(".team-title").textContent = "Players";
    } else {
        el("enemyCol").style.display = "";
        el("teams").classList.remove("solo");
        el("allyCol").querySelector(".team-title").textContent = "Allies";
    }

    renderList(el("allyList"), allies);
    renderList(el("enemyList"), enemies);

    if (openModalPuuid) {
        const p = players.find((x) => x.puuid === openModalPuuid);
        if (p) fillModal(p);
    }
}

function renderList(container, list) {
    container.innerHTML = "";
    for (const p of list) {
        container.appendChild(playerCard(p));
    }
}

function skinLine(label, w) {
    if (!w) {
        return `<div class="skin-line"><span class="wlabel">${label}</span>
                <span class="sname" style="color:var(--text-dim)">—</span></div>`;
    }
    const img = w.image
        ? `<img src="${w.image}" alt="" onerror="this.style.display='none'">` : "";
    return `<div class="skin-line">
        <span class="wlabel">${label}</span>
        ${img}
        <span class="sname">${escapeHtml(w.skin || "Default")}</span>
    </div>`;
}

function playerCard(p) {
    const div = document.createElement("div");
    div.className = "player " + p.team + (p.is_me ? " me" : "");
    div.onclick = () => openModal(p.puuid);

    const agentImg = p.agent.icon
        ? `<img class="agent-img" src="${p.agent.icon}" alt="" onerror="this.style.opacity=0.2">`
        : `<div class="agent-img"></div>`;
    const rankBadge = p.rank.icon
        ? `<span class="rank-badge"><img src="${p.rank.icon}" alt=""></span>` : "";

    const ghost = p.is_private ? `<span class="ghost" title="Incognito">👻</span>` : "";
    const star = p.is_me ? `<span class="star" title="You">★</span>` : "";
    const tag = p.tag ? `<span class="tag">#${escapeHtml(p.tag)}</span>` : "";

    const peak = (p.peak && p.peak.tier && p.peak.tier !== p.rank.tier)
        ? `<span class="peak"> · peak ${escapeHtml(p.peak.name)}</span>` : "";
    const rr = p.rank.rr ? ` · ${p.rank.rr} RR` : "";

    const loadout = p.loadout || {};

    div.innerHTML = `
        <div class="agent-wrap">${agentImg}${rankBadge}</div>
        <div class="pinfo">
            <div class="pname">${escapeHtml(p.name)}${tag}${star}${ghost}</div>
            <div class="pmeta">
                <span class="agent-name">${escapeHtml(p.agent.name || "—")}</span>
                · <span class="rank-name" style="color:${p.rank.color}">${escapeHtml(p.rank.name)}</span>${rr}${peak}
            </div>
        </div>
        <div class="skins-preview">
            ${skinLine("Vandal", loadout.vandal)}
            ${skinLine("Phantom", loadout.phantom)}
        </div>
    `;
    return div;
}

function openModal(puuid) {
    openModalPuuid = puuid;
    const p = (currentSnapshot.players || []).find((x) => x.puuid === puuid);
    if (!p) return;
    fillModal(p);
    el("modal").hidden = false;
}

function closeModal() {
    openModalPuuid = null;
    el("modal").hidden = true;
}

function fillModal(p) {
    const header = el("modalHeader");
    const agentImg = p.agent.icon ? `<img class="mh-agent" src="${p.agent.icon}" alt="">` : "";
    const rankImg = p.rank.icon ? `<img src="${p.rank.icon}" alt="">` : "";
    header.innerHTML = `
        ${agentImg}
        <div>
            <div class="mh-name">${escapeHtml(p.name)}${p.tag ? '<span style="color:var(--text-dim)">#' + escapeHtml(p.tag) + '</span>' : ''}</div>
            <div class="mh-rank">${rankImg}<span style="color:${p.rank.color}">${escapeHtml(p.rank.name)}</span>
                ${p.rank.rr ? '· ' + p.rank.rr + ' RR' : ''}
                ${p.agent.name ? '· ' + escapeHtml(p.agent.name) : ''}</div>
        </div>`;

    const arsenal = el("arsenal");
    arsenal.innerHTML = "";
    const weapons = (p.loadout && p.loadout.weapons) || [];
    if (!weapons.length) {
        arsenal.innerHTML = `<div class="empty-note">Loadouts are only available during the match, not in agent select.</div>`;
        return;
    }
    for (const w of weapons) {
        const card = document.createElement("div");
        card.className = "weapon-card";
        const img = w.image ? `<img src="${w.image}" alt="" onerror="this.style.display='none'">` : "";
        card.innerHTML = `
            <div class="wc-top">
                <span class="wc-weapon">${escapeHtml(w.weapon)}</span>
            </div>
            <div class="wc-skin">${escapeHtml(w.skin || "Default")}</div>
            ${img}`;
        arsenal.appendChild(card);
    }
}

function escapeHtml(s) {
    return String(s == null ? "" : s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

// Refresh immediately when the tab regains focus, instead of waiting out
// the slow background timer.
document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
});

el("modalClose").onclick = closeModal;
el("modal").onclick = (e) => { if (e.target.id === "modal") closeModal(); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

poll();
