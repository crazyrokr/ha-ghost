"use strict";

/* Node-based tests for ha-admin-theme.js.
 *
 * Loads the real browser script into a minimal fake window/document: a
 * parent HA window with readable CSS custom properties, a matchMedia with
 * a change listener, the Ghost 6 admin boot splash (with its video source),
 * and a MutationObserver registry that the tests flush explicitly.
 *
 * Ghost 6's React admin owns a `dark` class on <html> and ships a complete
 * dark theme behind it. The script therefore applies that class (plus a
 * private marker used for the add-on logo) on the unauthenticated screens,
 * hands the theme back to the app on authenticated routes, and swaps the
 * boot splash video.
 *
 * Run with: node --test tests/ha-admin-theme.test.js
 */

const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const {test} = require("node:test");

const SCRIPT_PATH = path.join(
  __dirname,
  "..",
  "ghost",
  "root",
  "usr",
  "local",
  "lib",
  "ha-admin-theme",
  "ha-admin-theme.js"
);

function createClassList(classes) {
  return {
    add(name) {
      classes.add(name);
    },
    remove(name) {
      classes.delete(name);
    },
    toggle(name, force) {
      const enabled = force === undefined ? !classes.has(name) : Boolean(force);
      if (enabled) {
        classes.add(name);
      } else {
        classes.delete(name);
      }
    },
    contains(name) {
      return classes.has(name);
    }
  };
}

