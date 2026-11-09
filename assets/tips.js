// Bulles d'aide (layout.py::tip) : une seule ouverte à la fois, fermeture au
// clic en dehors ou avec Échap, ancrage à droite si la bulle sortirait de l'écran.
// Chargé automatiquement par Dash (dossier assets/).
(function () {
  function closeAll(except) {
    document.querySelectorAll("details.mk-tip[open]").forEach(function (d) {
      if (d !== except) d.removeAttribute("open");
    });
  }

  document.addEventListener("click", function (e) {
    var tip = e.target.closest ? e.target.closest("details.mk-tip") : null;
    closeAll(tip);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeAll(null);
  });

  // "toggle" ne remonte pas : capture sur le document.
  document.addEventListener("toggle", function (e) {
    var d = e.target;
    if (!d.classList || !d.classList.contains("mk-tip") || !d.open) return;
    d.classList.remove("mk-tip--right");
    var body = d.querySelector(".mk-tip-body");
    if (body && body.getBoundingClientRect().right > window.innerWidth - 8) {
      d.classList.add("mk-tip--right");
    }
  }, true);
})();
