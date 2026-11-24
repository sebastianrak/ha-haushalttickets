// Smoke test for haushalttickets-card.js in jsdom: render, open, save, create.
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
const same = (a, b) => assert.equal(JSON.stringify(a), JSON.stringify(b));

const code = readFileSync(process.argv[2], "utf8");
// Opened from a notification link: ?ticket=2
const dom = new JSDOM("<!doctype html><body></body>", {
  pretendToBeVisual: true, runScripts: "outside-only", url: "https://ha.example/dashboard-tickets/board?ticket=2",
});
const { window } = dom;
window.matchMedia = () => ({ matches: false });
window.URL.createObjectURL = () => "blob:fake";
window.eval(code);

const now = new Date().toISOString();
const ticket = (id, extra = {}) => ({
  id, type: "repair", title: `Ticket ${id}`, description: "", status: "new", urgency: "someday",
  importance: null, due_date: null, requested_by_id: "u-alex", created_by_id: "u-alex", created_via: "ui",
  series_id: null, assignee_ids: [], ha_area_id: null, ha_device_id: null, ha_targets: [],
  ai_draft_requested: false, ai_implement_approved: false, ai_implement_approved_by_id: null,
  ai_implement_approved_at: null, price_estimated: null, price_actual: null, product_url: null, vendor: null,
  warranty_until: null, approval_state: "not_required", approval_by_id: null, approval_at: null,
  contractor_name: null, contractor_phone: null, contractor_appointment: null, cost_estimated: null,
  cost_actual: null, warranty_case: false, related_purchase_id: null, checklist: [], created_at: now,
  updated_at: now, closed_at: null, comment_count: 0, attachment_count: 0, cover_attachment_id: null, ...extra,
});
const open = [
  ticket(1, { urgency: "burning", assignee_ids: ["u-robin"], cover_attachment_id: "a1", attachment_count: 1, series_id: 5 }),
  ticket(2, { status: "waiting_for_parts", type: "purchase", approval_state: "pending", price_estimated: "899.00" }),
  ticket(3, { status: "acceptance", type: "home_assistant", comment_count: 2 }),
];
const closed = Array.from({ length: 8 }, (_, i) => ticket(10 + i, { status: i % 2 ? "rejected" : "done", closed_at: now }));
const serien = [{
  id: 5, type: "repair", title: "Rauchmelder testen", description: "", urgency: "someday", importance: null,
  requested_by_id: "u-alex", assignee_ids: ["u-robin"], ha_area_id: null, ha_device_id: null, checklist: [], fields: {},
  mode: "fixed_calendar", interval_count: 1, interval_unit: "years", lead_days: 7, next_due: "2099-06-01",
  anchor: "2098-06-01", active: true, created_at: now, updated_at: now, open_ticket_id: null, appears_on: "2099-05-25", round_count: 1,
}];
const sent = [];
const hass = {
  language: "de",
  user: { id: "u-alex", name: "Alex" },
  states: {
    "person.alex": { entity_id: "person.alex", attributes: { friendly_name: "Alex Muster", user_id: "u-alex" } },
    "person.robin": { entity_id: "person.robin", attributes: { friendly_name: "Robin Muster", user_id: "u-robin" } },
  },
  areas: { kueche: { area_id: "kueche", name: "Küche" } },
  connection: {
    async sendMessagePromise(msg) {
      sent.push(msg);
      const { method, path, params } = msg;
      if (path === "/api/v1/users") return [
        { ha_user_id: "u-alex", display_name: "Alex Muster", role: "admin", ha_is_admin: true, ha_person_entity_id: "person.alex", active: true },
        { ha_user_id: "u-robin", display_name: "Robin Muster", role: "adult", ha_is_admin: false, ha_person_entity_id: "person.robin", active: true },
        { ha_user_id: "automation", display_name: "Automation", role: "adult", ha_is_admin: false, ha_person_entity_id: null, active: true },
      ];
      if (path === "/api/v1/tickets" && method === "GET") {
        return params.status ? { count: closed.length, tickets: closed } : { count: open.length, tickets: open };
      }
      if (path === "/api/v1/tickets/2" && method === "GET") return open[1];
      if (/\/api\/v1\/tickets\/2\/(comments|history|attachments)/.test(path)) {
        if (path.endsWith("history")) return [{ id: 1, ticket_id: 2, actor_id: "u-alex", via: "ui", field: "status", old: "new", new: "waiting_for_parts", at: now }];
        if (path.endsWith("comments")) return [{ id: 1, ticket_id: 2, author_id: "u-robin", body: "Hallo", via: "mcp", created_at: now }];
        return [{ id: "a9", ticket_id: 2, kind: "invoice", filename: "rechnung.pdf", content_type: "application/pdf", size: 1, sha256: "", uploaded_by_id: "u-alex", uploaded_via: "ui", created_at: now }];
      }
      if (path === "/api/v1/tickets/3" && method === "GET") return { ...open[2], latest_draft_version: 2, ai_implement_draft_version: null };
      if (path === "/api/v1/tickets/3/drafts") return [
        { id: 2, ticket_id: 3, version: 2, body: "Plan v2:\n- Automation <b>Licht</b>", run_id: 1, created_at: now },
        { id: 1, ticket_id: 3, version: 1, body: "Plan v1", run_id: 1, created_at: now },
      ];
      if (path === "/api/v1/tickets/3/changes") return [
        { id: 1, ticket_id: 3, run_id: 1, summary: "Paket knx.yaml ergänzt", objects: ["packages/knx.yaml"], git_commit: "0123456789abcdef", needs_restart: true, created_at: now },
      ];
      if (/\/api\/v1\/tickets\/3\/(comments|history|attachments)/.test(path)) return [];
      if (method === "POST" && path === "/api/v1/tickets") return ticket(99, msg.body);
      if (path === "/api/v1/series" && method === "GET") return serien;
      if (path === "/api/v1/series" && method === "POST") return { id: 9, appears_on: "2099-01-01", ...msg.body };
      return {};
    },
    subscribeMessage(cb, msg) { sent.push(msg); return Promise.resolve(() => {}); },
  },
  async fetchWithAuth() { return { ok: true, blob: async () => new window.Blob(["x"]) }; },
  async callWS() { return { path: "/signed" }; },
};

