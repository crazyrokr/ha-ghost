/* Synchronizes Ghost's unauthenticated admin screens with the active Home
 * Assistant theme. The ingress proxy injects this script into the admin
 * document before </head>, tagged with data-session="absent" when the
 * request carried no admin session cookie and "present" when it did.
 *
 * It runs before the admin bundle: on an unauthenticated screen it applies
 * the HA theme (read from the same-origin ingress parent, falling back to
 * prefers-color-scheme) to the sign-in, setup and reset pages; on an
 * authenticated document it does nothing, so Ghost's own per-user Night
 * shift applies without a flash. The critical stylesheet below styles the
 * auth flow completely, so no first paint depends on the 588 KB
 * ghost-dark.css being loaded.
 */
(function () {
  "use strict";

  var STYLE_ID = "ha-admin-theme-style";
  var DARK_LINK_ID = "dark-styles";
  var SCRIPT_MARKER = "script[data-ha-admin-theme]";
  var EXPLICIT_UNAUTH_ROUTE = /^\/(?:setup|signin|signup|reset)(?:[/?]|$)/;

  var CRITICAL_CSS = [
    "html.dark { color-scheme: dark; }",
    "html.dark, html.dark body { background-color: #15171a; }",
    "html.dark body { color: #e3e6e8; border-top-color: #15171a; }",
    "html.dark .gh-loading-content { background-color: #15171a; }",
    "html.dark .gh-loading-spinner { border-color: rgba(255, 255, 255, 0.1); }",
    "html.dark .gh-loading-spinner:before { background-color: #ffffff; }",
    "html.dark .gh-flow { background: #15171a; }",
    "html.dark .gh-flow:has(form:is(.gh-signin, .gh-signup)) { background: #15171a; }",
    "html.dark .gh-flow-content { color: #e3e6e8; }",
    "html.dark .gh-flow-content h1,",
    "html.dark .gh-signin h1 { color: #fafafb; }",
    "html.dark form label { color: #fafafb; }",
    "html.dark .gh-setup .gh-flow-content p,",
    "html.dark .gh-auth-email p { color: #7b8189; }",
    "html.dark .gh-flow-content .gh-flow-create {",
    "    background: #1f2328;",
    "    border-color: #2a3037;",
    "    box-shadow: 0 20px 45px -10px rgba(0, 0, 0, 0.5);",
    "}",
    "html.dark .gh-flow-content .gh-input,",
    "html.dark :is(.gh-signin, .gh-signup) .gh-input {",
    "    background: #15171a;",
    "    border-color: #131416;",
    "    color: #e3e6e8;",
    "}",
    "html.dark .gh-signin .gh-input:focus:not(.gh-signin .gh-input.reset-password:focus),",
    "html.dark .gh-signup .gh-input:focus,",
    "html.dark .gh-flow-content .gh-input:focus {",
    "    background-color: #15171a;",
    "    border-color: #30cf43;",
    "    box-shadow: 0 0 0 3px rgba(48, 207, 67, 0.25);",
    "}",
    "html.dark .gh-input::placeholder { color: #7c8b9a; }",
    "html.dark .gh-input-icon svg { fill: #7b8189; }",
    "html.dark .forgotten-wrap .forgotten-link { border-left-color: #2a3037; }",
    "html.dark .forgotten-link span { color: #7b8189; }",
    "html.dark .forgotten-link:hover span { color: #fafafb; }",
    "html.dark .gh-signin .gh-btn-reset { background: #fafafb !important; }",
    "html.dark .gh-signin .gh-btn-reset span { color: #15171a; }",
    "html.dark .gh-flow-content .main-error { color: #f50b23; }",
    "html.dark .gh-flow-content .main-notification { color: #fafafb; }",
    "html.dark .gh-flow-content .pw-strength { background: rgba(21, 23, 26, 0.9); }",
    "html.dark .gh-flow-content .pw-strength-dot { background-color: #394047; }",
    "html.dark .gh-setup .gh-flow-content header svg { filter: invert(1); }"
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
      /^rgba?\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)/
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

  function isHomeRoute(route) {
    return route === "" || route === "/";
  }

  var scriptTag =
    window.document.currentScript ||
    window.document.querySelector(SCRIPT_MARKER);
  var anonymousAtLoad = Boolean(
    scriptTag && scriptTag.dataset && scriptTag.dataset.session === "absent"
  );

  var lastApplied = null;

  function shouldApply() {
    var route = hashRoute();
    if (isExplicitUnauthRoute(route)) return true;
    if (isHomeRoute(route)) return anonymousAtLoad;
    return false;
  }

  function ensureStyle() {
    if (window.document.getElementById(STYLE_ID)) return;
    var style = window.document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CRITICAL_CSS;
    var target = window.document.head || window.document.documentElement;
    target.appendChild(style);
  }

  function apply(dark) {
    window.document.documentElement.classList.toggle("dark", dark);
    var link = window.document.getElementById(DARK_LINK_ID);
    if (link) link.disabled = !dark;
    lastApplied = {
      dark: Boolean(dark),
      linkDisabled: link ? Boolean(link.disabled) : null
    };
  }

  function sync() {
    if (!shouldApply()) return;
    ensureStyle();
    apply(isDark());
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
  window.addEventListener("hashchange", sync);
  sync();
})();
