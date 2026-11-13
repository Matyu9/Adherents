// Tout le JavaScript de comportement est ici : la Content-Security-Policy interdit les attributs onclick, onsubmit…

// Menu latéral (tiroir sur mobile).
(function () {
  const body = document.body;
  function setNav(open) {
    body.classList.toggle("nav-open", open);
    const scrim = document.querySelector(".scrim");
    if (scrim) scrim.hidden = !open;
    document.querySelectorAll("[data-open-nav]").forEach((b) => b.setAttribute("aria-expanded", String(open)));
  }
  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-open-nav]")) setNav(!body.classList.contains("nav-open"));
    else if (event.target.closest("[data-close-nav]") || event.target.closest(".sidebar a")) setNav(false);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") setNav(false);
  });
})();

// <form data-confirm="Question ?"> : demande confirmation avant l'envoi.
// <select data-autosubmit> : envoie le formulaire dès qu'on change de valeur.
(function () {
  document.addEventListener("submit", function (event) {
    const question = event.target.dataset ? event.target.dataset.confirm : null;
    if (question && !window.confirm(question)) {
      event.preventDefault();
      // <form data-reset-on-cancel> : une case cochée par erreur revient à son état si on refuse la confirmation.
      if ("resetOnCancel" in event.target.dataset) event.target.reset();
    }
  });
  document.addEventListener("change", function (event) {
    if (event.target.matches("[data-autosubmit]") && event.target.form) event.target.form.requestSubmit();
  });
})();

// Erreurs des requêtes HTMX : sans ce bandeau, l'action échoue sans rien afficher.
(function () {
  const MESSAGES = {
    400: "La page a expiré : rechargez-la puis recommencez.",
    403: "Vous n'avez pas les droits nécessaires pour cette action.",
    404: "Élément introuvable : il a peut-être été supprimé entre-temps.",
    413: "Fichier trop volumineux.",
    429: "Trop de tentatives : patientez quelques minutes.",
  };
  function show(text) {
    const main = document.querySelector("main");
    if (!main) return;
    let banner = main.querySelector(".htmx-error");
    if (!banner) {
      banner = document.createElement("div");
      banner.className = "flash flash-error htmx-error";
      banner.setAttribute("role", "alert");
      main.prepend(banner);
    }
    banner.textContent = text;
  }
  document.addEventListener("htmx:responseError", function (event) {
    const status = event.detail.xhr.status;
    show(MESSAGES[status] || "Une erreur est survenue (" + status + "). Rechargez la page puis réessayez.");
  });
  document.addEventListener("htmx:sendError", function () {
    show("Connexion au serveur impossible : vérifiez votre réseau puis réessayez.");
  });
})();

// <button data-dismiss> : referme le message qui le contient.
// <div data-show-when="champ" data-show-value="valeur autre"> : visible seulement quand ce champ du même formulaire vaut
// l'une de ces valeurs, séparées par des espaces (date d'abandon, effet d'une règle…). Sans JavaScript, tout reste affiché.
(function () {
  document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-dismiss]");
    if (!button) return;
    const box = button.closest(".flash");
    const list = box && box.parentElement;
    if (box) box.remove();
    if (list && list.classList.contains("flashes") && !list.children.length) list.remove();
  });
  function refresh(form) {
    form.querySelectorAll("[data-show-when]").forEach(function (block) {
      const field = form.elements[block.dataset.showWhen];
      block.hidden = !!field && !block.dataset.showValue.split(" ").includes(field.value);
    });
  }
  document.addEventListener("change", function (event) {
    if (event.target.form) refresh(event.target.form);
  });
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form").forEach(refresh);
  });
})();

// <button data-enabled-by="champ"> : désactivé tant que la case « champ » du même formulaire n'est pas cochée (attestation
// avant un envoi…). Sans JavaScript, le bouton reste actif et le serveur refuse l'envoi.
(function () {
  function refresh(form) {
    form.querySelectorAll("[data-enabled-by]").forEach(function (button) {
      const box = form.elements[button.dataset.enabledBy];
      button.disabled = !!box && !box.checked;
    });
  }
  document.addEventListener("change", function (event) {
    if (event.target.form) refresh(event.target.form);
  });
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form").forEach(refresh);
  });
})();