const tick = () => new Promise((r) => setTimeout(r, 30));
const card = window.document.createElement("haushalttickets-card");
card.setConfig({ titel: "Hausmeisterei" });
window.document.body.appendChild(card);
card.hass = hass;
await tick(); await tick(); await tick();

// Link aus einer Benachrichtigung: #2 öffnet nach dem Laden, die Nummer verschwindet aus der Adresse
assert.ok(sent.some((m) => m.path === "/api/v1/tickets/2"), "link opens #2");
assert.equal(window.location.pathname + window.location.search, "/dashboard-tickets/board");
assert.ok(window.document.querySelector(".haushalttickets-dialog").shadowRoot.querySelector(".dtitel"));
card._zu_();

const root = card.shadowRoot;
const spalten = [...root.querySelectorAll(".spalte")].map((s) => s.dataset.s);
same(spalten, ["new", "discussing", "in_progress", "acceptance", "done", "rejected"]);
assert.equal(root.querySelectorAll(".tabs .tab").length, 6);
const inArbeit = root.querySelector('.spalte[data-s="in_progress"]');
assert.match(inArbeit.textContent, /Wartet auf Teile/);
assert.match(inArbeit.textContent, /Freigabe/);
assert.equal(root.querySelector('.spalte[data-s="done"] .karten').children.length, 4);
const archiv = root.querySelector('.spalte[data-s="done"] .mehr');
assert.equal(archiv, null); // 4 done tickets ≤ 6
assert.ok(root.querySelector('img.cover[data-bild="a1"]'));
assert.equal(root.querySelector(".kopf .zahl").textContent, "3");
assert.equal(root.querySelectorAll(".chip[data-f]").length, 4); // Alle, Meine, Alex, Robin

