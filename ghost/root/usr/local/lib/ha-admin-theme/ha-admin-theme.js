/* Synchronizes the Ghost admin sign-in, setup and reset screens — and the
 * boot loading screen — with the active Home Assistant theme. The ingress
 * proxy injects this script into the admin document before </head>, with
 * the hashed dark loader video in data-dark-loader when the build has one.
 *
 * The script owns a private class (ha-auth-dark) and a critical stylesheet
 * scoped to it. It never touches Ghost's own `dark` class or Night shift
 * stylesheet, so the authenticated admin panel is completely unaffected:
 * the class is removed as soon as the app has booted outside an
 * unauthenticated route, and every rule is scoped to the auth flow or the
 * boot loader.
 */
(function () {
  "use strict";

  var STYLE_ID = "ha-admin-theme-style";
  var THEME_CLASS = "ha-auth-dark";
  var SCRIPT_MARKER = "script[data-ha-admin-theme]";
  var EXPLICIT_UNAUTH_ROUTE = /^\/(?:setup|signin|signup|reset)(?:[/?]|$)/;
  var LOADER_SELECTOR = ".ember-load-indicator";
  var LOADER_VIDEO_SELECTOR = ".ember-load-indicator video";

  var CRITICAL_CSS = [
    "html.ha-auth-dark .ember-load-indicator video { visibility: hidden; }",
    "html.ha-auth-dark .gh-loading-content { background-color: #15171a; }",
    "html.ha-auth-dark .gh-loading-spinner { border-color: rgba(255, 255, 255, 0.1); }",
    "html.ha-auth-dark .gh-loading-spinner:before { background-color: #ffffff; }",
    "html.ha-auth-dark .gh-flow { color-scheme: dark; background: #15171a; }",
    "html.ha-auth-dark .gh-flow:has(form:is(.gh-signin, .gh-signup)) { background: #15171a; }",
    "html.ha-auth-dark .gh-flow-content { color: #e3e6e8; }",
    "html.ha-auth-dark .gh-flow-content h1,",
    "html.ha-auth-dark .gh-signin h1 { color: #fafafb; }",
    "html.ha-auth-dark .gh-flow form label { color: #fafafb; }",
    "html.ha-auth-dark .gh-setup .gh-flow-content p,",
    "html.ha-auth-dark .gh-auth-email p { color: #7b8189; }",
    "html.ha-auth-dark .gh-flow-content .gh-flow-create {",
    "    background: #1f2328;",
    "    border-color: #2a3037;",
    "    box-shadow: 0 20px 45px -10px rgba(0, 0, 0, 0.5);",
    "}",
    "html.ha-auth-dark .gh-flow-content .gh-input,",
    "html.ha-auth-dark :is(.gh-signin, .gh-signup) .gh-input {",
    "    background: #15171a;",
    "    border-color: #131416;",
    "    color: #e3e6e8;",
    "}",
    "html.ha-auth-dark .gh-signin .gh-input:focus:not(.gh-signin .gh-input.reset-password:focus),",
    "html.ha-auth-dark .gh-signup .gh-input:focus,",
    "html.ha-auth-dark .gh-flow-content .gh-input:focus {",
    "    background-color: #15171a;",
    "    border-color: #30cf43;",
    "    box-shadow: 0 0 0 3px rgba(48, 207, 67, 0.25);",
    "}",
    "html.ha-auth-dark .gh-flow .gh-input::placeholder { color: #7c8b9a; }",
    "html.ha-auth-dark .gh-flow .gh-input-icon svg { fill: #7b8189; }",
    "html.ha-auth-dark .gh-flow .forgotten-wrap .forgotten-link { border-left-color: #2a3037; }",
    "html.ha-auth-dark .gh-flow .forgotten-link span { color: #7b8189; }",
    "html.ha-auth-dark .gh-flow .forgotten-link:hover span { color: #fafafb; }",
    "html.ha-auth-dark .gh-flow .gh-signin .gh-btn-reset { background: #fafafb !important; }",
    "html.ha-auth-dark .gh-flow .gh-signin .gh-btn-reset span { color: #15171a; }",
    "html.ha-auth-dark .gh-flow-content .main-error { color: #f50b23; }",
    "html.ha-auth-dark .gh-flow-content .main-notification { color: #fafafb; }",
    "html.ha-auth-dark .gh-flow-content .pw-strength { background: rgba(21, 23, 26, 0.9); }",
    "html.ha-auth-dark .gh-flow-content .pw-strength-dot { background-color: #394047; }",
    "html.ha-auth-dark .gh-setup .gh-flow-content header svg { filter: invert(1); }"
  ].join("\n");

  function parseColor(value) {
    if (typeof value !== "string") return null;
    var text = value.trim().toLowerCase();

    if (text.charAt(0) === "#") {
      var digits = text.slice(1);
      if (digits.length === 3) {
        digits = digits
          .split("")
          .map(function (ch) {
            return ch + ch;
          })
          .join("");
      }
      if (digits.length !== 6 && digits.length !== 8) return null;
      var channels = [0, 2, 4].map(function (index) {
        return parseInt(digits.slice(index, index + 2), 16);
      });
      if (channels.some(function (channel) {
        return Number.isNaN(channel);
      })) {
        return null;
      }
      return channels;
    }

    var match = text.match(
      /^rgba?\(\s*(\d+(?:\.\d+)?)\s*(?:,\s*|\s+)(\d+(?:\.\d+)?)\s*(?:,\s*|\s+)(\d+(?:\.\d+)?)\s*(?:[,/]\s*[\d.]+%?\s*)?\)/
    );
    if (!match) return null;
    var rgb = [Number(match[1]), Number(match[2]), Number(match[3])];
    if (rgb.some(function (channel) {
      return channel > 255;
    })) {
      return null;
    }
    return rgb;
  }

  function relativeLuminance(rgb) {
    var linear = rgb.map(function (channel) {
      var scaled = channel / 255;
      return scaled <= 0.03928 ? scaled / 12.92 : Math.pow((scaled + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
  }

  function parentIsDark() {
    try {
      var parentWindow = window.parent;
      if (!parentWindow || parentWindow === window) return null;
      var styles = parentWindow.getComputedStyle(parentWindow.document.documentElement);
      var candidates = ["--clear-background-color", "--primary-background-color"];
      for (var index = 0; index < candidates.length; index += 1) {
        var rgb = parseColor(styles.getPropertyValue(candidates[index]));
        if (rgb) return relativeLuminance(rgb) < 0.5;
      }
      return null;
    } catch (error) {
      return null;
    }
  }

  function localIsDark() {
    try {
      return Boolean(
        window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      );
    } catch (error) {
      return false;
    }
  }

  function isDark() {
    var parent = parentIsDark();
    return parent === null ? localIsDark() : parent;
  }

  function hashRoute() {
    var hash = String(window.location.hash || "");
    return hash.charAt(0) === "#" ? hash.slice(1) : hash;
  }

  function isExplicitUnauthRoute(route) {
    return EXPLICIT_UNAUTH_ROUTE.test(route);
  }

  var scriptTag =
    window.document.currentScript ||
    window.document.querySelector(SCRIPT_MARKER);
  var darkLoaderUrl =
    scriptTag && scriptTag.dataset ? scriptTag.dataset.darkLoader || "" : "";

  var booted = false;
  var currentDark = false;
  var originalLoaderSource = null;
  var bootObserver = null;

  function ensureStyle() {
    if (window.document.getElementById(STYLE_ID)) return;
    var style = window.document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CRITICAL_CSS;
    var target = window.document.head || window.document.documentElement;
    target.appendChild(style);
  }

  function loaderVideo() {
    return window.document.querySelector(LOADER_VIDEO_SELECTOR);
  }

  function applyLoaderVideo(dark) {
    var video = loaderVideo();
    if (!video) return;
    var source =
      typeof video.querySelector === "function" ? video.querySelector("source") : null;
    if (!source || typeof source.getAttribute !== "function") return;
    if (originalLoaderSource === null) {
      originalLoaderSource = source.getAttribute("src");
    }
    if (dark && darkLoaderUrl) {
      if (source.getAttribute("src") !== darkLoaderUrl) {
        source.setAttribute("src", darkLoaderUrl);
        if (typeof video.load === "function") video.load();
      }
      if (video.style) video.style.visibility = "visible";
      return;
    }
    if (dark) {
      if (video.style) video.style.visibility = "hidden";
      return;
    }
    if (
      originalLoaderSource &&
      source.getAttribute("src") !== originalLoaderSource
    ) {
      source.setAttribute("src", originalLoaderSource);
      if (typeof video.load === "function") video.load();
    }
    if (video.style) video.style.visibility = "";
  }

  function apply(dark) {
    currentDark = Boolean(dark);
    window.document.documentElement.classList.toggle(THEME_CLASS, currentDark);
    applyLoaderVideo(currentDark);
  }

  function sync() {
    if (!booted) {
      ensureStyle();
      apply(isDark());
      return;
    }
    if (isExplicitUnauthRoute(hashRoute())) {
      ensureStyle();
      apply(isDark());
    } else {
      apply(false);
    }
  }

  function checkBoot() {
    if (booted) return;
    if (window.document.readyState === "loading") return;
    if (window.document.querySelector(LOADER_SELECTOR)) return;
    booted = true;
    if (bootObserver) {
      bootObserver.disconnect();
      bootObserver = null;
    }
    sync();
  }

  function watchBoot() {
    var Observer = window.MutationObserver;
    if (typeof Observer === "function") {
      bootObserver = new Observer(function () {
        applyLoaderVideo(currentDark);
        checkBoot();
      });
      bootObserver.observe(window.document.documentElement, {
        childList: true,
        subtree: true
      });
    }
    window.document.addEventListener("DOMContentLoaded", function () {
      applyLoaderVideo(currentDark);
      checkBoot();
    });
  }

  function watchColorScheme() {
    try {
      if (typeof window.matchMedia !== "function") return;
      var media = window.matchMedia("(prefers-color-scheme: dark)");
      if (!media) return;
      if (typeof media.addEventListener === "function") {
        media.addEventListener("change", sync);
      } else if (typeof media.addListener === "function") {
        media.addListener(sync);
      }
    } catch (error) {
      /* No media query support: the parent theme still works. */
    }
  }

  function watchParentTheme() {
    try {
      var parentWindow = window.parent;
      if (!parentWindow || parentWindow === window) return;
      var Observer = parentWindow.MutationObserver;
      if (typeof Observer !== "function") return;
      var root = parentWindow.document.documentElement;
      if (!root) return;
      var observer = new Observer(sync);
      observer.observe(root, {
        attributes: true,
        attributeFilter: ["theme", "class", "style", "data-theme"],
        childList: true,
        subtree: true
      });
    } catch (error) {
      /* Cross-origin or restricted parent: the scheme fallback remains. */
    }
  }

  ensureStyle();
  watchColorScheme();
  watchParentTheme();
  watchBoot();
  window.addEventListener("hashchange", sync);
  apply(isDark());
})();