function createBrowser(options = {}) {
  const observers = [];
  const windowListeners = {};
  const documentListeners = {};
  const styles = [];
  const rootClasses = new Set();
  const bodyClasses = new Set();
  const reactClasses = new Set();

  let hash = options.hash || "";
  let localDark = Boolean(options.localDark);
  let parentVars = {...(options.parentVars || {})};
  let mediaListener = null;
  let loaderPresent =
    options.loaderPresent === undefined ? true : Boolean(options.loaderPresent);
  let styleElement = null;
  let loaderLoads = 0;

  const originalLoaderSource =
    options.loaderSource || "assets/logo-loader-light-test.mp4";
  const loaderSource = {
    src: originalLoaderSource,
    getAttribute(name) {
      return name === "src" ? this.src : null;
    },
    setAttribute(name, value) {
      if (name === "src") this.src = value;
    }
  };
  const loaderVideo = {
    tagName: "VIDEO",
    style: {},
    querySelector(selector) {
      return selector === "source" ? loaderSource : null;
    },
    load() {
      loaderLoads += 1;
    }
  };

  class FakeMutationObserver {
    constructor(callback) {
      this.callback = callback;
      this.active = false;
      this.kind = "window";
      observers.push(this);
    }

    observe() {
      this.active = true;
    }

    disconnect() {
      this.active = false;
    }
  }

  class FakeParentMutationObserver extends FakeMutationObserver {
    constructor(callback) {
      super(callback);
      this.kind = "parent";
    }
  }

  function flushObservers(kind) {
    for (const observer of observers.slice()) {
      if (observer.active && (kind === undefined || observer.kind === kind)) {
        observer.callback([], observer);
      }
    }
  }

  function dispatch(type, listeners, event) {
    for (const listener of (listeners[type] || []).slice()) {
      listener(event);
    }
  }

  if (options.ghostDark) {
    rootClasses.add("dark");
  }

  const scriptTag = {dataset: {}};
  if (options.darkLoader !== undefined) {
    scriptTag.dataset.darkLoader = options.darkLoader;
  }
  if (options.siteIcon !== undefined) {
    scriptTag.dataset.siteIcon = options.siteIcon;
  }

  const reactRoot = {classList: createClassList(reactClasses)};

  const fakeDocument = {
    documentElement: {classList: createClassList(rootClasses)},
    body: {classList: createClassList(bodyClasses)},
    head: {
      appendChild(element) {
        if (element.tagName === "style") {
          styleElement = element;
          styles.push(element);
        }
      }
    },
    readyState: "loading",
    currentScript: scriptTag,
    querySelector(selector) {
      if (selector === "script[data-ha-admin-theme]") return scriptTag;
      if (selector === "#boot-splash") return loaderPresent ? {} : null;
      if (selector === "#boot-splash video") return loaderPresent ? loaderVideo : null;
      return null;
    },
    getElementById(id) {
      if (id === "ha-admin-theme-style") return styleElement;
      if (id === "root") return reactRoot;
      return null;
    },
    createElement(tagName) {
      return {tagName, id: "", textContent: ""};
    },
    addEventListener(type, listener) {
      (documentListeners[type] = documentListeners[type] || []).push(listener);
    }
  };

  const parentElement = {};
  const parentDocument = {documentElement: parentElement};
  const parentWindow = options.crossOriginParent
    ? {
        get document() {
          throw new Error("SecurityError: cross-origin access denied");
        },
        getComputedStyle() {
          throw new Error("SecurityError: cross-origin access denied");
        },
        MutationObserver: FakeParentMutationObserver
      }
    : {
        document: parentDocument,
        getComputedStyle() {
          return {
            getPropertyValue(name) {
              return parentVars[name] || "";
            }
          };
        },
        MutationObserver: FakeParentMutationObserver
      };

  const media = {
    get matches() {
      return localDark;
    },
    addEventListener(type, listener) {
      if (type === "change") mediaListener = listener;
    },
    addListener(listener) {
      mediaListener = listener;
    }
  };

  const fakeWindow = {
    document: fakeDocument,
    location: {
      get hash() {
        return hash;
      },
      set hash(value) {
        hash = value;
      }
    },
    matchMedia(query) {
      assert.equal(query, "(prefers-color-scheme: dark)");
      return media;
    },
    addEventListener(type, listener) {
      (windowListeners[type] = windowListeners[type] || []).push(listener);
    },
    MutationObserver: FakeMutationObserver,
    parent: options.noParent ? null : parentWindow
  };
  if (options.noParent) {
    fakeWindow.parent = fakeWindow;
  }

  return {
    window: fakeWindow,
    load() {
      const source = fs.readFileSync(SCRIPT_PATH, "utf8");
      new Function("window", source)(fakeWindow);
    },
    setHash(nextHash) {
      hash = nextHash;
      dispatch("hashchange", windowListeners, {});
    },
    setHashSilently(nextHash) {
      // React Router updates the hash through history.replaceState, which
      // fires no hashchange event.
      hash = nextHash;
    },
    setParentVars(vars) {
      parentVars = {...vars};
    },
    setLocalDark(value) {
      localDark = Boolean(value);
      if (mediaListener) {
        mediaListener({matches: localDark});
      }
    },
    flushObservers,
    flushAppObservers() {
      flushObservers("window");
    },
    showLoader() {
      loaderPresent = true;
      flushObservers();
    },
    bootApp() {
      // Ghost's React shell adds this class once the app has mounted; the
      // boot splash element stays in the document (CSS hides it).
      bodyClasses.add("react-admin-mounted");
      fakeDocument.readyState = "interactive";
      dispatch("DOMContentLoaded", documentListeners, {});
      flushObservers();
    },
    setGhostTheme(dark) {
      if (dark) {
        rootClasses.add("dark");
      } else {
        rootClasses.delete("dark");
      }
    },
    authDarkClass() {
      return rootClasses.has("ha-auth-dark");
    },
    reactDarkClass() {
      return reactClasses.has("dark");
    },
    ghostDarkClass() {
      return rootClasses.has("dark");
    },
    videoSrc() {
      return loaderSource.getAttribute("src");
    },
    videoVisibility() {
      return loaderVideo.style.visibility || "";
    },
    videoLoads() {
      return loaderLoads;
    },
    styleText() {
      return styleElement ? styleElement.textContent : null;
    },
    styleCount() {
      return styles.length;
    }
  };
}