// Typ-Filter
root.querySelector('.chip[data-y="purchase"]').click();
assert.equal(root.querySelectorAll(".karte").length, 1);
root.querySelector('.chip[data-y="alle"]').click();

// Ticket #2 öffnen
root.querySelector('.karte[data-id="2"]').click();
await tick(); await tick();
const portal = window.document.querySelector(".haushalttickets-dialog").shadowRoot;
const dlg = portal.querySelector(".dlg");
assert.ok(dlg, "dialog open");
assert.match(dlg.textContent, /Wartet auf Freigabe/);
assert.ok(portal.querySelector('.wt').checked, "waiting checkbox");
assert.match(portal.querySelector(".verlauf").textContent, /über KI/);
assert.match(portal.querySelector(".anhaenge").textContent, /rechnung\.pdf/);
assert.ok(portal.querySelector('input[data-feld="price_estimated"]'));
assert.ok(portal.querySelector(".weg"), "admin can delete");

// Titel ändern, Preis ändern, Status → Abnahme, speichern
portal.querySelector(".dtitel").value = "Waschmaschine neu";
portal.querySelector('input[data-feld="price_estimated"]').value = "899";
portal.querySelector('input[data-feld="vendor"]').value = "Fachhandel";
portal.querySelector('.st button[data-v="acceptance"]').click();
sent.length = 0;
portal.querySelector(".ok").click();
await tick(); await tick();
const patch = sent.find((m) => m.method === "PATCH");
same(patch.body, { title: "Waschmaschine neu", vendor: "Fachhandel" });
const status = sent.find((m) => m.path.endsWith("/status"));
same(status.body, { status: "acceptance" });
assert.ok(!sent.some((m) => m.path.endsWith("/assignees")), "assignees unchanged");
assert.equal(window.document.querySelector(".haushalttickets-dialog"), null, "dialog closed");

// Neues Ticket anlegen (Anschaffung)
root.querySelector(".plus").click();
await tick();
const p2 = window.document.querySelector(".haushalttickets-dialog").shadowRoot;
p2.querySelector('.ty button[data-v="purchase"]').click();
p2.querySelector(".dtitel").value = "Akkuschrauber";
p2.querySelector('input[data-feld="price_estimated"]').value = "49.9";
p2.querySelector('.pe button[data-v="u-robin"]').click();
p2.querySelector('.pr button[data-v="this_week"]').click();
sent.length = 0;
p2.querySelector(".ok").click();
await tick(); await tick();
const create = sent.find((m) => m.method === "POST" && m.path === "/api/v1/tickets");
same(create.body, { type: "purchase", title: "Akkuschrauber", urgency: "this_week", assignee_ids: ["u-robin"], price_estimated: "49.90" });

// Fotos im Dialog „Neues Ticket“: sofort eingelesen, auch wenn die Originaldatei danach
// nicht mehr lesbar ist (iOS-App); zu große Dateien und Netzwerkfehler werden klar gemeldet
// (jsdom 24 kennt Blob.arrayBuffer nicht, Browser schon)
window.Blob.prototype.arrayBuffer = function () {
  return new Promise((ok, fail) => {
    const r = new window.FileReader();
    r.onload = () => ok(r.result);
    r.onerror = () => fail(r.error);
    r.readAsArrayBuffer(this);
  });
};
const hinweise = [];
window.document.addEventListener("hass-notification", (e) => hinweise.push(e.detail.message));
const uploads = [];
let netzFehler = false;
hass.fetchWithAuth = async (url, opts) => {
  if (!opts) return { ok: true, blob: async () => new window.Blob(["x"]) };
  if (netzFehler) throw new window.TypeError("Load failed");
  uploads.push({ url, datei: opts.body.get("file") });
  return { ok: true, status: 201, json: async () => ({}) };
};
const waehlen = async (input, dateien) => {
  Object.defineProperty(input, "files", { value: dateien, configurable: true });
  input.dispatchEvent(new window.Event("change"));
  await tick();
};

