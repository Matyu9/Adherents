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
    if (question && !window.confirm(question)) event.preventDefault();
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