const DARK_PARENT = {"--clear-background-color": "#0b0c10"};
const LIGHT_PARENT = {"--clear-background-color": "#ffffff"};
const DARK_LOADER = "assets/logo-loader-dark-test.mp4";
const SITE_ICON = "assets/ha-admin-theme-logo.png";

test("dark HA parent themes the sign-in screen", () => {
  // Given an ingress sign-in page whose parent HA theme is dark
  const browser = createBrowser({hash: "#/signin", parentVars: DARK_PARENT});

  // When the script loads and the app boots
  browser.load();
  browser.bootApp();

  // Then Ghost's dark theme and our auth marker are applied
  assert.equal(browser.ghostDarkClass(), true);
  assert.equal(browser.authDarkClass(), true);
});

test("light HA parent leaves the sign-in screen light", () => {
  // Given an ingress sign-in page whose parent HA theme is light
  const browser = createBrowser({hash: "#/signin", parentVars: LIGHT_PARENT});

  // When the script loads and the app boots
  browser.load();
  browser.bootApp();

  // Then neither theme class is applied
  assert.equal(browser.ghostDarkClass(), false);
  assert.equal(browser.authDarkClass(), false);
});

test("a stale Ghost dark state is cleared on the sign-in screen", () => {
  // Given Ghost still carries its dark class from an earlier session while
  // the Home Assistant theme is light
  const browser = createBrowser({
    hash: "#/signin",
    parentVars: LIGHT_PARENT,
    ghostDark: true
  });

  // When the sign-in screen is themed
  browser.load();
  browser.bootApp();

  // Then the sign-in follows Home Assistant, not the stale Ghost state
  assert.equal(browser.ghostDarkClass(), false);
  assert.equal(browser.authDarkClass(), false);
});

test("an authenticated panel load hands the theme back to Ghost", () => {
  // Given a side-panel load on an authenticated route with HA dark
  const browser = createBrowser({
    hash: "#/dashboard",
    parentVars: DARK_PARENT,
    ghostDark: false
  });

  // When the document starts loading
  browser.load();

  // Then the boot loader is themed
  assert.equal(browser.ghostDarkClass(), true);
  assert.equal(browser.authDarkClass(), true);

  // And once the app has mounted, our marker is gone while Ghost's own
  // theme state is left for the app to reconcile
  browser.bootApp();
  assert.equal(browser.authDarkClass(), false);
  assert.equal(browser.ghostDarkClass(), true);

  // When Ghost applies a light account preference it removes the class
  browser.setGhostTheme(false);
  browser.flushAppObservers();
  assert.equal(browser.ghostDarkClass(), false);
  assert.equal(browser.authDarkClass(), false);
});

test("empty-hash panel entry hands the theme back at boot", () => {
  // Given the side-panel entry URL (bare /ghost/) with HA dark
  const browser = createBrowser({hash: "", parentVars: DARK_PARENT});

  // When the document starts loading
  browser.load();
  assert.equal(browser.ghostDarkClass(), true);

  // Then the marker is removed at boot and Ghost's state is left alone
  browser.bootApp();
  assert.equal(browser.authDarkClass(), false);
  assert.equal(browser.ghostDarkClass(), true);
});

test("a programmatic route change re-syncs without a hashchange event", () => {
  // Given a booted admin on the empty-hash entry that Ghost redirects to
  // the setup screen with history.replaceState (which fires no event)
  const browser = createBrowser({hash: "", parentVars: DARK_PARENT});
  browser.load();
  browser.bootApp();
  assert.equal(browser.authDarkClass(), false);

  // When the app lands on the setup route
  browser.setHashSilently("#/setup");
  browser.flushAppObservers();

  // Then the setup screen follows the HA theme
  assert.equal(browser.ghostDarkClass(), true);
  assert.equal(browser.authDarkClass(), true);
});

