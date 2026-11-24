/*
 * Haushalttickets – Kanban-Karte für Home Assistant
 *
 * Basiert auf dem Ticket-Board von Kily's World (https://kilysworld.com,
 * https://github.com/johnkily/ha-blueprints, MIT-Lizenz) einschließlich der lokalen
 * Anpassungen p1–p6 (Spalte „Neue Tickets“, iPhone-Reiter, Dialog als Portal mit festem
 * Fußbalken, zweistufiges Löschen, Hinweise statt alert(), Touch-Bedienung ohne Ziehen).
 * Die Tickets liegen auf dem Haushalttickets-Server; die Karte spricht ihn über die
 * Integration an (WebSocket haushalttickets/request, Fotos über /api/haushalttickets/…).
 * Was jemand darf, entscheidet allein der Server.
 *
 * type: custom:haushalttickets-card
 * titel: Hausmeisterei   # optional
 * archiv: 6              # optional: so viele Erledigte / Träum weiter sichtbar
 *
 * MIT-Lizenz
 */
const HT_VERSION = "0.3.1";

const HT_TEXTE = {
  de: {
    spalten: { new: "Neue Tickets", discussing: "Wird beredet", in_progress: "In Arbeit", acceptance: "Abnahme", done: "Erledigt", rejected: "Träum weiter" },
    dringlich: { someday: "Irgendwann", this_week: "Diese Woche", burning: "Brennt" },
    typen: { repair: "Reparatur", purchase: "Anschaffung", home_assistant: "Home Assistant" },
    alle: "Alle", ich: "Meine", alleTypen: "Alle Typen", suche: "Suchen …", neu: "Neues Ticket", titel: "Was ist zu tun?",
    beschreibung: "Beschreibung", status: "Status", dringlichkeit: "Wie eilig?", zustaendig: "Wer kümmert sich?",
    niemand: "Noch niemand", herz: "Wie wichtig?", faellig: "Fällig", keinTermin: "Kein Termin", woche: "In 1 Woche",
    zweiWochen: "In 2 Wochen", monat: "In 1 Monat", speichern: "Speichern", anlegen: "Anlegen", loeschen: "Löschen",
    loeschenFrage: "Ticket wirklich löschen?", schliessen: "Schließen", kommentar: "Kommentar …", senden: "Senden",
    verlauf: "Verlauf", abnahmeDurch: "Abnahme durch", mehr: "Alle %n anzeigen", weniger: "Weniger anzeigen",
    leer: "Nichts zu tun 🎉", ueberfaellig: "überfällig", heute: "heute", morgen: "morgen", gewuenscht: "Gewünscht von",
    wartet: "Wartet auf Teile", typ: "Art", bereich: "Bereich", keinBereich: "Kein Bereich", details: "Details",
    preisGeschaetzt: "Preis geschätzt (€)", preisTatsaechlich: "Preis tatsächlich (€)", haendler: "Händler",
    link: "Link", garantieBis: "Garantie bis", handwerker: "Handwerker", telefon: "Telefon", termin: "Termin",
    kostenGeschaetzt: "Kosten geschätzt (€)", kostenTatsaechlich: "Kosten tatsächlich (€)", garantiefall: "Garantiefall",
    bezug: "Gehört zu Anschaffung", keinBezug: "Keine", entitaeten: "Betroffene Entitäten (kommagetrennt)",
    ki: "KI", kiEntwurf: "KI soll entwerfen", kiUmsetzen: "KI soll umsetzen", checkliste: "Checkliste",
    punkt: "Neuer Punkt …", anhaenge: "Fotos und Dateien", foto: "Foto", rechnung: "Rechnung", datei: "Datei",
    laedtHoch: "Lädt hoch …", freigabe: "Freigabe", freigabeOffen: "Wartet auf Freigabe durch die zweite erwachsene Person",
    freigabeAbgelehnt: "Freigabe abgelehnt", freigegeben: "Freigegeben", freigeben: "Freigeben", ablehnen: "Ablehnen",
    keinZugriff: "Haushalttickets ist nicht erreichbar.", laedt: "Lädt …", angelegt: "angelegt", statusAuf: "Status →",
    geaendert: "geändert", entfernen: "Entfernen?", fotosNachher: "Fotos", via_ai: "über KI",
    liest: "Liest …", nichtLesbar: "Datei nicht lesbar", hochladenFehler: "Hochladen fehlgeschlagen",
    felder: {
      title: "Titel", description: "Beschreibung", urgency: "Dringlichkeit", importance: "Herzen", due_date: "Fälligkeit",
      ha_area_id: "Bereich", ha_device_id: "Gerät", ha_targets: "Entitäten", price_estimated: "Preis geschätzt",
      price_actual: "Preis tatsächlich", product_url: "Link", vendor: "Händler", warranty_until: "Garantie",
      contractor_name: "Handwerker", contractor_phone: "Telefon", contractor_appointment: "Termin",
      cost_estimated: "Kosten geschätzt", cost_actual: "Kosten tatsächlich", warranty_case: "Garantiefall",
      related_purchase_id: "Anschaffung", assignees: "Zuständige", approval_state: "Freigabe",
      checklist_added: "Checkliste +", checklist_removed: "Checkliste −", checklist_text: "Checkliste",
      checklist_done: "Checkliste ✓", attachment_added: "Anhang +", attachment_removed: "Anhang −",
      ai_draft_requested: "KI-Entwurf", ai_implement_approved: "KI-Umsetzung", admin_override: "Admin-Abnahme",
    },
    fehler: {
      acceptance_by_requester: "Abnehmen darf nur, wer das Ticket gewünscht hat.",
      approval_pending: "Die Anschaffung braucht erst die Freigabe der zweiten erwachsenen Person.",
      approval_by_other_adult: "Freigeben muss die andere erwachsene Person.",
      approval_forbidden: "Freigeben dürfen nur Erwachsene.",
      assign_forbidden: "Zuweisen dürfen nur Erwachsene.",
      child_status_forbidden: "Kinder dürfen nur eigene Tickets abnehmen.",
      delete_forbidden: "Löschen dürfen nur Admins.",
      field_forbidden: "Dieses Feld darfst du nicht ändern.",
      not_own_ticket: "Das geht nur bei eigenen Tickets.",
      ticket_closed: "Geschlossene Tickets erst wieder öffnen.",
      ai_flags_forbidden: "KI-Freigaben dürfen nur Erwachsene setzen.",
      too_large: "Die Datei ist zu groß.",
      unsupported_type: "Dieser Dateityp geht nicht.",
    },
  },
  en: {
    spalten: { new: "Inbox", discussing: "Discussing", in_progress: "In progress", acceptance: "Review", done: "Done", rejected: "Dream on" },
    dringlich: { someday: "Someday", this_week: "This week", burning: "Urgent" },
    typen: { repair: "Repair", purchase: "Purchase", home_assistant: "Home Assistant" },
    alle: "All", ich: "Mine", alleTypen: "All types", suche: "Search …", neu: "New ticket", titel: "What needs doing?",
    beschreibung: "Description", status: "Status", dringlichkeit: "How urgent?", zustaendig: "Who takes care?",
    niemand: "Nobody yet", herz: "How important?", faellig: "Due", keinTermin: "No date", woche: "In 1 week",
    zweiWochen: "In 2 weeks", monat: "In 1 month", speichern: "Save", anlegen: "Create", loeschen: "Delete",
    loeschenFrage: "Really delete this ticket?", schliessen: "Close", kommentar: "Comment …", senden: "Send",
    verlauf: "History", abnahmeDurch: "Review by", mehr: "Show all %n", weniger: "Show less",
    leer: "Nothing to do 🎉", ueberfaellig: "overdue", heute: "today", morgen: "tomorrow", gewuenscht: "Requested by",
    wartet: "Waiting for parts", typ: "Kind", bereich: "Area", keinBereich: "No area", details: "Details",
    preisGeschaetzt: "Estimated price (€)", preisTatsaechlich: "Actual price (€)", haendler: "Vendor",
    link: "Link", garantieBis: "Warranty until", handwerker: "Contractor", telefon: "Phone", termin: "Appointment",
    kostenGeschaetzt: "Estimated cost (€)", kostenTatsaechlich: "Actual cost (€)", garantiefall: "Warranty case",
    bezug: "Belongs to purchase", keinBezug: "None", entitaeten: "Affected entities (comma separated)",
    ki: "AI", kiEntwurf: "AI should draft", kiUmsetzen: "AI should implement", checkliste: "Checklist",
    punkt: "New item …", anhaenge: "Photos and files", foto: "Photo", rechnung: "Invoice", datei: "File",
    laedtHoch: "Uploading …", freigabe: "Approval", freigabeOffen: "Waiting for the second adult's approval",
    freigabeAbgelehnt: "Approval declined", freigegeben: "Approved", freigeben: "Approve", ablehnen: "Decline",
    keinZugriff: "Haushalttickets cannot be reached.", laedt: "Loading …", angelegt: "created", statusAuf: "Status →",
    geaendert: "changed", entfernen: "Remove?", fotosNachher: "Photos", via_ai: "via AI",
    liest: "Reading …", nichtLesbar: "File not readable", hochladenFehler: "Upload failed",
    felder: {
      title: "Title", description: "Description", urgency: "Urgency", importance: "Hearts", due_date: "Due date",
      ha_area_id: "Area", ha_device_id: "Device", ha_targets: "Entities", price_estimated: "Estimated price",
      price_actual: "Actual price", product_url: "Link", vendor: "Vendor", warranty_until: "Warranty",
      contractor_name: "Contractor", contractor_phone: "Phone", contractor_appointment: "Appointment",
      cost_estimated: "Estimated cost", cost_actual: "Actual cost", warranty_case: "Warranty case",
      related_purchase_id: "Purchase", assignees: "Assignees", approval_state: "Approval",
      checklist_added: "Checklist +", checklist_removed: "Checklist −", checklist_text: "Checklist",
      checklist_done: "Checklist ✓", attachment_added: "Attachment +", attachment_removed: "Attachment −",
      ai_draft_requested: "AI draft", ai_implement_approved: "AI implementation", admin_override: "Admin acceptance",
    },
    fehler: {},
  },
};

