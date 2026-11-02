/* Zettelwirtschaft — Matomo-Consent-Banner (Muster von syncvault/auffi).
 *
 * Geladen von allen Seiten der statischen Website
 * (zettelwirtschaft.mr-development.de). Reichweitenmessung mit der
 * selbst gehosteten Matomo-Instanz, nur nach Einwilligung:
 *
 *   - Do-Not-Track gesetzt -> kein Banner, kein Matomo.
 *   - localStorage 'zettelwirtschaft_matomo_consent' === 'ok' -> Matomo laden.
 *   - === 'no' -> nichts tun.
 *   - unbekannt -> Banner mit "Statistik OK" / "Ablehnen".
 *
 * Matomo laeuft ohne Cookies (disableCookies), die IP-Adresse kuerzt der
 * Matomo-Server vor der Speicherung (PrivacyManager, 2 Bytes).
 * Externe Datei statt Inline-Script, damit die CSP ohne zusaetzlichen Hash auskommt.
 */
(function () {
  var STORAGE_KEY = "zettelwirtschaft_matomo_consent";
  var TRACKER_URL = "https://musikersuche.org/matomo/";
  var SITE_ID = "12";

  function readConsent() {
    try {
      var v = localStorage.getItem(STORAGE_KEY);
      return v === "ok" || v === "no" ? v : null;
    } catch (_e) {
      return null;
    }
  }

  function writeConsent(value) {
    try {
      localStorage.setItem(STORAGE_KEY, value);
    } catch (_e) {
      /* localStorage gesperrt: Banner erscheint beim naechsten Aufruf erneut */
    }
  }

  function dntActive() {
    var nav = navigator;
    return (
      nav.doNotTrack === "1" ||
      window.doNotTrack === "1" ||
      nav.msDoNotTrack === "1"
    );
  }

  function loadMatomo() {
    if (window.__matomoLoaded) return;
    window.__matomoLoaded = true;
    var _paq = (window._paq = window._paq || []);
    _paq.push(["disableCookies"]);
    _paq.push(["setDoNotTrack", true]);
    // Setup.exe und Update-/Image-Archive als Download zaehlen.
    _paq.push(["addDownloadExtensions", "exe|zip|gz"]);
    _paq.push(["trackPageView"]);
    _paq.push(["enableLinkTracking"]);
    _paq.push(["setTrackerUrl", TRACKER_URL + "matomo.php"]);
    _paq.push(["setSiteId", SITE_ID]);
    var script = document.createElement("script");
    script.async = true;
    script.src = TRACKER_URL + "matomo.js";
    document.head.appendChild(script);
  }

  function dismissBanner(banner) {
    banner.remove();
    document.body.classList.remove("matomo-consent-shown");
  }

  function buildBanner() {
    var banner = document.createElement("div");
    banner.id = "matomo-consent-banner";
    banner.className = "matomo-consent-banner";
    banner.setAttribute("role", "dialog");
    banner.setAttribute("aria-live", "polite");
    banner.setAttribute("aria-label", "Datenschutz-Hinweis zur Reichweitenmessung");

    var text = document.createElement("p");
    text.className = "matomo-consent-text";
    text.appendChild(document.createTextNode(
      "Darf ich anonym zählen, wie oft diese Website aufgerufen wird? Selbst betriebenes Matomo, ohne Cookies, IP-Adresse gekürzt. "
    ));
    var link = document.createElement("a");
    link.href = "datenschutz.html#matomo";
    link.className = "matomo-consent-link";
    link.textContent = "Mehr Info";
    text.appendChild(link);
    text.appendChild(document.createTextNode("."));
    banner.appendChild(text);

    var actions = document.createElement("div");
    actions.className = "matomo-consent-actions";

    var no = document.createElement("button");
    no.type = "button";
    no.className = "matomo-consent-btn matomo-consent-no";
    no.textContent = "Ablehnen";
    no.addEventListener("click", function () {
      writeConsent("no");
      dismissBanner(banner);
    });

    var ok = document.createElement("button");
    ok.type = "button";
    ok.className = "matomo-consent-btn matomo-consent-ok";
    ok.textContent = "Statistik OK";
    ok.addEventListener("click", function () {
      writeConsent("ok");
      dismissBanner(banner);
      loadMatomo();
    });

    actions.appendChild(no);
    actions.appendChild(ok);
    banner.appendChild(actions);
    return banner;
  }

  function showBanner() {
    if (document.getElementById("matomo-consent-banner")) return;
    document.body.appendChild(buildBanner());
    document.body.classList.add("matomo-consent-shown");
  }

  // Widerruf auf der Datenschutz-Seite: <button data-matomo-revoke>.
  function wireRevoke() {
    var buttons = document.querySelectorAll("[data-matomo-revoke]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function () {
        try {
          localStorage.removeItem(STORAGE_KEY);
        } catch (_e) {
          /* ignorieren */
        }
        window.location.reload();
      });
    }
  }

  function init() {
    wireRevoke();
    if (dntActive()) return;
    var consent = readConsent();
    if (consent === "ok") loadMatomo();
    else if (consent === null) showBanner();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