test("the HA theme is read from the parent with an OS fallback", () => {
  // Given a parent exposing only the primary background color
  const primary = createBrowser({
    hash: "#/signin",
    parentVars: {"--primary-background-color": "#111318"}
  });
  primary.load();
  primary.bootApp();
  assert.equal(primary.ghostDarkClass(), true);

  // And a standalone tab on a dark OS scheme
  const standalone = createBrowser({
    hash: "#/signin",
    noParent: true,
    localDark: true
  });
  standalone.load();
  standalone.bootApp();
  assert.equal(standalone.ghostDarkClass(), true);

  // And a cross-origin parent on a light OS scheme
  const crossOrigin = createBrowser({
    hash: "#/signin",
    crossOriginParent: true,
    localDark: false
  });
  crossOrigin.load();
  crossOrigin.bootApp();
  assert.equal(crossOrigin.ghostDarkClass(), false);
});

test("parent color formats are parsed", () => {
  // Given the HA parent's background color in each supported CSS form
  const cases = [
    ["#0b0", true],
    ["#fff", false],
    ["#11131880", true],
    ["rgb(0, 0, 0)", true],
    ["rgba(255, 255, 255, 0.5)", false],
    ["rgb(11 12 16)", true],
    ["rgb(255 255 255 / 0.5)", false]
  ];

  for (const [color, expected] of cases) {
    const browser = createBrowser({
      hash: "#/signin",
      parentVars: {"--clear-background-color": color}
    });

    // When the script loads and the app boots
    browser.load();
    browser.bootApp();

    // Then the color decides the theme
    assert.equal(browser.ghostDarkClass(), expected, color);
  }
});

test("invalid parent colors fall back without throwing", () => {
  // Given an unparseable color, the OS scheme decides
  const invalid = createBrowser({
    hash: "#/signin",
    parentVars: {"--clear-background-color": "not-a-color"},
    localDark: true
  });
  invalid.load();
  invalid.bootApp();
  assert.equal(invalid.ghostDarkClass(), true);

  // And an out-of-range channel is rejected the same way
  const outOfRange = createBrowser({
    hash: "#/signin",
    parentVars: {"--clear-background-color": "rgb(300, 0, 0)"},
    localDark: false
  });
  outOfRange.load();
  outOfRange.bootApp();
  assert.equal(outOfRange.ghostDarkClass(), false);

  // And the next candidate is used when the first is unparseable
  const fallback = createBrowser({
    hash: "#/signin",
    parentVars: {
      "--clear-background-color": "not-a-color",
      "--primary-background-color": "#111318"
    }
  });
  fallback.load();
  fallback.bootApp();
  assert.equal(fallback.ghostDarkClass(), true);
});

test("luminance threshold classifies mid greys", () => {
  // Given the WCAG 0.5 threshold, a dark grey and a light grey
  const darkGrey = createBrowser({
    hash: "#/signin",
    parentVars: {"--clear-background-color": "#808080"}
  });
  darkGrey.load();
  darkGrey.bootApp();
  assert.equal(darkGrey.ghostDarkClass(), true);

  const lightGrey = createBrowser({
    hash: "#/signin",
    parentVars: {"--clear-background-color": "#bfbfbf"}
  });
  lightGrey.load();
  lightGrey.bootApp();
  assert.equal(lightGrey.ghostDarkClass(), false);
});

test("logout re-applies the HA theme", () => {
  // Given an authenticated panel on a light Home Assistant theme
  const browser = createBrowser({
    hash: "#/dashboard",
    parentVars: LIGHT_PARENT,
    ghostDark: false
  });
  browser.load();
  browser.bootApp();
  assert.equal(browser.ghostDarkClass(), false);

  // When the parent theme turns dark and the user logs out
  browser.setParentVars(DARK_PARENT);
  browser.setHash("#/signin");

  // Then the sign-in screen follows the parent theme
  assert.equal(browser.ghostDarkClass(), true);
  assert.equal(browser.authDarkClass(), true);

  // And a light parent flips it back
  browser.setParentVars(LIGHT_PARENT);
  browser.flushObservers();
  assert.equal(browser.ghostDarkClass(), false);
  assert.equal(browser.authDarkClass(), false);
});