const HT_SPALTEN = [
  { key: "new", icon: "mdi:inbox-arrow-down", farbe: "#90a4ae" },
  { key: "discussing", icon: "mdi:chat-processing-outline", farbe: "#4fc3f7" },
  { key: "in_progress", icon: "mdi:hammer-wrench", farbe: "#ffb74d" },
  { key: "acceptance", icon: "mdi:clipboard-check-outline", farbe: "#ce93d8" },
  { key: "done", icon: "mdi:check-circle-outline", farbe: "#81c784", archiv: true },
  { key: "rejected", icon: "mdi:weather-night", farbe: "#9575cd", archiv: true },
];
// Dateien, die schon im Speicher liegen (nicht noch einmal verkleinern)
const HT_EINGELESEN = new WeakSet();
// Platz für den Multipart-Rahmen unter Home Assistants Grenze von 16 MiB
const HT_MAX_UPLOAD = 16 * 1024 * 1024 - 64 * 1024;
const HT_DRINGLICH = { someday: "#78909c", this_week: "#ffb74d", burning: "#ef5350" };
const HT_DRINGLICH_RANG = { burning: 0, this_week: 1, someday: 2 };
const HT_TYPEN = { repair: "mdi:wrench-outline", purchase: "mdi:cart-outline", home_assistant: "mdi:home-assistant" };
const HT_GESCHLOSSEN = ["done", "rejected"];
const HT_ERWACHSEN = ["admin", "adult"];
// Felder je Typ, wie auf dem Server (schemas.TYPE_FIELDS)
const HT_TYPFELDER = {
  repair: ["ha_area_id", "contractor_name", "contractor_phone", "contractor_appointment", "cost_estimated", "cost_actual", "warranty_case", "related_purchase_id"],
  purchase: ["price_estimated", "price_actual", "product_url", "vendor", "warranty_until"],
  home_assistant: ["ha_area_id", "ha_targets"],
};
const HT_BILDER = new Map(); // Anhang-ID → Promise<Objekt-URL>, über alle Karten hinweg

function htEsc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Ziehen nur mit Maus/Trackpad – auf Touch-Geräten läuft der Statuswechsel über den Dialog (p6)
function htZiehbar() {
  return !!(window.matchMedia && window.matchMedia("(hover: hover) and (pointer: fine)").matches);
}

