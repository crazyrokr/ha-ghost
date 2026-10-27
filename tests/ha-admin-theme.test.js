"use strict";

/* Node-based tests for ha-admin-theme.js.
 *
 * Loads the real browser script into a minimal fake window/document: a
 * parent HA window with readable CSS custom properties, a matchMedia with
 * a change listener, the admin boot loader (with its video source), and a
 * MutationObserver registry that the tests flush explicitly. Run with:
 *
 *   node --test tests/ha-admin-theme.test.js
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

function createBrowser(options = {}) {
  const observers = [];
  const windowListeners = {};
  const documentListeners = {};
  const styles = [];
  const classes = new Set();

  let hash = options.hash || "";
  let localDark = Boolean(options.localDark);
  let parentVars = {...(options.parentVars || {})};
  let mediaListener = null;
  let loaderPresent =
    options.loaderPresent === undefined ? true : Boolean(options.loaderPresent);
  let ghostLinkDisabled =
    options.ghostLinkDisabled === undefined ? true : Boolean(options.ghostLinkDisabled);
  let styleElement = null;
  let loaderLoads = 0;

  const originalLoaderSource = options.loaderSource || "assets/videos/logo-loader-light.mp4";
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
      observers.push(this);
    }

    observe() {
      this.active = true;
    }

    disconnect() {
      this.active = false;
    }
  }

  function flushObservers() {
    for (const observer of observers.slice()) {
      if (observer.active) {
        observer.callback([], observer);
      }
    }
  }

  function dispatch(type, listeners, event) {
    for (const listener of (listeners[type] || []).slice()) {
      listener(event);
    }
  }

  const documentElement = {
    classList: {
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
    }
  };

  if (options.ghostDark) {
    classes.add("dark");
  }

  const darkLink = {
    id: "dark-styles",
    get disabled() {
      return ghostLinkDisabled;
    },
    set disabled(value) {
      ghostLinkDisabled = Boolean(value);
    }
  };

  const scriptTag = {dataset: {}};
  if (options.darkLoader !== undefined) {
    scriptTag.dataset.darkLoader = options.darkLoader;
  }

  const fakeDocument = {
    documentElement,
    body: {},
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
      if (selector === ".ember-load-indicator") return loaderPresent ? {} : null;
      if (selector === ".ember-load-indicator video") return loaderPresent ? loaderVideo : null;
      return null;
    },
    getElementById(id) {
      if (id === "dark-styles") return darkLink;
      if (id === "ha-admin-theme-style") return styleElement;
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
        MutationObserver: FakeMutationObserver
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
        MutationObserver: FakeMutationObserver
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
    showLoader() {
      loaderPresent = true;
      flushObservers();
    },
    bootApp() {
      // The application inserted its view and removed the boot loader.
      loaderPresent = false;
      fakeDocument.readyState = "interactive";
      dispatch("DOMContentLoaded", documentListeners, {});
      flushObservers();
    },
    authDarkClass() {
      return classes.has("ha-auth-dark");
    },
    ghostDarkClass() {
      return classes.has("dark");
    },
    setGhostTheme(dark, disabled) {
      if (dark) {
        classes.add("dark");
      } else {
        classes.delete("dark");
      }
      ghostLinkDisabled = Boolean(disabled);
    },
    ghostLinkDisabled() {
      return ghostLinkDisabled;
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
const DARK_LOADER = "assets/videos/logo-loader-dark-test.mp4";

test("dark HA parent themes the sign-in screen", () => {
  // Given an ingress sign-in page whose parent HA theme is dark
  const browser = createBrowser({hash: "#/signin", parentVars: DARK_PARENT});

  // When the script loads and the app boots
  browser.load();
  browser.bootApp();

  // Then our theme class is on, and Ghost's own dark state is untouched
  assert.equal(browser.authDarkClass(), true);
  assert.equal(browser.ghostDarkClass(), false);
  assert.equal(browser.ghostLinkDisabled(), true);
});

test("light HA parent leaves the sign-in screen light", () => {
  // Given an ingress sign-in page whose parent HA theme is light
  const browser = createBrowser({hash: "#/signin", parentVars: LIGHT_PARENT});

  // When the script loads and the app boots
  browser.load();
  browser.bootApp();

  // Then our theme class is absent
  assert.equal(browser.authDarkClass(), false);
});

test("authenticated panel load themes only the loading screen", () => {
  // Given an authenticated side-panel load on a panel route, HA dark
  const browser = createBrowser({
    hash: "#/dashboard",
    parentVars: DARK_PARENT,
    ghostDark: false
  });

  // When the document starts loading
  browser.load();

  // Then the boot loader is themed before the app takes over
  assert.equal(browser.authDarkClass(), true);

  // And after the app boots the class is removed, leaving the panel alone
  browser.bootApp();
  assert.equal(browser.authDarkClass(), false);
  assert.equal(browser.ghostDarkClass(), false);
  assert.equal(browser.ghostLinkDisabled(), true);
});

test("empty-hash panel entry themes only the loading screen", () => {
  // Given the side-panel entry URL (bare /ghost/) with a session, HA dark
  const browser = createBrowser({hash: "", parentVars: DARK_PARENT});

  // When the document starts loading
  browser.load();

  // Then the boot loader is themed
  assert.equal(browser.authDarkClass(), true);

  // And the class is removed at boot, so it cannot leak into the panel
  browser.bootApp();
  assert.equal(browser.authDarkClass(), false);
});

test("the HA theme is read from the parent with an OS fallback", () => {
  // Given a parent exposing only the primary background color
  const primary = createBrowser({
    hash: "#/signin",
    parentVars: {"--primary-background-color": "#111318"}
  });
  primary.load();
  primary.bootApp();
  assert.equal(primary.authDarkClass(), true);

  // And a standalone tab on a dark OS scheme
  const standalone = createBrowser({
    hash: "#/signin",
    noParent: true,
    localDark: true
  });
  standalone.load();
  standalone.bootApp();
  assert.equal(standalone.authDarkClass(), true);

  // And a cross-origin parent on a light OS scheme
  const crossOrigin = createBrowser({
    hash: "#/signin",
    crossOriginParent: true,
    localDark: false
  });
  crossOrigin.load();
  crossOrigin.bootApp();
  assert.equal(crossOrigin.authDarkClass(), false);
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
    assert.equal(browser.authDarkClass(), expected, color);
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
  assert.equal(invalid.authDarkClass(), true);

  // And an out-of-range channel is rejected the same way
  const outOfRange = createBrowser({
    hash: "#/signin",
    parentVars: {"--clear-background-color": "rgb(300, 0, 0)"},
    localDark: false
  });
  outOfRange.load();
  outOfRange.bootApp();
  assert.equal(outOfRange.authDarkClass(), false);

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
  assert.equal(fallback.authDarkClass(), true);
});

test("luminance threshold classifies mid greys", () => {
  // Given the WCAG 0.5 threshold, a dark grey and a light grey
  const darkGrey = createBrowser({
    hash: "#/signin",
    parentVars: {"--clear-background-color": "#808080"}
  });
  darkGrey.load();
  darkGrey.bootApp();
  assert.equal(darkGrey.authDarkClass(), true);

  const lightGrey = createBrowser({
    hash: "#/signin",
    parentVars: {"--clear-background-color": "#bfbfbf"}
  });
  lightGrey.load();
  lightGrey.bootApp();
  assert.equal(lightGrey.authDarkClass(), false);
});

test("the post-login spinner keeps the HA theme", () => {
  // Given a dark HA sign-in page with the app booted
  const browser = createBrowser({hash: "#/signin", parentVars: DARK_PARENT});
  browser.load();
  browser.bootApp();
  assert.equal(browser.authDarkClass(), true);

  // When Ghost applies a light account Night shift during the wait
  browser.setGhostTheme(false, true);
  browser.flushObservers();

  // Then our class stays on, so the spinner screen keeps the HA theme
  assert.equal(browser.authDarkClass(), true);
  assert.equal(browser.ghostDarkClass(), false);
  assert.equal(browser.ghostLinkDisabled(), true);

  // And when Ghost applies a dark account Night shift it still keeps it
  browser.setGhostTheme(true, false);
  browser.flushObservers();
  assert.equal(browser.authDarkClass(), true);
  assert.equal(browser.ghostDarkClass(), true);

  // When the panel route commits our class is removed, leaving Ghost's state
  browser.setHash("#/dashboard");
  assert.equal(browser.authDarkClass(), false);
  assert.equal(browser.ghostDarkClass(), true);
  assert.equal(browser.ghostLinkDisabled(), false);

  // And logout re-applies the HA theme
  browser.setHash("#/signin");
  assert.equal(browser.authDarkClass(), true);
});

test("a light HA theme stays light while Ghost's panel turns dark", () => {
  // Given a light HA sign-in page with the app booted
  const browser = createBrowser({hash: "#/signin", parentVars: LIGHT_PARENT});
  browser.load();
  browser.bootApp();
  assert.equal(browser.authDarkClass(), false);

  // When Ghost applies a dark account Night shift during the wait
  browser.setGhostTheme(true, false);
  browser.flushObservers();

  // Then the sign-in/loading screen stays light (it follows HA), and Ghost's
  // own state is untouched for the panel
  assert.equal(browser.authDarkClass(), false);
  assert.equal(browser.ghostDarkClass(), true);
  assert.equal(browser.ghostLinkDisabled(), false);

  browser.setHash("#/dashboard");
  assert.equal(browser.authDarkClass(), false);
  assert.equal(browser.ghostDarkClass(), true);
});

test("parent theme mutation re-syncs the auth screen", () => {
  // Given a light sign-in page with the app booted
  const browser = createBrowser({hash: "#/signin", parentVars: LIGHT_PARENT});
  browser.load();
  browser.bootApp();
  assert.equal(browser.authDarkClass(), false);

  // When the parent HA theme turns dark
  browser.setParentVars(DARK_PARENT);
  browser.flushObservers();

  // Then the page follows it
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
  assert.equal(browser.authDarkClass(), false);

  // When the OS scheme flips to dark
  browser.setLocalDark(true);

  // Then the page follows it
  assert.equal(browser.authDarkClass(), true);
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
  assert.equal(browser.videoSrc(), "assets/videos/logo-loader-light.mp4");
  assert.equal(browser.videoLoads(), 0);
  assert.equal(browser.videoVisibility(), "");
});

test("a dark theme without a dark loader hides the white video", () => {
  // Given a dark HA theme but no dark loader asset
  const browser = createBrowser({hash: "#/signin", parentVars: DARK_PARENT});
  browser.load();

  // Then the light video is hidden rather than showing a white square
  assert.equal(browser.videoVisibility(), "hidden");
  assert.equal(browser.videoSrc(), "assets/videos/logo-loader-light.mp4");
});

test("critical CSS is scoped to our class and the auth/loading screens", () => {
  // Given the critical stylesheet inserted on an ingress admin page
  const browser = createBrowser({hash: "#/signin", parentVars: DARK_PARENT});
  browser.load();
  const css = browser.styleText();

  // Then every rule hangs off our own class, never Ghost's global state
  assert.match(css, /html\.ha-auth-dark \.gh-flow/);
  assert.match(css, /html\.ha-auth-dark \.ember-load-indicator video/);
  assert.doesNotMatch(css, /html\.dark/);
  assert.doesNotMatch(css, /html\.ha-auth-dark body/);
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