test("the app's own theme wins once it applies on the sign-in screen", () => {
  // Given a dark HA sign-in page with the app booted
  const browser = createBrowser({hash: "#/signin", parentVars: DARK_PARENT});
  browser.load();
  browser.bootApp();
  assert.equal(browser.ghostDarkClass(), true);

  // When Ghost applies a light account preference during the wait
  browser.setGhostTheme(false);
  browser.flushAppObservers();

  // Then the script detects that the application has an opinion and stops
  // forcing its own theme, so it cannot fight the app
  assert.equal(browser.ghostDarkClass(), false);
  assert.equal(browser.authDarkClass(), true);

  // And the authenticated route leaves Ghost's state in place
  browser.setHash("#/dashboard");
  assert.equal(browser.authDarkClass(), false);
  assert.equal(browser.ghostDarkClass(), false);
});

test("parent theme mutation re-syncs the auth screen", () => {
  // Given a light sign-in page with the app booted
  const browser = createBrowser({hash: "#/signin", parentVars: LIGHT_PARENT});
  browser.load();
  browser.bootApp();
  assert.equal(browser.ghostDarkClass(), false);

  // When the parent HA theme turns dark
  browser.setParentVars(DARK_PARENT);
  browser.flushObservers();

  // Then the page follows it
  assert.equal(browser.ghostDarkClass(), true);
  assert.equal(browser.authDarkClass(), true);
});

test("media change re-syncs the auth screen", () => {
  // Given a standalone tab on a light OS scheme
  const browser = createBrowser({
    hash: "#/signin",
    noParent: true,
    localDark: false
  });
  browser.load();
  browser.bootApp();
  assert.equal(browser.ghostDarkClass(), false);

  // When the OS scheme flips to dark
  browser.setLocalDark(true);

  // Then the page follows it
  assert.equal(browser.ghostDarkClass(), true);
});

test("the dark loader video replaces the light one", () => {
  // Given a dark HA theme and a dark loader asset, with the loader not yet
  // parsed (the script runs in <head>)
  const browser = createBrowser({
    hash: "#/signin",
    parentVars: DARK_PARENT,
    darkLoader: DARK_LOADER,
    loaderPresent: false
  });
  browser.load();

  // When the boot loader appears
  browser.showLoader();

  // Then the dark video source is loaded and shown
  assert.equal(browser.videoSrc(), DARK_LOADER);
  assert.equal(browser.videoLoads(), 1);
  assert.equal(browser.videoVisibility(), "visible");
});

test("a light theme keeps the original loader video", () => {
  // Given a light HA theme
  const browser = createBrowser({
    hash: "#/signin",
    parentVars: LIGHT_PARENT,
    darkLoader: DARK_LOADER
  });
  browser.load();

  // Then the original source is untouched and visible
  assert.equal(browser.videoSrc(), "assets/logo-loader-light-test.mp4");
  assert.equal(browser.videoLoads(), 0);
  assert.equal(browser.videoVisibility(), "");
});

test("a dark theme without a dark loader hides the white video", () => {
  // Given a dark HA theme but no dark loader asset
  const browser = createBrowser({hash: "#/signin", parentVars: DARK_PARENT});
  browser.load();

  // Then the light video is hidden rather than showing a white square
  assert.equal(browser.videoVisibility(), "hidden");
  assert.equal(browser.videoSrc(), "assets/logo-loader-light-test.mp4");
});

