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