// <button data-add-row hidden> : ajoute une ligne au <tbody data-rows> du même bloc, copiée du <template data-row-template>
// (« __n__ » devient le numéro de ligne, « __label__ » son rang). Sans JavaScript, le bouton reste caché et le
// formulaire garde ses lignes vides.
(function () {
  document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-add-row]");
    if (!button) return;
    const block = button.parentElement;
    const rows = block.querySelector("[data-rows]");
    const template = block.querySelector("template[data-row-template]");
    if (!rows || !template) return;
    const n = rows.children.length;
    const html = template.innerHTML.replaceAll("__n__", String(n)).replaceAll("__label__", String(n + 1));
    rows.insertAdjacentHTML("beforeend", html);
    const first = rows.lastElementChild && rows.lastElementChild.querySelector("input");
    if (first) first.focus();
  });
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-add-row]").forEach(function (button) { button.hidden = false; });
  });
})();

// <form data-roll> : fiche d'appel. Compteur par statut, et « Tous présents » (sauf les absences prévenues).
(function () {
  const LABELS = { present: "présent", late: "en retard", absent: "absent", excused: "excusé" };
  function refresh(form) {
    const counts = {};
    form.querySelectorAll("[data-roll-row] input:checked").forEach(function (input) {
      counts[input.value] = (counts[input.value] || 0) + 1;
    });
    const parts = Object.keys(LABELS).filter(function (k) { return counts[k]; }).map(function (k) {
      return counts[k] + " " + LABELS[k] + (counts[k] > 1 && k !== "late" ? "s" : "");
    });
    const output = form.querySelector("[data-roll-count]");
    if (output) output.textContent = parts.join(" · ");
  }
  document.addEventListener("change", function (event) {
    const form = event.target.closest("form[data-roll]");
    if (form) refresh(form);
  });
  document.addEventListener("click", function (event) {
    const button = event.target.closest("[data-roll-all]");
    if (!button) return;
    const form = button.closest("form[data-roll]");
    form.querySelectorAll("[data-roll-row]:not([data-notice]) input[value=present]").forEach(function (input) {
      input.checked = true;
    });
    refresh(form);
  });
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form[data-roll]").forEach(refresh);
  });
})();