test("the loader video is restored on an authenticated route", () => {
  // Given a dark HA theme with a dark loader on the sign-in screen
  const browser = createBrowser({
    hash: "#/signin",
    parentVars: DARK_PARENT,
    darkLoader: DARK_LOADER
  });
  browser.load();
  browser.bootApp();
  assert.equal(browser.videoSrc(), DARK_LOADER);

  // When the app lands on an authenticated route
  browser.setHash("#/dashboard");

  // Then the light loader is restored and our marker removed
  assert.equal(browser.videoSrc(), "assets/logo-loader-light-test.mp4");
  assert.equal(browser.videoVisibility(), "");
  assert.equal(browser.authDarkClass(), false);
});

test("critical CSS is scoped to our class and adds no Ghost overrides", () => {
  // Given the critical stylesheet inserted on an ingress admin page
  const browser = createBrowser({
    hash: "#/signin",
    parentVars: DARK_PARENT,
    siteIcon: SITE_ICON
  });
  browser.load();
  const css = browser.styleText();

  // Then every rule hangs off our own marker class, never Ghost's
  assert.match(css, /html\.ha-auth-dark/);
  assert.match(css, /html\.ha-auth-dark body/);
  assert.doesNotMatch(css, /html\.dark/);
  assert.doesNotMatch(css, /gh-flow/);
  assert.doesNotMatch(css, /ember-load-indicator/);
});

test("the add-on logo replaces the site icon on the auth screens", () => {
  // Given the proxy supplied the add-on logo URL
  const light = createBrowser({
    hash: "#/signin",
    parentVars: LIGHT_PARENT,
    siteIcon: SITE_ICON
  });

  // When the script loads under either HA theme
  light.load();
  const dark = createBrowser({
    hash: "#/signin",
    parentVars: DARK_PARENT,
    siteIcon: SITE_ICON
  });
  dark.load();

  // Then Ghost's React auth header image is hidden and the add-on logo is
  // drawn in its place, in both modes
  const hideRule = "html.ha-auth-dark header > img { display: none; }";
  const logoRule = `background-image: url("${SITE_ICON}")`;
  for (const browser of [light, dark]) {
    assert.ok(browser.styleText().includes(hideRule), hideRule);
    assert.ok(browser.styleText().includes(logoRule), logoRule);
  }
});

test("the add-on logo replaces the admin sidebar site icon", () => {
  // Given the proxy supplied the add-on logo URL
  const light = createBrowser({
    hash: "#/dashboard",
    parentVars: LIGHT_PARENT,
    siteIcon: SITE_ICON
  });

  // When the script loads on a panel route under either HA theme
  light.load();
  const dark = createBrowser({
    hash: "#/dashboard",
    parentVars: DARK_PARENT,
    siteIcon: SITE_ICON
  });
  dark.load();

  // Then Ghost's sidebar site icon image (the orb) renders the add-on logo
  // in its place, on authenticated routes as well
  const rule = `img[alt="Site icon"] { content: url("${SITE_ICON}"); }`;
  for (const browser of [light, dark]) {
    assert.ok(browser.styleText().includes(rule), rule);
  }
});

test("the site icon rule is absent without the logo attribute", () => {
  // Given a build where the proxy found no add-on logo
  const browser = createBrowser({hash: "#/signin", parentVars: LIGHT_PARENT});

  // When the script loads
  browser.load();

  // Then Ghost's own site icon is left alone
  assert.doesNotMatch(browser.styleText(), /ha-admin-theme-logo/);
});

test("critical style is inserted once", () => {
  // Given an active page that re-syncs several times
  const browser = createBrowser({hash: "#/signin", parentVars: DARK_PARENT});
  browser.load();

  // When the parent theme changes back and forth
  browser.setParentVars(LIGHT_PARENT);
  browser.flushObservers();
  browser.setParentVars(DARK_PARENT);
  browser.flushObservers();

  // Then the critical stylesheet is not duplicated
  assert.equal(browser.styleCount(), 1);
});