root.querySelector(".plus").click();
await tick();
const p3 = window.document.querySelector(".haushalttickets-dialog").shadowRoot;
p3.querySelector(".dtitel").value = "Wasserhahn tropft";
const original = new window.File(["jpegdaten"], "IMG_1567.jpg", { type: "image/jpeg" });
let gelesen = 0;
original.arrayBuffer = function () { gelesen += 1; return window.Blob.prototype.arrayBuffer.call(this); };
await waehlen(p3.querySelector(".vorab"), [original]);
assert.equal(gelesen, 1, "read when selected, not when saved");
assert.match(p3.querySelector(".vorabliste").textContent, /IMG_1567\.jpg/);
original.arrayBuffer = () => Promise.reject(new window.Error("NotReadableError"));   // iOS gibt die Datei frei
p3.querySelector(".ok").click();
await tick(); await tick();
assert.equal(uploads.length, 1, "photo uploaded after create");
assert.equal(uploads[0].url, "/api/haushalttickets/tickets/99/attachments");
assert.equal(uploads[0].datei.size, original.size);
same(hinweise, []);

// Ticket #2: zu große Datei wird nicht gesendet, Netzwerkfehler nennt Datei und Größe
root.querySelector('.karte[data-id="2"]').click();
await tick(); await tick();
const p4 = window.document.querySelector(".haushalttickets-dialog").shadowRoot;
const fotoFeld = p4.querySelector('.hoch input[data-k="photo"]');
uploads.length = 0;
await waehlen(fotoFeld, [new window.File([new Uint8Array(17 * 1024 * 1024)], "riesig.pdf", { type: "application/pdf" })]);
await tick();
assert.equal(uploads.length, 0, "too large: not sent");
assert.equal(hinweise.pop(), "Die Datei ist zu groß.");
netzFehler = true;
await waehlen(fotoFeld, [new window.File(["abc"], "scan.pdf", { type: "application/pdf" })]);
await tick();
assert.match(hinweise.pop(), /^Hochladen fehlgeschlagen: scan\.pdf \(0 MB\) – Load failed$/);

// Wiederkehrende Tickets: Symbol auf der Kachel, Zähler im Kopf, Übersicht
card._zu_();
assert.ok(root.querySelector('.karte[data-id="1"] ha-icon[icon="mdi:repeat"]'), "repeat icon on tile");
assert.equal(root.querySelector(".kopf .wdhk b").textContent, "1");
root.querySelector(".kopf .wdhk").click();
await tick();
let ps = window.document.querySelector(".haushalttickets-dialog").shadowRoot;
const zeile = ps.querySelector('.serie[data-s="5"]');
assert.match(zeile.textContent, /jedes Jahr · fester Kalender/);
assert.match(zeile.textContent, /nächste Runde fällig/);
sent.length = 0;
zeile.querySelector('[data-a="pause"]').click();
await tick(); await tick();
same(sent.find((m) => m.method === "PATCH").body, { active: false });
ps = window.document.querySelector(".haushalttickets-dialog").shadowRoot;
ps.querySelector('.serie[data-s="5"] [data-a="edit"]').click();
ps = window.document.querySelector(".haushalttickets-dialog").shadowRoot;
const edit = ps.querySelector('.serie[data-s="5"] .sedit');
edit.querySelector(".rn").value = "2";
edit.querySelector('.rm button[data-v="after_completion"]').click();
sent.length = 0;
edit.querySelector('[data-a="save"]').click();
await tick(); await tick();
same(sent.find((m) => m.method === "PATCH").body, { interval_count: 2, mode: "after_completion" });
card._serienZu();

