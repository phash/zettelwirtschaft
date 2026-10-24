/* Zettelwirtschaft Website — minimal progressive enhancement */
(function () {
  "use strict";

  // Footer year
  document.querySelectorAll("[data-year]").forEach(function (el) {
    el.textContent = new Date().getFullYear();
  });

  // Accessible mobile nav toggle (button + aria-expanded)
  var toggle = document.getElementById("nav-toggle");
  var nav = document.getElementById("primary-nav");
  if (toggle && nav) {
    var setOpen = function (open) {
      nav.classList.toggle("open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    };
    toggle.addEventListener("click", function () {
      setOpen(toggle.getAttribute("aria-expanded") !== "true");
    });
    nav.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function () { setOpen(false); });
    });
    // Close on Escape for keyboard users
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && nav.classList.contains("open")) { setOpen(false); toggle.focus(); }
    });
  }

  // Scroll reveal
  var els = document.querySelectorAll(".reveal");
  if (!("IntersectionObserver" in window) || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    els.forEach(function (el) { el.classList.add("in"); });
    return;
  }
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    });
  }, { threshold: 0.12, rootMargin: "0px 0px -8% 0px" });
  els.forEach(function (el) { io.observe(el); });
})();
