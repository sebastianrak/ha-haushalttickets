// Smoke test for haushalttickets-card.js in jsdom: render, open, save, create.
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
const same = (a, b) => assert.equal(JSON.stringify(a), JSON.stringify(b));

const code = readFileSync(process.argv[2], "utf8");
const dom = new JSDOM("<!doctype html><body></body>", { pretendToBeVisual: true, runScripts: "outside-only" });
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
  ticket(1, { urgency: "burning", assignee_ids: ["u-robin"], cover_attachment_id: "a1", attachment_count: 1 }),
  ticket(2, { status: "waiting_for_parts", type: "purchase", approval_state: "pending", price_estimated: "899.00" }),
  ticket(3, { status: "acceptance", type: "home_assistant", comment_count: 2 }),
];
const closed = Array.from({ length: 8 }, (_, i) => ticket(10 + i, { status: i % 2 ? "rejected" : "done", closed_at: now }));
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
      if (method === "POST" && path === "/api/v1/tickets") return ticket(99, msg.body);
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
await tick(); await tick();

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

console.log("card smoke test ok");