// Neues Ticket mit Wiederholung, erste Runde weit hin: nur die Serie wird geplant
root.querySelector(".plus").click();
await tick();
let p5 = window.document.querySelector(".haushalttickets-dialog").shadowRoot;
p5.querySelector(".dtitel").value = "Heizung warten";
p5.querySelector(".datum").value = "2099-09-01";
p5.querySelector(".datum").dispatchEvent(new window.Event("change"));
p5.querySelector(".wdhan").click();
p5.querySelector(".wdhregel .re").value = "years";
sent.length = 0;
p5.querySelector(".ok").click();
await tick(); await tick();
assert.ok(!sent.some((m) => m.method === "POST" && m.path === "/api/v1/tickets"), "no ticket yet");
const plan = sent.find((m) => m.method === "POST" && m.path === "/api/v1/series").body;
same(plan, { type: "repair", title: "Heizung warten", urgency: "someday", first_due: "2099-09-01", interval_count: 1, interval_unit: "years", mode: "after_completion", lead_days: 7 });
assert.match(hinweise.pop(), /^Geplant – erscheint am /);

// Bestehendes Ticket wiederholen: das Ticket wird die erste Runde
root.querySelector('.karte[data-id="2"]').click();
await tick(); await tick();
p5 = window.document.querySelector(".haushalttickets-dialog").shadowRoot;
p5.querySelector(".wdhan").click();
sent.length = 0;
p5.querySelector(".ok").click();
await tick(); await tick();
same(sent.find((m) => m.path === "/api/v1/series").body, { from_ticket_id: 2, interval_count: 1, interval_unit: "months", mode: "after_completion", lead_days: 7 });

// Link, während das Board schon offen ist (HA navigiert und meldet location-changed)
card._zu_();
window.history.pushState(null, "", "/dashboard-tickets/board?ticket=2");
sent.length = 0;
window.dispatchEvent(new window.Event("location-changed"));
await tick(); await tick();
assert.ok(sent.some((m) => m.path === "/api/v1/tickets/2"), "link while open");
assert.equal(window.location.search, "");
window.history.pushState(null, "", "/dashboard-tickets/board?ticket=abc");
sent.length = 0;
window.dispatchEvent(new window.Event("location-changed"));
await tick();
assert.ok(!sent.some((m) => /\/api\/v1\/tickets\/\w+$/.test(m.path)), "no ticket for a bad number");

// KI: Entwürfe und Änderungen lesen, Freigabe gilt für Entwurf v2
card._zu_();
root.querySelector('.karte[data-id="3"]').click();
await tick(); await tick();
let p6 = window.document.querySelector(".haushalttickets-dialog").shadowRoot;
const kientw = p6.querySelector(".kientw");
assert.ok(kientw, "drafts shown");
assert.match(kientw.textContent, /Entwurf v2/);
assert.ok(!p6.querySelector(".kitext b"), "draft text is escaped");
assert.match(p6.querySelector(".kiaend").textContent, /0123456789 · wirksam erst nach Neuladen\/Neustart/);
let ku = p6.querySelector(".ku");
assert.equal(ku.disabled, false);
assert.match(ku.closest("label").textContent, /KI soll Entwurf v2 umsetzen/);
ku.checked = true;
sent.length = 0;
p6.querySelector(".ok").click();
await tick(); await tick();
same(sent.find((m) => m.path === "/api/v1/tickets/3/ai-flags").body, { implement_approved: true });
// Neuen Entwurf anfordern sperrt das Umsetzen
card._zu_();
root.querySelector('.karte[data-id="3"]').click();
await tick(); await tick();
p6 = window.document.querySelector(".haushalttickets-dialog").shadowRoot;
const kd = p6.querySelector(".kd");
kd.checked = true;
kd.dispatchEvent(new window.Event("change"));
ku = p6.querySelector(".ku");
assert.equal(ku.disabled, true);
assert.match(p6.querySelector(".kiflags").textContent, /Ein neuer Entwurf ist angefragt/);
card._zu_();

console.log("card smoke test ok");