// Règles de tarifs : <div data-target-picker> remplace les quatre listes « éléments / catégories visés, sauf… » par
// une interface au choix, retenue dans ce navigateur : des étiquettes avec recherche, ou un glisser-déposer en trois
// colonnes. Les listes restent la source de vérité (et l'interface sans JavaScript).
(function () {
  const KEY = "adherents.targetPicker";
  function remember(mode) { try { localStorage.setItem(KEY, mode); } catch (e) { /* stockage indisponible */ } }
  function recall() { try { return localStorage.getItem(KEY) || "chips"; } catch (e) { return "chips"; } }

  function setup(box) {
    const form = box.closest("form");
    const selects = {
      in: { item: form.elements.target_items, cat: form.elements.target_categories },
      out: { item: form.elements.target_exclude_items, cat: form.elements.target_exclude_categories },
    };
    if (!selects.in.item || !selects.in.cat) return;
    const entries = [];
    Array.from(selects.in.cat.options).forEach(function (o) { entries.push({ kind: "cat", value: o.value, label: "Catégorie " + o.textContent }); });
    Array.from(selects.in.item.options).forEach(function (o) { entries.push({ kind: "item", value: o.value, label: o.textContent }); });
    function stateOf(entry) {
      if (option(selects.in[entry.kind], entry.value).selected) return "in";
      if (selects.out[entry.kind] && option(selects.out[entry.kind], entry.value).selected) return "out";
      return "none";
    }
    function option(select, value) { return Array.from(select.options).find(function (o) { return o.value === value; }); }
    function set(entry, state) {
      option(selects.in[entry.kind], entry.value).selected = state === "in";
      if (selects.out[entry.kind]) option(selects.out[entry.kind], entry.value).selected = state === "out";
      render();
    }
    // Le bloc s'affiche selon l'effet choisi (data-show-when, déjà appliqué) ; les listes d'origine se cachent.
    form.querySelector(".target-selects").hidden = true;
    form.querySelector(".target-selects").removeAttribute("data-show-when");
    let mode = recall();

    function el(tag, attrs, text) {
      const node = document.createElement(tag);
      Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
      if (text) node.textContent = text;
      return node;
    }

    function chip(entry, state) {
      const node = el("span", { class: "chip chip-" + state });
      node.appendChild(el("span", {}, (state === "out" ? "sauf : " : "") + entry.label));
      const flip = el("button", { type: "button", class: "chip-btn", "aria-label": state === "out" ? "Viser " + entry.label : "Exclure " + entry.label, title: state === "out" ? "Viser" : "Exclure" }, state === "out" ? "+" : "−");
      flip.addEventListener("click", function () { set(entry, state === "out" ? "in" : "out"); });
      const remove = el("button", { type: "button", class: "chip-btn", "aria-label": "Retirer " + entry.label }, "×");
      remove.addEventListener("click", function () { set(entry, "none"); });
      node.appendChild(flip);
      node.appendChild(remove);
      return node;
    }

    function renderChips(body) {
      const list = el("div", { class: "chips" });
      const chosen = entries.filter(function (e) { return stateOf(e) !== "none"; });
      if (!chosen.length) list.appendChild(el("span", { class: "muted small" }, "Rien de choisi : tous les éléments."));
      chosen.forEach(function (e) { list.appendChild(chip(e, stateOf(e))); });
      body.appendChild(list);
      const search = el("input", { type: "search", placeholder: "Rechercher un élément ou une catégorie…", "aria-label": "Ajouter un élément ou une catégorie visés" });
      const results = el("ul", { class: "chip-results", role: "listbox" });
      function show() {
        results.innerHTML = "";
        const q = search.value.trim().toLowerCase();
        if (!q) return;
        entries.filter(function (e) { return stateOf(e) === "none" && e.label.toLowerCase().indexOf(q) !== -1; }).slice(0, 8).forEach(function (e) {
          const li = el("li", { role: "option", tabindex: "0" }, e.label);
          function pick() { set(e, "in"); const again = box.querySelector("input[type=search]"); if (again) again.focus(); }
          li.addEventListener("click", pick);
          li.addEventListener("keydown", function (ev) { if (ev.key === "Enter") { ev.preventDefault(); pick(); } });
          results.appendChild(li);
        });
      }
      search.addEventListener("input", show);
      search.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") { ev.preventDefault(); const first = results.querySelector("li"); if (first) first.click(); }
      });
      body.appendChild(search);
      body.appendChild(results);
    }

    function renderColumns(body) {
      const grid = el("div", { class: "dnd-grid" });
      [["none", "Catalogue"], ["in", "Visés"], ["out", "Exclus"]].forEach(function (col) {
        const zone = el("div", { class: "dnd-col", "data-state": col[0] });
        zone.appendChild(el("strong", {}, col[1]));
        const list = el("ul", { class: "dnd-list" });
        entries.filter(function (e) { return stateOf(e) === col[0]; }).forEach(function (e) {
          const li = el("li", { draggable: "true", tabindex: "0", title: "Glissez, ou Entrée pour passer à la colonne suivante" }, e.label);
          li.addEventListener("dragstart", function (ev) { ev.dataTransfer.setData("text/plain", e.kind + ":" + e.value); });
          li.addEventListener("keydown", function (ev) {
            if (ev.key === "Enter") { ev.preventDefault(); set(e, { none: "in", in: "out", out: "none" }[col[0]]); }
          });
          list.appendChild(li);
        });
        if (col[0] === "in" && !list.children.length) list.appendChild(el("li", { class: "muted small dnd-empty" }, "Vide : tous les éléments"));
        zone.appendChild(list);
        zone.addEventListener("dragover", function (ev) { ev.preventDefault(); zone.classList.add("is-over"); });
        zone.addEventListener("dragleave", function () { zone.classList.remove("is-over"); });
        zone.addEventListener("drop", function (ev) {
          ev.preventDefault();
          const raw = ev.dataTransfer.getData("text/plain");
          const kind = raw.slice(0, raw.indexOf(":")), value = raw.slice(raw.indexOf(":") + 1);
          const entry = entries.find(function (x) { return x.kind === kind && x.value === value; });
          if (entry) set(entry, col[0]);
        });
        grid.appendChild(zone);
      });
      body.appendChild(grid);
    }

    function render() {
      box.innerHTML = "";
      const head = el("div", { class: "picker-head" });
      head.appendChild(el("label", {}, "Éléments visés"));
      const toggle = el("div", { class: "segmented small", role: "group", "aria-label": "Affichage" });
      [["chips", "Étiquettes"], ["dnd", "Glisser-déposer"]].forEach(function (m) {
        const b = el("button", { type: "button", class: "seg-btn" + (mode === m[0] ? " is-on" : ""), "aria-pressed": String(mode === m[0]) }, m[1]);
        b.addEventListener("click", function () { mode = m[0]; remember(mode); render(); });
        toggle.appendChild(b);
      });
      head.appendChild(toggle);
      box.appendChild(head);
      const body = el("div", { class: "picker-body" });
      (mode === "dnd" ? renderColumns : renderChips)(body);
      box.appendChild(body);
    }
    render();
  }
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-target-picker]").forEach(setup);
  });
})();