// Zeitpunkt (ISO, mit Zeitzone) ↔ Eingabefeld datetime-local in Ortszeit
function htLokal(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const z = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`;
}

// Status für die Spalten: „Wartet auf Teile“ steht als Markierung in „In Arbeit“
function htSpalte(status) {
  return status === "waiting_for_parts" ? "in_progress" : status;
}

class HaushaltticketsCard extends HTMLElement {
  static getStubConfig() {
    return { titel: "Hausmeisterei" };
  }

  setConfig(config) {
    this._config = Object.assign({ archiv: 6 }, config || {});
    this._offen = this._offen || [];
    this._zu = this._zu || [];
    this._zuAnzahl = this._zuAnzahl || 0;
    this._users = this._users || {};
    this._filter = this._filter || "alle";
    this._typ = this._typ || "alle";
    this._suche = this._suche || "";
    this._archivAuf = this._archivAuf || {};
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._geruest();
    if (this._hass) { this._kopf(); this._laden(); }
  }

  set hass(hass) {
    const erst = !this._hass;
    this._hass = hass;
    this._t = HT_TEXTE[(hass.language || "de").startsWith("de") ? "de" : "en"];
    if (erst) { this._kopf(); this._laden(); this._abonnieren(); }
  }

  getCardSize() { return 8; }
  getGridOptions() { return { columns: "full", min_columns: 6 }; }

  connectedCallback() {
    if (this._hass && !this._abo) { this._abonnieren(); this._laden(); }
  }

  disconnectedCallback() {
    if (this._abo) { this._abo.then((f) => f && f()).catch(() => {}); this._abo = null; }
    if (this._pt) { this._pt.remove(); this._dialog = null; }
  }

  // ---------- Server ----------
  _ws(method, path, extra = {}) {
    const msg = { type: "haushalttickets/request", method, path };
    if (extra.params) msg.params = extra.params;
    if (extra.body !== undefined) msg.body = extra.body;
    return this._hass.connection.sendMessagePromise(msg);
  }

  async _laden() {
    if (!this._hass || !this._hass.connection) return;
    if (this._laedt) { this._nochmal = true; return; }
    this._laedt = true;
    try {
      const [users, offen, zu] = await Promise.all([
        this._ws("GET", "/api/v1/users"),
        this._ws("GET", "/api/v1/tickets", { params: { include_closed: false, limit: 500 } }),
        this._ws("GET", "/api/v1/tickets", { params: { status: HT_GESCHLOSSEN, limit: 200 } }),
      ]);
      const vorher = Object.keys(this._users).length;
      this._users = Object.fromEntries(users.map((u) => [u.ha_user_id, u]));
      this._offen = offen.tickets;
      this._zu = zu.tickets;
      this._zuAnzahl = zu.count;
      this._geladen = true;
      if (!vorher) this._kopf(); else this._board();
    } catch (e) {
      this._fehler(this._t.keinZugriff + " (" + (e.message || e.code || e) + ")");
    } finally {
      this._laedt = false;
      if (this._nochmal) { this._nochmal = false; this._laden(); }
    }
  }

  _abonnieren() {
    if (!this._hass || !this._hass.connection || this._abo) return;
    this._abo = this._hass.connection.subscribeMessage((ev) => {
      clearTimeout(this._ladeTimer);
      this._ladeTimer = setTimeout(() => this._laden(), 250);
      if (this._dialog && this._dialog.id && ev && ev.ticket_id === this._dialog.id) this._dialogNachladen();
    }, { type: "haushalttickets/subscribe" });
    this._abo.catch(() => { this._abo = null; });
  }

  // Fehler als HA-Hinweis statt alert() (p5)
  _hinweis(e) {
    const code = e && e.code;
    const text = (code && this._t.fehler[code]) || (e && e.message) || String(e);
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message: text }, bubbles: true, composed: true }));
  }

  async _aktion(fn) {
    try { await fn(); return true; } catch (e) { this._hinweis(e); return false; }
  }

  // ---------- Personen ----------
  _ich() { return (this._hass.user && this._hass.user.id) || ""; }
  _rolle() { const u = this._users[this._ich()]; return u ? u.role : "child"; }
  _erwachsen() { return HT_ERWACHSEN.includes(this._rolle()); }

  // Haushaltsmitglieder: mit Person verknüpft oder KI-Konto; nie der Benutzer „Automation“
  _mitglieder() {
    return Object.values(this._users)
      .filter((u) => u.active && u.ha_user_id !== "automation" && (u.ha_person_entity_id || u.role === "ai_agent"))
      .sort((a, b) => String(a.display_name).localeCompare(String(b.display_name)));
  }

  _name(userId) {
    const u = this._users[userId];
    if (!u) return userId === "automation" ? "Automation" : "?";
    const p = u.ha_person_entity_id && this._hass.states[u.ha_person_entity_id];
    return ((p && p.attributes.friendly_name) || u.display_name || "?").split(" ")[0];
  }

  _avatar(userId, groesse) {
    if (!userId) return "";
    const u = this._users[userId];
    const p = u && u.ha_person_entity_id && this._hass.states[u.ha_person_entity_id];
    const bild = p && p.attributes.entity_picture;
    const name = this._name(userId);
    const farbe = ["#4fc3f7", "#f06292", "#81c784", "#ffb74d", "#ba68c8", "#4db6ac"][[...name].reduce((a, c) => a + c.charCodeAt(0), 0) % 6];
    return bild
      ? `<img class="av" style="width:${groesse}px;height:${groesse}px" src="${htEsc(bild)}" alt="${htEsc(name)}" title="${htEsc(name)}">`
      : `<span class="av" title="${htEsc(name)}" style="width:${groesse}px;height:${groesse}px;background:${farbe};font-size:${Math.round(groesse * 0.45)}px">${htEsc(name.trim()[0] || "?")}</span>`;
  }

  // ---------- Gerüst & Kopf ----------
  _geruest() {
    this.shadowRoot.innerHTML = `<style>${HT_CSS}</style>
      <ha-card><div class="kopf"></div><div class="board"></div></ha-card>`;
    this.shadowRoot.querySelector(".board").addEventListener("click", (e) => this._klickBoard(e));
    this._ziehen();
  }

  // Dialog hängt an <body>, damit er nie unter der HA-Kopfleiste landet (p4)
  _portal() {
    if (!this._pt) {
      this._pt = document.createElement("div");
      this._pt.className = "haushalttickets-dialog";
      this._pt.attachShadow({ mode: "open" }).innerHTML = `<style>${HT_CSS}</style><div class="ueber"></div>`;
    }
    if (!this._pt.isConnected) document.body.appendChild(this._pt);
    return this._pt.shadowRoot.querySelector(".ueber");
  }

  _kopf() {
    if (!this._t || !this.shadowRoot) return;
    const t = this._t, kopf = this.shadowRoot.querySelector(".kopf");
    const chips = [["alle", t.alle, ""], ["ich", t.ich, ""]]
      .concat(this._mitglieder().filter((u) => u.ha_person_entity_id).map((u) => [u.ha_user_id, this._name(u.ha_user_id), u.ha_user_id]));
    const typen = [["alle", t.alleTypen, ""]].concat(Object.keys(HT_TYPEN).map((k) => [k, t.typen[k], HT_TYPEN[k]]));
    kopf.innerHTML = `
      <div class="titel"><ha-icon icon="mdi:hammer-screwdriver"></ha-icon><span>${htEsc(this._config.titel || "Tickets")}</span><b class="zahl"></b></div>
      <div class="werkzeug">
        <div class="filter">${chips.map(([k, n, u]) => `<button class="chip ${this._filter === k ? "an" : ""}" data-f="${htEsc(k)}">${u ? this._avatar(u, 20) : ""}${htEsc(n)}</button>`).join("")}</div>
        <div class="filter typen">${typen.map(([k, n, i]) => `<button class="chip ${this._typ === k ? "an" : ""}" data-y="${htEsc(k)}" title="${htEsc(n)}">${i ? `<ha-icon icon="${i}"></ha-icon>` : ""}<span>${htEsc(n)}</span></button>`).join("")}</div>
        <input class="suche" type="search" placeholder="${htEsc(t.suche)}" value="${htEsc(this._suche)}">
        <button class="plus" title="${htEsc(t.neu)}"><ha-icon icon="mdi:plus"></ha-icon><span>${htEsc(t.neu)}</span></button>
      </div>`;
    kopf.querySelectorAll(".chip[data-f]").forEach((b) => b.addEventListener("click", () => {
      this._filter = b.dataset.f;
      kopf.querySelectorAll(".chip[data-f]").forEach((x) => x.classList.toggle("an", x === b));
      this._board();
    }));
    kopf.querySelectorAll(".chip[data-y]").forEach((b) => b.addEventListener("click", () => {
      this._typ = b.dataset.y;
      kopf.querySelectorAll(".chip[data-y]").forEach((x) => x.classList.toggle("an", x === b));
      this._board();
    }));
    kopf.querySelector(".suche").addEventListener("input", (e) => { this._suche = e.target.value; this._board(); });
    kopf.querySelector(".plus").addEventListener("click", () => this._oeffnen(null));
    this._board();
  }

  // ---------- Board ----------
  _sichtbar(i) {
    const f = this._filter;
    if (f === "ich") { const ich = this._ich(); if (!i.assignee_ids.includes(ich) && i.requested_by_id !== ich) return false; }
    else if (f !== "alle" && !i.assignee_ids.includes(f)) return false;
    if (this._typ !== "alle" && i.type !== this._typ) return false;
    const q = this._suche.trim().toLowerCase();
    return !q || ("#" + i.id + " " + i.title + " " + (i.description || "")).toLowerCase().includes(q);
  }

  _board() {
    if (!this._t || !this.shadowRoot) return;
    const board = this.shadowRoot.querySelector(".board");
    if (!board) return;
    const t = this._t;
    if (!this._geladen) { board.innerHTML = `<p class="leer">${htEsc(t.laedt)}</p>`; return; }
    const alle = this._offen.concat(this._zu);
    const items = alle.filter((i) => this._sichtbar(i));
    const zahl = this.shadowRoot.querySelector(".kopf .zahl");
    if (zahl) zahl.textContent = this._offen.length ? String(this._offen.length) : "";
    // iPhone: Reiterleiste, eine Spalte zur Zeit (p3)
    if (!HT_SPALTEN.some((sp) => sp.key === this._spalte)) this._spalte = "new";
    const anzahl = (key) => items.filter((i) => htSpalte(i.status) === key).length;
    const reiter = `<nav class="tabs">${HT_SPALTEN.map((sp) => `<button class="tab ${this._spalte === sp.key ? "an" : ""}" data-t="${sp.key}" style="--sf:${sp.farbe}" title="${htEsc(t.spalten[sp.key])}" aria-label="${htEsc(t.spalten[sp.key])}"><ha-icon icon="${sp.icon}"></ha-icon><b>${anzahl(sp.key)}</b></button>`).join("")}</nav>`;
    board.innerHTML = reiter + HT_SPALTEN.map((sp) => {
      let liste = items.filter((i) => htSpalte(i.status) === sp.key);
      if (sp.archiv) liste.sort((a, b) => String(b.closed_at || b.updated_at).localeCompare(String(a.closed_at || a.updated_at)));
      else liste.sort((a, b) => (HT_DRINGLICH_RANG[a.urgency] - HT_DRINGLICH_RANG[b.urgency])
        || String(a.due_date || "9999").localeCompare(String(b.due_date || "9999")) || (a.id - b.id));
      const gesamt = liste.length;
      const auf = !!this._archivAuf[sp.key];
      if (sp.archiv && !auf && gesamt > this._config.archiv) liste = liste.slice(0, this._config.archiv);
      return `<section class="spalte ${this._spalte === sp.key ? "aktiv" : ""}" data-s="${sp.key}" style="--sf:${sp.farbe}">
        <header><ha-icon icon="${sp.icon}"></ha-icon><span>${htEsc(t.spalten[sp.key])}</span><b>${gesamt}</b></header>
        <div class="karten">${liste.map((i) => this._karte(i)).join("") || (sp.key === "new" && !alle.length ? `<p class="leer">${htEsc(t.leer)}</p>` : "")}</div>
        ${sp.archiv && gesamt > this._config.archiv ? `<button class="mehr" data-a="${sp.key}">${htEsc(auf ? t.weniger : t.mehr.replace("%n", gesamt))}</button>` : ""}
      </section>`;
    }).join("");
    this._bilderLaden(board);
  }

  _faellig(due) {
    if (!due) return "";
    const t = this._t;
    const heute = new Date(); heute.setHours(0, 0, 0, 0);
    const d = new Date(due + "T00:00:00");
    const tage = Math.round((d - heute) / 864e5);
    const text = tage < 0 ? t.ueberfaellig : tage === 0 ? t.heute : tage === 1 ? t.morgen : d.toLocaleDateString(this._hass.language || "de", { day: "numeric", month: "numeric" });
    return `<span class="faellig ${tage < 0 ? "rot" : tage <= 1 ? "gelb" : ""}"><ha-icon icon="mdi:calendar-clock"></ha-icon>${htEsc(text)}</span>`;
  }

  _karte(i) {
    const t = this._t;
    const zu = HT_GESCHLOSSEN.includes(i.status);
    const abnahme = i.status === "acceptance" ? `<span class="abn" title="${htEsc(t.abnahmeDurch)}">${this._avatar(i.requested_by_id, 18)}<ha-icon icon="mdi:check-bold"></ha-icon></span>` : "";
    const freigabe = i.type === "purchase" && (i.approval_state === "pending" || i.approval_state === "declined")
      ? `<span class="frei ${i.approval_state}"><ha-icon icon="${i.approval_state === "pending" ? "mdi:cart-arrow-right" : "mdi:cart-off"}"></ha-icon>${htEsc(i.approval_state === "pending" ? t.freigabe : t.freigabeAbgelehnt)}</span>` : "";
    const avs = i.assignee_ids.slice(0, 3).map((u) => this._avatar(u, 26)).join("");
    return `<article class="karte u-${i.urgency} ${zu ? "zu" : ""}" draggable="${htZiehbar()}" data-id="${i.id}" style="--pf:${HT_DRINGLICH[i.urgency]}">
      ${i.cover_attachment_id ? `<img class="cover" data-bild="${htEsc(i.cover_attachment_id)}" alt="">` : ""}
      <div class="zeile1"><ha-icon class="typ" icon="${HT_TYPEN[i.type]}" title="${htEsc(t.typen[i.type])}"></ha-icon><span class="kt"><span class="nr">#${i.id}</span> ${htEsc(i.title)}</span><span class="avs">${avs}</span></div>
      <div class="zeile2">
        ${i.urgency !== "someday" && !zu ? `<span class="prio">${htEsc(t.dringlich[i.urgency])}</span>` : ""}
        ${i.status === "waiting_for_parts" ? `<span class="wartet"><ha-icon icon="mdi:package-variant-closed"></ha-icon>${htEsc(t.wartet)}</span>` : ""}
        ${freigabe}
        ${zu ? "" : this._faellig(i.due_date)}
        ${i.importance ? `<span class="herz">${"♥".repeat(i.importance)}</span>` : ""}
        ${i.comment_count ? `<span class="kom"><ha-icon icon="mdi:comment-outline"></ha-icon>${i.comment_count}</span>` : ""}
        ${i.attachment_count ? `<span class="kom"><ha-icon icon="mdi:paperclip"></ha-icon>${i.attachment_count}</span>` : ""}
        ${abnahme}
      </div>
    </article>`;
  }

  // Vorschaubilder über die Integration laden (mit Anmeldung), einmal je Sitzung
  _bild(id, variante) {
    const key = id + "/" + variante;
    if (!HT_BILDER.has(key)) {
      HT_BILDER.set(key, (async () => {
        const r = await this._hass.fetchWithAuth(`/api/haushalttickets/attachments/${encodeURIComponent(id)}${variante === "thumbnail" ? "/thumbnail" : ""}`);
        if (!r.ok) throw new Error(String(r.status));
        return URL.createObjectURL(await r.blob());
      })());
      HT_BILDER.get(key).catch(() => HT_BILDER.delete(key));
    }
    return HT_BILDER.get(key);
  }

  _bilderLaden(wurzel) {
    wurzel.querySelectorAll("img[data-bild]").forEach((img) => {
      this._bild(img.dataset.bild, img.dataset.voll ? "" : "thumbnail")
        .then((url) => { img.src = url; })
        .catch(() => { img.remove(); });
    });
  }

  _klickBoard(e) {
    const reiter = e.target.closest(".tab");
    if (reiter) { this._spalte = reiter.dataset.t; this._board(); return; }
    const mehr = e.target.closest(".mehr");
    if (mehr) { this._archivAuf[mehr.dataset.a] = !this._archivAuf[mehr.dataset.a]; this._board(); return; }
    const k = e.target.closest(".karte");
    if (k) this._oeffnen(Number(k.dataset.id));
  }

  _ziehen() {
    const board = this.shadowRoot.querySelector(".board");
    board.addEventListener("dragstart", (e) => {
      const k = e.target.closest(".karte"); if (!k) return;
      e.dataTransfer.setData("text/plain", k.dataset.id); k.classList.add("zieht");
    });
    board.addEventListener("dragend", (e) => { const k = e.target.closest(".karte"); if (k) k.classList.remove("zieht"); });
    board.addEventListener("dragover", (e) => {
      const sp = e.target.closest(".spalte"); if (!sp) return;
      e.preventDefault();
      board.querySelectorAll(".spalte").forEach((s) => s.classList.toggle("ziel", s === sp));
    });
    board.addEventListener("dragleave", (e) => { if (!e.relatedTarget || !board.contains(e.relatedTarget)) board.querySelectorAll(".ziel").forEach((s) => s.classList.remove("ziel")); });
    board.addEventListener("drop", async (e) => {
      const sp = e.target.closest(".spalte"); if (!sp) return;
      e.preventDefault();
      board.querySelectorAll(".ziel").forEach((s) => s.classList.remove("ziel"));
      const id = Number(e.dataTransfer.getData("text/plain"));
      const item = this._offen.concat(this._zu).find((x) => x.id === id);
      if (item && htSpalte(item.status) !== sp.dataset.s) {
        await this._aktion(() => this._ws("POST", `/api/v1/tickets/${id}/status`, { body: { status: sp.dataset.s } }));
        this._laden();
      }
    });
  }

  // ---------- Dialog ----------
  async _oeffnen(id) {
    const neu = id == null;
    const ueber = this._portal();
    this._dialog = { id: neu ? null : id };
    if (neu) {
      this._dialog.w = { type: this._typ !== "alle" ? this._typ : "repair", title: "", description: "", urgency: "someday", importance: null, due_date: null, assignee_ids: [], felder: {}, dateien: [] };
      this._dialogZeichnen(ueber);
      return;
    }
    ueber.innerHTML = `<div class="schleier"></div><div class="dlg"><p class="leer">${htEsc(this._t.laedt)}</p></div>`;
    ueber.querySelector(".schleier").addEventListener("click", () => this._zu_());
    if (!(await this._dialogNachladen())) this._zu_();
  }

  async _dialogNachladen() {
    const d = this._dialog;
    if (!d || !d.id) return false;
    try {
      const base = `/api/v1/tickets/${d.id}`;
      const [ticket, kommentare, verlauf, anhaenge] = await Promise.all([
        this._ws("GET", base), this._ws("GET", base + "/comments"), this._ws("GET", base + "/history"), this._ws("GET", base + "/attachments"),
      ]);
      if (this._dialog !== d) return true;
      const offenerEntwurf = d.w && d.ticket && d.ticket.id === ticket.id;
      d.ticket = ticket; d.kommentare = kommentare; d.verlauf = verlauf; d.anhaenge = anhaenge;
      if (offenerEntwurf) { this._nebenbereicheZeichnen(); return true; }   // Eingaben im offenen Dialog nicht überschreiben
      d.w = {
        type: ticket.type, title: ticket.title, description: ticket.description || "", status: ticket.status,
        urgency: ticket.urgency, importance: ticket.importance, due_date: ticket.due_date, assignee_ids: [...ticket.assignee_ids],
        ai_draft_requested: ticket.ai_draft_requested, ai_implement_approved: ticket.ai_implement_approved,
        felder: Object.fromEntries((HT_TYPFELDER[ticket.type] || []).map((f) => [f, ticket[f]])),
      };
      this._dialogZeichnen(this._portal());
      return true;
    } catch (e) {
      this._hinweis(e);
      return false;
    }
  }

  _zu_() {
    const ueber = this._pt && this._pt.shadowRoot.querySelector(".ueber");
    if (ueber) ueber.innerHTML = "";
    this._dialog = null;
    clearTimeout(this._wegTimer);
    if (this._pt) this._pt.remove();
  }

  _feld(name, label, typ, wert, extra = "") {
    return `<div class="feld"><label>${htEsc(label)}</label><input data-feld="${name}" type="${typ}" value="${htEsc(wert == null ? "" : wert)}" ${extra}></div>`;
  }

  _typfelder(w) {
    const t = this._t, f = w.felder;
    const bereiche = Object.values((this._hass && this._hass.areas) || {}).sort((a, b) => String(a.name).localeCompare(String(b.name)));
    const bereich = (HT_TYPFELDER[w.type] || []).includes("ha_area_id")
      ? `<div class="feld"><label>${htEsc(t.bereich)}</label><select data-feld="ha_area_id"><option value="">${htEsc(t.keinBereich)}</option>${bereiche.map((a) => `<option value="${htEsc(a.area_id)}" ${f.ha_area_id === a.area_id ? "selected" : ""}>${htEsc(a.name)}</option>`).join("")}</select></div>` : "";
    if (w.type === "purchase") {
      return `<div class="raster">
        ${this._feld("price_estimated", t.preisGeschaetzt, "number", f.price_estimated, 'step="0.01" min="0" inputmode="decimal"')}
        ${this._feld("price_actual", t.preisTatsaechlich, "number", f.price_actual, 'step="0.01" min="0" inputmode="decimal"')}
        ${this._feld("vendor", t.haendler, "text", f.vendor)}
        ${this._feld("warranty_until", t.garantieBis, "date", f.warranty_until)}
      </div>${this._feld("product_url", t.link, "url", f.product_url, 'placeholder="https://"')}`;
    }
    if (w.type === "repair") {
      const kaeufe = this._offen.concat(this._zu).filter((x) => x.type === "purchase");
      return `${bereich}<div class="raster">
        ${this._feld("contractor_name", t.handwerker, "text", f.contractor_name)}
        ${this._feld("contractor_phone", t.telefon, "tel", f.contractor_phone)}
        ${this._feld("contractor_appointment", t.termin, "datetime-local", htLokal(f.contractor_appointment))}
        ${this._feld("cost_estimated", t.kostenGeschaetzt, "number", f.cost_estimated, 'step="0.01" min="0" inputmode="decimal"')}
        ${this._feld("cost_actual", t.kostenTatsaechlich, "number", f.cost_actual, 'step="0.01" min="0" inputmode="decimal"')}
        <div class="feld"><label>${htEsc(t.bezug)}</label><select data-feld="related_purchase_id"><option value="">${htEsc(t.keinBezug)}</option>${kaeufe.map((k) => `<option value="${k.id}" ${Number(f.related_purchase_id) === k.id ? "selected" : ""}>#${k.id} ${htEsc(k.title)}</option>`).join("")}</select></div>
      </div><label class="haken"><input type="checkbox" data-feld="warranty_case" ${f.warranty_case ? "checked" : ""}>${htEsc(t.garantiefall)}</label>`;
    }
    return `${bereich}${this._feld("ha_targets", t.entitaeten, "text", (f.ha_targets || []).join(", "), 'placeholder="light.flur, automation.flurlicht"')}`;
  }

  _dialogZeichnen(ueber) {
    const t = this._t, d = this._dialog, w = d.w, neu = !d.id, tk = d.ticket;
    const erwachsen = this._erwachsen();
    const mitglieder = this._mitglieder();
    const statusWert = htSpalte(w.status || "new");
    const freigabe = !neu && w.type === "purchase" && tk.approval_state !== "not_required" ? this._freigabeBox(tk) : "";
    ueber.innerHTML = `<div class="schleier"></div><div class="dlg" role="dialog">
      <div class="dkopf">${neu ? "" : `<span class="dnr">#${d.id}</span>`}<input class="dtitel" placeholder="${htEsc(t.titel)}" value="${htEsc(w.title)}"><button class="zu" title="${htEsc(t.schliessen)}"><ha-icon icon="mdi:close"></ha-icon></button></div>
      ${neu ? "" : `<div class="von"><ha-icon icon="${HT_TYPEN[w.type]}"></ha-icon>${htEsc(t.typen[w.type])} · ${htEsc(t.gewuenscht)} ${htEsc(this._name(tk.requested_by_id))} · ${htEsc(new Date(tk.created_at).toLocaleDateString(this._hass.language || "de"))}</div>`}
      ${neu ? `<label>${htEsc(t.typ)}</label><div class="seg ty">${Object.keys(HT_TYPEN).map((k) => `<button data-v="${k}" class="${w.type === k ? "an" : ""}"><ha-icon icon="${HT_TYPEN[k]}"></ha-icon>${htEsc(t.typen[k])}</button>`).join("")}</div>` : ""}
      ${freigabe}
      <textarea class="dtext" rows="3" placeholder="${htEsc(t.beschreibung)}">${htEsc(w.description)}</textarea>
      ${neu ? "" : `<label>${htEsc(t.status)}</label>
      <div class="seg st">${HT_SPALTEN.map((s) => `<button data-v="${s.key}" style="--sf:${s.farbe}" class="${statusWert === s.key ? "an" : ""}"><ha-icon icon="${s.icon}"></ha-icon>${htEsc(t.spalten[s.key])}</button>`).join("")}</div>
      <label class="haken wartet ${statusWert === "in_progress" ? "" : "aus"}"><input type="checkbox" class="wt" ${w.status === "waiting_for_parts" ? "checked" : ""}>${htEsc(t.wartet)}</label>`}
      <label>${htEsc(t.dringlichkeit)}</label>
      <div class="seg pr">${Object.keys(HT_DRINGLICH).map((p) => `<button data-v="${p}" style="--sf:${HT_DRINGLICH[p]}" class="${w.urgency === p ? "an" : ""}">${htEsc(t.dringlich[p])}</button>`).join("")}</div>
      ${mitglieder.length && (erwachsen || !neu) ? `<label>${htEsc(t.zustaendig)}</label>
      <div class="seg pe ${erwachsen ? "" : "nurlesen"}">${mitglieder.map((u) => `<button data-v="${htEsc(u.ha_user_id)}" class="${w.assignee_ids.includes(u.ha_user_id) ? "an" : ""}" ${erwachsen ? "" : "disabled"}>${this._avatar(u.ha_user_id, 20)}${htEsc(this._name(u.ha_user_id))}</button>`).join("")}${!erwachsen && !w.assignee_ids.length ? `<span class="hinweis">${htEsc(t.niemand)}</span>` : ""}</div>` : ""}
      <div class="zwei">
        <div><label>${htEsc(t.faellig)}</label>
          <div class="seg du"><button data-v="">${htEsc(t.keinTermin)}</button><button data-v="7">${htEsc(t.woche)}</button><button data-v="14">${htEsc(t.zweiWochen)}</button><button data-v="30">${htEsc(t.monat)}</button></div>
          <input class="datum" type="date" value="${htEsc(w.due_date || "")}"></div>
        <div><label>${htEsc(t.herz)}</label><div class="herzen">${[1, 2, 3, 4, 5].map((h) => `<button data-v="${h}" class="${h <= (w.importance || 0) ? "an" : ""}">♥</button>`).join("")}</div></div>
      </div>
      <details class="mehrfelder" ${neu ? "" : "open"}><summary>${htEsc(t.details)}</summary><div class="typfelder">${this._typfelder(w)}</div></details>
      ${erwachsen && !neu ? `<label>${htEsc(t.ki)}</label><div class="kiflags">
        <label class="haken"><input type="checkbox" class="kd" ${w.ai_draft_requested ? "checked" : ""}>${htEsc(t.kiEntwurf)}</label>
        ${w.type === "home_assistant" ? `<label class="haken"><input type="checkbox" class="ku" ${w.ai_implement_approved ? "checked" : ""}>${htEsc(t.kiUmsetzen)}</label>` : ""}
      </div>` : ""}
      ${neu ? `<label>${htEsc(t.fotosNachher)}</label><div class="hoch"><label class="knopf"><ha-icon icon="mdi:camera-plus-outline"></ha-icon>${htEsc(t.foto)}<input type="file" class="vorab" accept="image/*,application/pdf" multiple hidden></label><span class="vorabliste">${htEsc(w.dateien.map((f) => f.name).join(", "))}</span></div>`
        : `<div class="neben"></div>`}
      <div class="fuss">${!neu && this._rolle() === "admin" ? `<button class="weg">${htEsc(t.loeschen)}</button>` : ""}<span></span><button class="ok">${htEsc(neu ? t.anlegen : t.speichern)}</button></div>
    </div>`;
    const dlg = ueber.querySelector(".dlg");
    this._dialogVerdrahten(ueber, dlg);
    if (!neu) this._nebenbereicheZeichnen();
    else setTimeout(() => { const f = dlg.querySelector(".dtitel"); if (f) f.focus(); }, 50);
  }

  _freigabeBox(tk) {
    const t = this._t;
    const kann = this._erwachsen() && tk.requested_by_id !== this._ich() && ["pending", "declined"].includes(tk.approval_state);
    const text = tk.approval_state === "pending" ? t.freigabeOffen : tk.approval_state === "declined" ? t.freigabeAbgelehnt : t.freigegeben;
    const wer = tk.approval_by_id ? " · " + this._name(tk.approval_by_id) : "";
    return `<div class="freigabe ${tk.approval_state}"><ha-icon icon="mdi:cart-check"></ha-icon><span>${htEsc(text + wer)}</span>
      ${kann ? `<button class="fg" data-d="approve">${htEsc(t.freigeben)}</button>${tk.approval_state === "pending" ? `<button class="fg nein" data-d="decline">${htEsc(t.ablehnen)}</button>` : ""}` : ""}</div>`;
  }

  _dialogVerdrahten(ueber, d) {
    const t = this._t, w = this._dialog.w, neu = !this._dialog.id;
    const seg = (cls, fn) => d.querySelectorAll(`.${cls} button`).forEach((b) => b.addEventListener("click", () => {
      fn(b.dataset.v, b);
    }));
    const einzeln = (cls, feld) => seg(cls, (v, b) => { w[feld] = v; d.querySelectorAll(`.${cls} button`).forEach((x) => x.classList.toggle("an", x === b)); });
    einzeln("pr", "urgency");
    seg("ty", (v, b) => {
      w.type = v; w.felder = {};
      d.querySelectorAll(".ty button").forEach((x) => x.classList.toggle("an", x === b));
      d.querySelector(".typfelder").innerHTML = this._typfelder(w);
    });
    seg("st", (v, b) => {
      w.status = v === "in_progress" && d.querySelector(".wt") && d.querySelector(".wt").checked ? "waiting_for_parts" : v;
      d.querySelectorAll(".st button").forEach((x) => x.classList.toggle("an", x === b));
      d.querySelector(".wartet").classList.toggle("aus", v !== "in_progress");
    });
    const wt = d.querySelector(".wt");
    if (wt) wt.addEventListener("change", () => { if (htSpalte(w.status) === "in_progress") w.status = wt.checked ? "waiting_for_parts" : "in_progress"; });
    if (this._erwachsen()) seg("pe", (v, b) => {
      w.assignee_ids = w.assignee_ids.includes(v) ? w.assignee_ids.filter((x) => x !== v) : w.assignee_ids.concat([v]);
      b.classList.toggle("an", w.assignee_ids.includes(v));
    });
    d.querySelectorAll(".du button").forEach((b) => b.addEventListener("click", () => {
      const v = b.dataset.v;
      if (!v) w.due_date = null; else { const x = new Date(); x.setDate(x.getDate() + Number(v)); w.due_date = x.toISOString().slice(0, 10); }
      d.querySelector(".datum").value = w.due_date || "";
    }));
    d.querySelector(".datum").addEventListener("change", (e) => { w.due_date = e.target.value || null; });
    d.querySelectorAll(".herzen button").forEach((b) => b.addEventListener("click", () => {
      const v = Number(b.dataset.v); w.importance = w.importance === v ? null : v;
      d.querySelectorAll(".herzen button").forEach((x) => x.classList.toggle("an", Number(x.dataset.v) <= (w.importance || 0)));
    }));
    d.querySelectorAll(".fg").forEach((b) => b.addEventListener("click", async () => {
      if (await this._aktion(() => this._ws("POST", `/api/v1/tickets/${this._dialog.id}/approval`, { body: { decision: b.dataset.d } }))) {
        this._dialog.w = null; await this._dialogNachladen(); this._laden();
      }
    }));
    const vorab = d.querySelector(".vorab");
    if (vorab) vorab.addEventListener("change", () => {
      const gewaehlt = [...vorab.files];
      vorab.value = "";
      const liste = d.querySelector(".vorabliste");
      liste.textContent = t.liest;
      // sofort lesen: in der iOS-App ist die Datei beim Speichern sonst oft nicht mehr lesbar
      w.einlesen = (w.einlesen || Promise.resolve()).then(async () => {
        for (const datei of gewaehlt) {
          try { w.dateien.push(await this._einlesen(datei)); } catch (e) { this._hinweis({ message: `${t.nichtLesbar}: ${datei.name}` }); }
        }
        liste.textContent = w.dateien.map((f) => f.name).join(", ");
      });
    });
    ueber.querySelector(".schleier").addEventListener("click", () => this._zu_());
    d.querySelector(".zu").addEventListener("click", () => this._zu_());
    d.addEventListener("keydown", (e) => { if (e.key === "Escape") this._zu_(); });
    const ok = d.querySelector(".ok");
    ok.addEventListener("click", async () => {
      w.title = d.querySelector(".dtitel").value.trim();
      w.description = d.querySelector(".dtext").value;
      if (!w.title) { d.querySelector(".dtitel").focus(); return; }
      this._felderLesen(d, w);
      ok.disabled = true;
      const fertig = neu ? await this._anlegen(w) : await this._speichern(w);
      ok.disabled = false;
      if (fertig) { this._zu_(); this._laden(); }
    });
    const weg = d.querySelector(".weg");
    if (weg) weg.addEventListener("click", async () => {
      // zweistufiges Löschen im Dialog statt confirm() (p5)
      if (!weg.classList.contains("sicher")) {
        weg.classList.add("sicher"); weg.textContent = t.loeschenFrage;
        clearTimeout(this._wegTimer);
        this._wegTimer = setTimeout(() => { weg.classList.remove("sicher"); weg.textContent = t.loeschen; }, 4000);
        return;
      }
      clearTimeout(this._wegTimer);
      if (await this._aktion(() => this._ws("DELETE", `/api/v1/tickets/${this._dialog.id}`))) { this._zu_(); this._laden(); }
    });
  }

  _felderLesen(d, w) {
    d.querySelectorAll(".typfelder [data-feld]").forEach((el) => {
      const name = el.dataset.feld;
      let v = el.type === "checkbox" ? el.checked : el.value.trim();
      if (name === "ha_targets") v = v ? v.split(",").map((x) => x.trim()).filter(Boolean) : [];
      else if (name === "contractor_appointment") v = v ? new Date(v).toISOString() : null;
      else if (name === "related_purchase_id") v = v ? Number(v) : null;
      else if (el.type !== "checkbox" && v === "") v = null;
      w.felder[name] = v;
    });
    const kd = d.querySelector(".kd"), ku = d.querySelector(".ku");
    if (kd) w.ai_draft_requested = kd.checked;
    if (ku) w.ai_implement_approved = ku.checked;
  }

  // Geld als Zeichenkette mit zwei Stellen, wie der Server es zurückgibt
  _wert(name, v) {
    if (v == null) return null;
    if (/(price|cost)_/.test(name)) return Number(v).toFixed(2);
    return v;
  }

  async _anlegen(w) {
    const body = { type: w.type, title: w.title, urgency: w.urgency };
    if (w.description.trim()) body.description = w.description;
    if (w.importance) body.importance = w.importance;
    if (w.due_date) body.due_date = w.due_date;
    if (w.assignee_ids.length) body.assignee_ids = w.assignee_ids;
    for (const [k, v] of Object.entries(w.felder)) {
      if (v != null && v !== false && !(Array.isArray(v) && !v.length)) body[k] = this._wert(k, v);
    }
    let ticket;
    if (!(await this._aktion(async () => { ticket = await this._ws("POST", "/api/v1/tickets", { body }); }))) return false;
    await w.einlesen;
    for (const datei of w.dateien) await this._hochladen(ticket.id, datei, null);
    return true;
  }

  async _speichern(w) {
    const tk = this._dialog.ticket;
    const patch = {};
    const vergleich = { title: w.title, description: w.description, urgency: w.urgency, importance: w.importance, due_date: w.due_date };
    for (const [k, v] of Object.entries(vergleich)) if ((tk[k] ?? null) !== (v ?? null)) patch[k] = v;
    for (const [k, v] of Object.entries(w.felder)) {
      const alt = tk[k] ?? null, neu = this._wert(k, v);
      const gleich = Array.isArray(alt) ? JSON.stringify(alt) === JSON.stringify(neu || []) :
        k === "contractor_appointment" ? htLokal(alt) === htLokal(neu) : String(alt ?? "") === String(neu ?? "");
      if (!gleich) patch[k] = neu;
    }
    const id = this._dialog.id, base = `/api/v1/tickets/${id}`;
    if (Object.keys(patch).length && !(await this._aktion(() => this._ws("PATCH", base, { body: patch })))) return false;
    const zuAlt = [...tk.assignee_ids].sort().join(), zuNeu = [...w.assignee_ids].sort().join();
    if (zuAlt !== zuNeu && !(await this._aktion(() => this._ws("PUT", base + "/assignees", { body: { user_ids: w.assignee_ids } })))) return false;
    const flags = {};
    if (w.ai_draft_requested !== tk.ai_draft_requested) flags.draft_requested = w.ai_draft_requested;
    if (w.type === "home_assistant" && w.ai_implement_approved !== tk.ai_implement_approved) flags.implement_approved = w.ai_implement_approved;
    if (Object.keys(flags).length && !(await this._aktion(() => this._ws("POST", base + "/ai-flags", { body: flags })))) return false;
    if (w.status && w.status !== tk.status && !(await this._aktion(() => this._ws("POST", base + "/status", { body: { status: w.status } })))) return false;
    return true;
  }

  // ---------- Checkliste, Anhänge, Verlauf (im geöffneten Ticket) ----------
  _nebenbereicheZeichnen() {
    const box = this._pt && this._pt.shadowRoot.querySelector(".ueber .neben");
    const d = this._dialog;
    if (!box || !d || !d.ticket) return;
    const t = this._t, tk = d.ticket, ich = this._ich(), admin = this._rolle() === "admin";
    const bild = (a) => a.content_type.startsWith("image/");
    box.innerHTML = `
      <label>${htEsc(t.checkliste)}</label>
      <div class="liste">${tk.checklist.map((c) => `<div class="punkt" data-c="${c.id}"><label class="haken"><input type="checkbox" ${c.done ? "checked" : ""}><span>${htEsc(c.text)}</span></label><button class="x" title="${htEsc(t.loeschen)}"><ha-icon icon="mdi:close"></ha-icon></button></div>`).join("")}
        <div class="punktneu"><input type="text" placeholder="${htEsc(t.punkt)}"><button class="add"><ha-icon icon="mdi:plus"></ha-icon></button></div></div>
      <label>${htEsc(t.anhaenge)}</label>
      <div class="anhaenge">${d.anhaenge.map((a) => `<div class="anhang" data-a="${htEsc(a.id)}">
          ${bild(a) ? `<img data-bild="${htEsc(a.id)}" alt="${htEsc(a.filename)}">` : `<ha-icon icon="${a.kind === "invoice" ? "mdi:receipt-text-outline" : "mdi:file-document-outline"}"></ha-icon><span>${htEsc(a.filename)}</span>`}
          ${a.uploaded_by_id === ich || admin ? `<button class="x" title="${htEsc(t.loeschen)}"><ha-icon icon="mdi:close"></ha-icon></button>` : ""}
        </div>`).join("")}</div>
      <div class="hoch">
        <label class="knopf"><ha-icon icon="mdi:camera-plus-outline"></ha-icon>${htEsc(t.foto)}<input type="file" data-k="photo" accept="image/*" multiple hidden></label>
        ${tk.type === "purchase" ? `<label class="knopf"><ha-icon icon="mdi:receipt-text-outline"></ha-icon>${htEsc(t.rechnung)}<input type="file" data-k="invoice" accept="image/*,application/pdf" hidden></label>` : ""}
        <label class="knopf"><ha-icon icon="mdi:paperclip"></ha-icon>${htEsc(t.datei)}<input type="file" data-k="document" accept="image/*,application/pdf" hidden></label>
        <span class="status"></span>
      </div>
      <div class="bildgross" hidden><img alt=""><button class="x"><ha-icon icon="mdi:close"></ha-icon></button></div>
      <label>${htEsc(t.verlauf)}</label><div class="verlauf"></div>
      <div class="komm"><textarea rows="2" placeholder="${htEsc(t.kommentar)}"></textarea><button class="senden" title="${htEsc(t.senden)}"><ha-icon icon="mdi:send"></ha-icon></button></div>`;
    this._bilderLaden(box);
    this._verlaufZeigen(box.querySelector(".verlauf"));
    const base = `/api/v1/tickets/${tk.id}`;
    const nachher = async () => { await this._dialogNachladen(); this._laden(); };
    box.querySelectorAll(".punkt").forEach((p) => {
      const cid = p.dataset.c;
      p.querySelector("input").addEventListener("change", async (e) => {
        if (await this._aktion(() => this._ws("PATCH", `${base}/checklist/${cid}`, { body: { done: e.target.checked } }))) nachher();
        else e.target.checked = !e.target.checked;
      });
      p.querySelector(".x").addEventListener("click", async () => {
        if (await this._aktion(() => this._ws("DELETE", `${base}/checklist/${cid}`))) nachher();
      });
    });
    const feld = box.querySelector(".punktneu input");
    const hinzu = async () => {
      const text = feld.value.trim(); if (!text) return;
      if (await this._aktion(() => this._ws("POST", `${base}/checklist`, { body: { text } }))) { feld.value = ""; nachher(); }
    };
    box.querySelector(".punktneu .add").addEventListener("click", hinzu);
    feld.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); hinzu(); } });
    box.querySelectorAll(".anhang").forEach((el) => {
      const a = d.anhaenge.find((x) => x.id === el.dataset.a);
      const x = el.querySelector(".x");
      if (x) x.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!x.classList.contains("sicher")) { x.classList.add("sicher"); x.title = t.entfernen; setTimeout(() => x.classList.remove("sicher"), 4000); return; }
        if (await this._aktion(() => this._ws("DELETE", `/api/v1/attachments/${a.id}`))) nachher();
      });
      el.addEventListener("click", async () => {
        if (bild(a)) {
          const gross = box.querySelector(".bildgross");
          gross.hidden = false;
          gross.querySelector("img").src = await this._bild(a.id, "thumbnail");
          this._bild(a.id, "").then((url) => { gross.querySelector("img").src = url; }).catch(() => {});
        } else {
          const fenster = window.open("", "_blank");   // sofort öffnen, sonst blockiert Safari das Fenster
          try {
            const r = await this._hass.callWS({ type: "auth/sign_path", path: `/api/haushalttickets/attachments/${a.id}`, expires: 300 });
            if (fenster) fenster.location = r.path; else window.location.assign(r.path);
          } catch (e) { if (fenster) fenster.close(); this._hinweis(e); }
        }
      });
    });
    const gross = box.querySelector(".bildgross");
    gross.addEventListener("click", () => { gross.hidden = true; });
    box.querySelectorAll(".hoch input[type=file]").forEach((inp) => inp.addEventListener("change", async () => {
      const dateien = [...inp.files]; inp.value = "";
      const status = box.querySelector(".hoch .status");
      status.textContent = t.laedtHoch;
      for (const datei of dateien) await this._hochladen(tk.id, datei, inp.dataset.k);
      status.textContent = "";
      nachher();
    }));
    const senden = box.querySelector(".senden");
    senden.addEventListener("click", async () => {
      const ta = box.querySelector(".komm textarea"); const text = ta.value.trim(); if (!text) return;
      senden.disabled = true;
      if (await this._aktion(() => this._ws("POST", `${base}/comments`, { body: { body: text } }))) { ta.value = ""; nachher(); }
      senden.disabled = false;
    });
  }

  // Fotos vor dem Hochladen verkleinern (max. 2560 px, JPEG) – spart Datenvolumen unterwegs
  async _verkleinern(datei) {
    if (!datei.type.startsWith("image/") || datei.type === "image/gif" || !window.createImageBitmap) return datei;
    try {
      const bild = await createImageBitmap(datei, { imageOrientation: "from-image" });
      const faktor = Math.min(1, 2560 / Math.max(bild.width, bild.height));
      if (faktor === 1 && datei.size < 3e6 && datei.type === "image/jpeg") return datei;
      const c = document.createElement("canvas");
      c.width = Math.round(bild.width * faktor); c.height = Math.round(bild.height * faktor);
      c.getContext("2d").drawImage(bild, 0, 0, c.width, c.height);
      const blob = await new Promise((ok) => c.toBlob(ok, "image/jpeg", 0.85));
      if (!blob) return datei;
      return new File([blob], datei.name.replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg" });
    } catch (e) {
      return datei;
    }
  }

  // Datei vollständig in den Speicher holen (Fotos dabei verkleinern)
  async _einlesen(datei) {
    if (HT_EINGELESEN.has(datei)) return datei;
    let fertig = await this._verkleinern(datei);
    if (fertig === datei) fertig = new File([await datei.arrayBuffer()], datei.name, { type: datei.type || "application/octet-stream" });
    HT_EINGELESEN.add(fertig);
    return fertig;
  }

  async _hochladen(ticketId, datei, art) {
    const t = this._t;
    let fertig;
    try { fertig = await this._einlesen(datei); } catch (e) { this._hinweis({ message: `${t.nichtLesbar}: ${datei.name}` }); return false; }
    // Home Assistant nimmt höchstens 16 MiB je Anfrage an und bricht größere mitten im Senden ab
    if (fertig.size > HT_MAX_UPLOAD) { this._hinweis({ code: "too_large" }); return false; }
    const form = new FormData();
    form.append("file", fertig, fertig.name);
    form.append("kind", art || (fertig.type.startsWith("image/") ? "photo" : "document"));
    try {
      const r = await this._hass.fetchWithAuth(`/api/haushalttickets/tickets/${ticketId}/attachments`, { method: "POST", body: form });
      if (!r.ok) {
        let fehler = { message: String(r.status) };
        try { const j = await r.json(); fehler = { code: j.code, message: j.message || j.detail || String(r.status) }; } catch (e) { /* kein JSON */ }
        this._hinweis(fehler);
        return false;
      }
      return true;
    } catch (e) {
      const mb = (fertig.size / 1048576).toLocaleString(this._hass.language || "de", { maximumFractionDigits: 1 });
      this._hinweis({ message: `${t.hochladenFehler}: ${fertig.name} (${mb} MB) – ${(e && e.message) || e}` });
      return false;
    }
  }

  _verlaufZeigen(box) {
    const d = this._dialog;
    if (!box || !d) return;
    const t = this._t;
    const eintraege = d.kommentare.map((c) => ({ at: c.created_at, wer: c.author_id, via: c.via, k: c.body }))
      .concat(d.verlauf.map((h) => ({ at: h.at, wer: h.actor_id, via: h.via, h })))
      .sort((a, b) => String(b.at).localeCompare(String(a.at)));
    box.innerHTML = eintraege.map((x) => {
      const wann = new Date(x.at).toLocaleString(this._hass.language || "de", { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" });
      const ki = x.via === "mcp" || x.via === "nightly_run" ? ` · ${t.via_ai}` : "";
      let was;
      if (x.k != null) was = `<p>${htEsc(x.k).replace(/\n/g, "<br>")}</p>`;
      else if (x.h.field === "status") was = `<i>${htEsc(t.statusAuf + " " + (x.h.new === "waiting_for_parts" ? t.wartet : t.spalten[x.h.new] || x.h.new))}</i>`;
      else if (x.h.field === "created") was = `<i>${htEsc(t.angelegt)}</i>`;
      else was = `<i>${htEsc((t.felder[x.h.field] || HT_TEXTE.de.felder[x.h.field] || x.h.field) + " " + t.geaendert)}</i>`;
      return `<div class="ve ${x.k != null ? "k" : ""}"><span>${htEsc(x.wer ? this._name(x.wer) : "System")}${htEsc(ki)} · ${htEsc(wann)}</span>${was}</div>`;
    }).join("");
  }

  _fehler(text) {
    const b = this.shadowRoot && this.shadowRoot.querySelector(".board");
    if (b) b.innerHTML = `<p class="fehler">${htEsc(text)}</p>`;
  }
}

const HT_CSS = `
:host { --tb-glas: rgba(255,255,255,0.045); --tb-rand: rgba(127,127,127,0.18); }
ha-card { padding: 14px 14px 16px; overflow: hidden; }
.kopf .titel { display:flex; align-items:center; gap:10px; font-size:20px; font-weight:600; margin:2px 2px 12px; }
.kopf .titel ha-icon { color: var(--primary-color); }
.kopf .zahl { margin-left:auto; min-width:26px; padding:2px 9px; border-radius:12px; background: var(--primary-color); color: var(--text-primary-color, #fff); font-size:14px; text-align:center; }
.kopf .zahl:empty { display:none; }
.werkzeug { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:12px; }
.filter { display:flex; gap:6px; flex-wrap:wrap; }
.chip { display:inline-flex; align-items:center; gap:6px; border:1px solid var(--tb-rand); background:var(--tb-glas); color:var(--primary-text-color); border-radius:18px; padding:5px 12px; font:inherit; font-size:13px; cursor:pointer; }
.chip ha-icon { --mdc-icon-size:16px; }
.chip.an { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
.suche { flex:1; min-width:120px; border:1px solid var(--tb-rand); background:var(--tb-glas); color:var(--primary-text-color); border-radius:18px; padding:7px 14px; font:inherit; font-size:13px; outline:none; }
.plus { display:inline-flex; align-items:center; gap:4px; border:0; background:var(--primary-color); color:var(--text-primary-color,#fff); border-radius:18px; padding:6px 14px 6px 10px; font:inherit; font-size:13px; font-weight:600; cursor:pointer; }
.plus ha-icon { --mdc-icon-size:18px; }
.board { display:grid; grid-auto-flow:column; grid-auto-columns:minmax(220px,1fr); gap:10px; overflow-x:auto; scroll-snap-type:x mandatory; padding-bottom:4px; }
@media (max-width: 600px) { .werkzeug .suche { order: 3; flex-basis: 100%; } .typen .chip span { display:none; } }
.spalte { scroll-snap-align:start; background:var(--tb-glas); border:1px solid var(--tb-rand); border-radius:16px; padding:10px; min-height:120px; transition: box-shadow .15s, border-color .15s; }
.spalte.ziel { border-color: var(--sf); box-shadow: 0 0 0 2px var(--sf) inset; }
.spalte header { display:flex; align-items:center; gap:8px; font-size:13px; font-weight:600; letter-spacing:.3px; color:var(--sf); margin:2px 2px 10px; text-transform:uppercase; }
.spalte header ha-icon { --mdc-icon-size:18px; }
.spalte header b { margin-left:auto; background:color-mix(in srgb, var(--sf) 22%, transparent); color:var(--primary-text-color); border-radius:10px; padding:1px 8px; font-size:12px; }
.karten { display:flex; flex-direction:column; gap:8px; }
.karte { position:relative; background: var(--ha-card-background, var(--card-background-color)); border:1px solid var(--tb-rand); border-left:4px solid var(--pf); border-radius:12px; padding:10px 10px 9px 11px; cursor:pointer; transition: transform .12s, box-shadow .12s; overflow:hidden; }
.karte.zu { opacity:.8; }
@media (hover: hover) { .karte:hover { transform: translateY(-1px); box-shadow: 0 4px 14px rgba(0,0,0,.18); } }
.karte.zieht { opacity:.45; }
.karte.u-burning:not(.zu) { box-shadow: 0 0 0 1px color-mix(in srgb, #ef5350 45%, transparent), 0 0 14px color-mix(in srgb, #ef5350 25%, transparent); }
.cover { display:block; width: calc(100% + 21px); margin: -10px -10px 8px -11px; height:110px; object-fit:cover; background:var(--tb-glas); }
.zeile1 { display:flex; gap:8px; align-items:flex-start; }
.typ { --mdc-icon-size:16px; color:var(--secondary-text-color); margin-top:1px; flex:none; }
.kt { flex:1; font-size:14px; font-weight:500; line-height:1.3; color:var(--primary-text-color); word-break:break-word; }
.nr { color:var(--secondary-text-color); font-weight:400; font-size:12px; }
.avs { display:flex; flex:none; } .avs .av + .av { margin-left:-8px; box-shadow:0 0 0 2px var(--ha-card-background, var(--card-background-color)); }
.zeile2 { display:flex; flex-wrap:wrap; gap:6px; margin-top:7px; align-items:center; font-size:11.5px; color:var(--secondary-text-color); }
.zeile2:empty { display:none; }
.prio { background: color-mix(in srgb, var(--pf) 20%, transparent); color: var(--pf); border-radius:8px; padding:1px 7px; font-weight:600; }
.wartet, .frei { display:inline-flex; align-items:center; gap:3px; border-radius:8px; padding:1px 7px; font-weight:600; background: color-mix(in srgb, #ffb74d 18%, transparent); color:#ffb74d; }
.frei.declined { background: color-mix(in srgb, #ef5350 18%, transparent); color:#ef5350; }
.wartet ha-icon, .frei ha-icon { --mdc-icon-size:13px; }
.faellig, .kom { display:inline-flex; align-items:center; gap:3px; }
.faellig ha-icon, .kom ha-icon { --mdc-icon-size:14px; }
.faellig.rot { color:#ef5350; font-weight:600; } .faellig.gelb { color:#ffb74d; font-weight:600; }
.herz { color:#f06292; letter-spacing:-1px; }
.abn { display:inline-flex; align-items:center; gap:2px; margin-left:auto; color:#81c784; } .abn ha-icon { --mdc-icon-size:14px; }
.av { border-radius:50%; object-fit:cover; flex:none; display:inline-flex; align-items:center; justify-content:center; color:#fff; font-weight:700; }
.leer { color:var(--secondary-text-color); font-size:13px; text-align:center; margin:18px 0; }
.mehr { width:100%; margin-top:8px; border:1px dashed var(--tb-rand); background:none; color:var(--secondary-text-color); border-radius:10px; padding:6px; font:inherit; font-size:12px; cursor:pointer; }
.fehler { color:#ef5350; padding:10px; }
.schleier { position:fixed; inset:0; background:rgba(0,0,0,.5); z-index:1000; backdrop-filter: blur(2px); -webkit-backdrop-filter: blur(2px); }
.dlg { position:fixed; z-index:1001; font-family: var(--ha-font-family-body, Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif); left:50%; top:50%; transform:translate(-50%,-50%); width:min(600px, calc(100vw - 20px)); max-height:calc(100vh - 30px); overflow:auto; background: var(--ha-card-background, var(--card-background-color, #1c1f26)); color:var(--primary-text-color); border-radius:20px; padding:16px 18px 14px; box-shadow:0 20px 60px rgba(0,0,0,.45); box-sizing:border-box; }
.dkopf { display:flex; gap:8px; align-items:center; }
.dnr { color:var(--secondary-text-color); font-size:15px; }
.dtitel { flex:1; min-width:0; font:inherit; font-size:18px; font-weight:600; border:0; border-bottom:2px solid var(--tb-rand); background:none; color:inherit; padding:6px 2px; outline:none; }
.dtitel:focus { border-bottom-color: var(--primary-color); }
.zu { border:0; background:none; color:var(--secondary-text-color); cursor:pointer; padding:4px; }
.von { display:flex; align-items:center; gap:4px; font-size:12px; color:var(--secondary-text-color); margin:6px 2px 0; } .von ha-icon { --mdc-icon-size:14px; }
.dtext, .komm textarea { width:100%; box-sizing:border-box; margin-top:10px; font:inherit; font-size:14px; border:1px solid var(--tb-rand); border-radius:12px; background:var(--tb-glas); color:inherit; padding:9px 11px; resize:vertical; outline:none; }
.dlg label { display:block; font-size:11.5px; font-weight:600; letter-spacing:.4px; text-transform:uppercase; color:var(--secondary-text-color); margin:14px 2px 6px; }
.dlg label.haken, .dlg label.knopf { text-transform:none; letter-spacing:0; font-size:13.5px; font-weight:400; color:var(--primary-text-color); display:flex; align-items:center; gap:8px; margin:8px 2px; }
.dlg label.haken.aus { display:none; }
.seg { display:flex; flex-wrap:wrap; gap:6px; }
.seg button { display:inline-flex; align-items:center; gap:5px; border:1px solid var(--tb-rand); background:var(--tb-glas); color:var(--primary-text-color); border-radius:12px; padding:6px 10px; font:inherit; font-size:12.5px; cursor:pointer; }
.seg button[disabled] { cursor:default; opacity:.6; } .seg button.an[disabled] { opacity:1; }
.seg button ha-icon { --mdc-icon-size:16px; color: var(--sf, inherit); }
.seg button.an { border-color: var(--sf, var(--primary-color)); background: color-mix(in srgb, var(--sf, var(--primary-color)) 22%, transparent); font-weight:600; }
.hinweis { font-size:12.5px; color:var(--secondary-text-color); align-self:center; }
.zwei, .raster { display:grid; grid-template-columns: 1.4fr 1fr; gap:12px; }
.raster { grid-template-columns: 1fr 1fr; gap:0 12px; }
@media (max-width: 520px) { .zwei { grid-template-columns: 1fr; } }
.feld input, .feld select { width:100%; box-sizing:border-box; font:inherit; font-size:13.5px; border:1px solid var(--tb-rand); border-radius:10px; background:var(--tb-glas); color:inherit; padding:7px 9px; color-scheme: light dark; }
.datum { margin-top:6px; font:inherit; font-size:13px; border:1px solid var(--tb-rand); border-radius:10px; background:var(--tb-glas); color:inherit; padding:5px 8px; color-scheme: light dark; }
.mehrfelder { margin-top:14px; border:1px solid var(--tb-rand); border-radius:12px; padding:6px 10px 10px; }
.mehrfelder summary { cursor:pointer; font-size:12px; font-weight:600; letter-spacing:.4px; text-transform:uppercase; color:var(--secondary-text-color); padding:4px 0; }
.freigabe { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:10px; padding:8px 10px; border-radius:12px; font-size:13px; background: color-mix(in srgb, #81c784 15%, transparent); }
.freigabe.pending { background: color-mix(in srgb, #ffb74d 18%, transparent); } .freigabe.declined { background: color-mix(in srgb, #ef5350 15%, transparent); }
.freigabe span { flex:1; min-width:160px; } .freigabe ha-icon { --mdc-icon-size:18px; }
.fg { border:0; background:var(--primary-color); color:var(--text-primary-color,#fff); border-radius:10px; padding:6px 12px; font:inherit; font-size:13px; font-weight:600; cursor:pointer; }
.fg.nein { background:none; border:1px solid #ef5350; color:#ef5350; }
.herzen button { border:0; background:none; font-size:24px; color:var(--tb-rand); cursor:pointer; padding:0 2px; }
.herzen button.an { color:#f06292; }
.liste { display:flex; flex-direction:column; gap:2px; }
.punkt { display:flex; align-items:center; } .punkt .haken { flex:1; margin:4px 2px !important; }
.punkt input:checked + span { text-decoration: line-through; opacity:.6; }
.punktneu { display:flex; gap:6px; } .punktneu input { flex:1; font:inherit; font-size:13.5px; border:1px solid var(--tb-rand); border-radius:10px; background:var(--tb-glas); color:inherit; padding:7px 9px; }
.add, .x { border:0; background:none; color:var(--secondary-text-color); cursor:pointer; padding:4px; }
.x.sicher { color:#ef5350; }
.anhaenge { display:grid; grid-template-columns: repeat(auto-fill, minmax(84px, 1fr)); gap:8px; }
.anhaenge:empty { display:none; }
.anhang { position:relative; height:84px; border-radius:10px; overflow:hidden; border:1px solid var(--tb-rand); background:var(--tb-glas); display:flex; flex-direction:column; align-items:center; justify-content:center; gap:4px; font-size:11px; color:var(--secondary-text-color); cursor:pointer; text-align:center; padding:4px; box-sizing:border-box; }
.anhang img { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }
.anhang span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:100%; }
.anhang .x { position:absolute; top:2px; right:2px; background: rgba(0,0,0,.45); color:#fff; border-radius:50%; padding:2px; }
.anhang .x ha-icon { --mdc-icon-size:16px; }
.hoch { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:8px; }
.dlg label.knopf { margin:0; border:1px solid var(--tb-rand); background:var(--tb-glas); border-radius:12px; padding:6px 10px; cursor:pointer; font-size:12.5px; }
.knopf ha-icon { --mdc-icon-size:16px; }
.hoch .status, .vorabliste { font-size:12px; color:var(--secondary-text-color); }
.bildgross { position:fixed; inset:0; z-index:1002; background:rgba(0,0,0,.85); display:flex; align-items:center; justify-content:center; }
.bildgross[hidden] { display:none; }
.bildgross img { max-width:100%; max-height:100%; object-fit:contain; }
.bildgross .x { position:absolute; top: calc(10px + env(safe-area-inset-top, 0px)); right:10px; color:#fff; }
.verlauf { display:flex; flex-direction:column; gap:6px; max-height:220px; overflow:auto; }
.ve { font-size:12.5px; color:var(--secondary-text-color); } .ve span { font-size:11px; opacity:.8; }
.ve p { margin:3px 0 0; padding:7px 10px; border-radius:10px; background:var(--tb-glas); color:var(--primary-text-color); }
.ve i { display:block; margin-top:1px; }
.komm { display:flex; gap:6px; align-items:flex-end; } .komm textarea { margin-top:6px; }
.senden { border:0; background:var(--primary-color); color:var(--text-primary-color,#fff); border-radius:12px; padding:9px 11px; cursor:pointer; }
.fuss { display:flex; align-items:center; margin-top:16px; } .fuss span { flex:1; }
.ok { border:0; background:var(--primary-color); color:var(--text-primary-color,#fff); border-radius:14px; padding:9px 22px; font:inherit; font-weight:600; cursor:pointer; }
.ok[disabled] { opacity:.6; }
.weg { border:1px solid color-mix(in srgb, #ef5350 50%, transparent); background:none; color:#ef5350; border-radius:14px; padding:8px 14px; font:inherit; font-size:13px; cursor:pointer; }
.weg.sicher { background:#ef5350; border-color:#ef5350; color:#fff; font-weight:600; }
/* ---------- iPhone (p3, p4, p6) ---------- */
.tabs { display:none; }
button, .karte { -webkit-tap-highlight-color: transparent; }
@media (max-width: 600px) {
  ha-card { padding: 12px 10px 14px; }
  .board { display:block; overflow:visible; }
  .tabs { display:grid; grid-template-columns:repeat(6, minmax(0, 1fr)); gap:5px; margin-bottom:10px; }
  .tab { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:2px; min-height:54px; border:1px solid var(--tb-rand); background:var(--tb-glas); color:var(--secondary-text-color); border-radius:14px; font:inherit; font-size:13px; padding:6px 2px; cursor:pointer; }
  .tab ha-icon { --mdc-icon-size:22px; color:var(--sf); }
  .tab.an { border-color:var(--sf); background:color-mix(in srgb, var(--sf) 22%, transparent); color:var(--primary-text-color); font-weight:600; }
  .spalte { display:none; min-height:0; }
  .spalte.aktiv { display:block; }
  .karte { padding:12px 12px 11px 13px; }
  .cover { width: calc(100% + 25px); margin: -12px -12px 10px -13px; height:140px; }
  .kt { font-size:15px; }
  .zeile2 { font-size:12.5px; }
  .schleier { display:none; }
  .dlg { inset:0; left:0; top:0; transform:none; width:auto; max-height:none; border-radius:0; box-shadow:none;
         padding: calc(10px + env(safe-area-inset-top, 0px)) 16px 0; overscroll-behavior:contain; -webkit-overflow-scrolling:touch; }
  .fuss { position:sticky; bottom:0; z-index:1; margin:16px -16px 0; padding:10px 16px calc(10px + env(safe-area-inset-bottom, 0px));
          background: var(--ha-card-background, var(--card-background-color, #1c1f26)); border-top:1px solid var(--tb-rand); }
  .verlauf { max-height:none; }
}
@media (pointer: coarse) {
  .suche, .dtext, .komm textarea, .datum, .feld input, .feld select, .punktneu input { font-size:16px; }
  .chip { padding:8px 14px; font-size:14px; }
  .plus { padding:9px 16px 9px 12px; font-size:14px; }
  .seg button { min-height:40px; padding:8px 12px; font-size:14px; }
  .herzen button { font-size:30px; padding:2px 6px; }
  .zu { padding:10px; }
  .zu ha-icon { --mdc-icon-size:26px; }
  .senden { padding:11px 13px; }
  .ok { padding:12px 26px; font-size:15px; }
  .weg { padding:11px 16px; font-size:14px; }
  .mehr { min-height:40px; font-size:13px; }
  .dlg label.knopf { min-height:40px; font-size:14px; }
  .x { padding:8px; }
}
`;

customElements.get("haushalttickets-card") || customElements.define("haushalttickets-card", HaushaltticketsCard);
window.customCards = window.customCards || [];
if (!window.customCards.find((c) => c.type === "haushalttickets-card")) {
  window.customCards.push({ type: "haushalttickets-card", name: "Haushalttickets", preview: false,
    description: "Kanban-Board für Reparaturen, Anschaffungen und Arbeit an Home Assistant.",
    documentationURL: "https://github.com/sebastianrak/ha-haushalttickets/blob/main/docs/card.md" });
}
console.info("%c HAUSHALTTICKETS %c " + HT_VERSION + " ", "background:#1e88e5;color:#fff;border-radius:3px 0 0 3px;padding:1px 4px", "background:#424242;color:#fff;border-radius:0 3px 3px 0;padding:1px 4px");
