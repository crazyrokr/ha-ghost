/* Synchronizes the Ghost admin sign-in, setup and reset screens — and the
 * boot loading screen — with the active Home Assistant theme. The ingress
 * proxy injects this script into the admin document before </head>, with
 * the hashed dark loader video in data-dark-loader when the build has one.
 *
 * Ghost 6's admin is a React application that ships a complete dark theme
 * behind a `dark` class on <html> and owns that class once a signed-in
 * user's preference is loaded. The script applies the class on the
 * unauthenticated screens (where the app has no user preference to apply)
 * plus a private marker class used for the add-on logo and the boot splash.
 * If the application changes the class while an auth screen is showing —
 * it applies the user's preference as soon as the session loads — the
 * script treats that as the app taking over and stops forcing the theme, so
 * it never overrides the application's own state. On authenticated routes
 * it removes the marker and restores the loader, leaving Ghost's class as
 * the application set it.
 */
(function () {
  "use strict";

  var STYLE_ID = "ha-admin-theme-style";
  var THEME_CLASS = "ha-auth-dark";
  var GHOST_DARK_CLASS = "dark";
  var SCRIPT_MARKER = "script[data-ha-admin-theme]";
  var EXPLICIT_UNAUTH_ROUTE = /^\/(?:setup|signin|signup|reset)(?:[/?]|$)/;
  var BOOT_MARKER_CLASS = "react-admin-mounted";
  var LOADER_VIDEO_SELECTOR = "#boot-splash video";
  var SITE_ICON_SIZE = 60;

  var CRITICAL_CSS = [
    "html.ha-auth-dark { color-scheme: dark; }",
    "html.ha-auth-dark body { background-color: #15171a; }"
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
  var siteIconUrl =
    scriptTag && scriptTag.dataset ? scriptTag.dataset.siteIcon || "" : "";
  var THEME_CSS = siteIconUrl
    ? CRITICAL_CSS +
      "\n" +
      "html.ha-auth-dark header > img { display: none; }\n" +
      "html.ha-auth-dark header::before { content: \"\"; display: block; " +
      "width: " + SITE_ICON_SIZE + "px; height: " + SITE_ICON_SIZE + "px; " +
      "border-radius: 4px; " +
      'background-image: url("' + siteIconUrl + '"); ' +
      "background-size: contain; background-position: center; " +
      "background-repeat: no-repeat; }"
    : CRITICAL_CSS;

  var booted = false;
  var currentDark = false;
  var lastAppliedDark = null;
  var appTookOver = false;
  var lastRoute = null;
  var originalLoaderSource = null;
  var bootObserver = null;

  function ensureStyle() {
    if (window.document.getElementById(STYLE_ID)) return;
    var style = window.document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = THEME_CSS;
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
      if (video.style && video.style.visibility !== "visible") {
        video.style.visibility = "visible";
      }
      return;
    }
    if (dark) {
      if (video.style && video.style.visibility !== "hidden") {
        video.style.visibility = "hidden";
      }
      return;
    }
    if (
      originalLoaderSource &&
      source.getAttribute("src") !== originalLoaderSource
    ) {
      source.setAttribute("src", originalLoaderSource);
      if (typeof video.load === "function") video.load();
    }
    if (video.style && video.style.visibility !== "") {
      video.style.visibility = "";
    }
  }

  function hasHtmlClass(name) {
    var root = window.document.documentElement;
    return Boolean(root && root.classList && root.classList.contains(name));
  }

  function setHtmlClass(name, enabled) {
    var root = window.document.documentElement;
    if (root && root.classList) {
      root.classList.toggle(name, Boolean(enabled));
    }
  }

  function applyAuthTheme(dark) {
    currentDark = Boolean(dark);
    setHtmlClass(THEME_CLASS, currentDark);
    setHtmlClass(GHOST_DARK_CLASS, currentDark);
    lastAppliedDark = currentDark;
    applyLoaderVideo(currentDark);
  }

  // The application has applied its own preference on an auth screen: keep
  // its class (never override it), keep the add-on branding, and follow the
  // effective state for the boot loader.
  function followAppTheme() {
    currentDark = hasHtmlClass(GHOST_DARK_CLASS);
    setHtmlClass(THEME_CLASS, true);
    applyLoaderVideo(currentDark);
  }

  function releaseAuthTheme() {
    currentDark = false;
    lastAppliedDark = null;
    setHtmlClass(THEME_CLASS, false);
    applyLoaderVideo(false);
  }

  function sync() {
    var route = hashRoute();
    var routeChanged = route !== lastRoute;
    lastRoute = route;

    if (!booted) {
      ensureStyle();
      appTookOver = false;
      applyAuthTheme(isDark());
      return;
    }

    if (isExplicitUnauthRoute(route)) {
      ensureStyle();
      if (routeChanged) {
        // A fresh auth screen (initial load, or a logout): the add-on theme
        // applies again until the application expresses its own preference.
        appTookOver = false;
        applyAuthTheme(isDark());
        return;
      }
      if (!appTookOver && lastAppliedDark !== null) {
        if (hasHtmlClass(GHOST_DARK_CLASS) !== lastAppliedDark) {
          appTookOver = true;
        }
      }
      if (appTookOver) {
        followAppTheme();
      } else {
        applyAuthTheme(isDark());
      }
      return;
    }

    appTookOver = false;
    releaseAuthTheme();
  }

  function appHasBooted() {
    var body = window.document.body;
    return Boolean(body && body.classList && body.classList.contains(BOOT_MARKER_CLASS));
  }

  function checkBoot() {
    if (booted) return;
    if (!appHasBooted()) return;
    booted = true;
    sync();
  }

  function watchBoot() {
    var Observer = window.MutationObserver;
    if (typeof Observer === "function") {
      // The React root and the boot splash are parsed after this script
      // (it runs in <head>), so re-apply once they exist. The observer
      // stays attached after boot: React Router updates the hash through
      // history.replaceState (a redirect from the bare /ghost/ to /setup,
      // or a page load onto a deep link), which fires no hashchange event,
      // so the rendered DOM is the reliable signal that the route changed.
      bootObserver = new Observer(function () {
        sync();
        checkBoot();
      });
      bootObserver.observe(window.document.documentElement, {
        childList: true,
        subtree: true
      });
    }
    window.document.addEventListener("DOMContentLoaded", function () {
      if (booted) return;
      sync();
      checkBoot();
    });
  }

  // React Router updates the hash through history.replaceState (a redirect
  // from the bare /ghost/ to /setup, or a deep link), which fires no
  // hashchange event; the rendered DOM is the reliable route signal. This
  // observer also notices when the application applies its own theme class.
  function watchGhostTheme() {
    var Observer = window.MutationObserver;
    if (typeof Observer !== "function") return;
    var observer = new Observer(function () {
      sync();
    });
    observer.observe(window.document.documentElement, {
      attributes: true,
      attributeFilter: ["class"]
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
  watchGhostTheme();
  window.addEventListener("hashchange", sync);
  applyAuthTheme(isDark());
  checkBoot();
})();
