"use strict";

/* Node-based tests for ha-admin-theme.js.
 *
 * Loads the real browser script into a minimal fake window/document: a
 * parent HA window with readable CSS custom properties, a matchMedia with
 * a change listener, a style/link DOM, and a MutationObserver registry that
 * the tests flush explicitly. Run with:
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
  let linkDisabled = options.linkDisabled === undefined ? true : options.linkDisabled;
  let parentVars = {...(options.parentVars || {})};
  let mediaListener = null;
  let running = false;
  let styleElement = null;

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

  const darkLink = {
    id: "dark-styles",
    get disabled() {
      return linkDisabled;
    },
    set disabled(value) {
      linkDisabled = Boolean(value);
    }
  };

  const scriptTag = {dataset: {}};
  if (options.session !== undefined) {
    scriptTag.dataset.session = options.session;
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
    currentScript: options.session === undefined ? null : scriptTag,
    querySelector(selector) {
      if (selector === "script[data-ha-admin-theme]") {
        return options.session === undefined ? null : scriptTag;
      }
      if (selector === '[data-test-task-button-state="running"]') {
        return running ? {} : null;
      }
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
    darkClass() {
      return classes.has("dark");
    },
    linkDisabled() {
      return linkDisabled;
    },
    styleText() {
      return styleElement ? styleElement.textContent : null;
    },
    styleCount() {
      return styles.length;
    },
    dispatchSubmit(submitOptions = {}) {
      running =
        submitOptions.running === undefined ? true : Boolean(submitOptions.running);
      dispatch("submit", documentListeners, {
        target: {
          closest(selector) {
            return selector === ".gh-flow" ? {} : null;
          }
        }
      });
    },
    setRunning(value) {
      running = Boolean(value);
    },
    setGhostTheme(dark, disabled) {
      if (dark) {
        classes.add("dark");
      } else {
        classes.delete("dark");
      }
      linkDisabled = Boolean(disabled);
    }
  };
}

const DARK_PARENT = {"--clear-background-color": "#0b0c10"};
const LIGHT_PARENT = {"--clear-background-color": "#ffffff"};

test("anonymous dark HA parent applies the theme on load", () => {
  // Given an anonymous ingress page whose parent HA theme is dark
  const browser = createBrowser({
    session: "absent",
    hash: "",
    parentVars: DARK_PARENT
  });

  // When the script loads
  browser.load();

  // Then the dark class is on, Ghost's dark stylesheet is enabled and the
  // critical styles are present exactly once
  assert.equal(browser.darkClass(), true);
  assert.equal(browser.linkDisabled(), false);
  assert.equal(browser.styleCount(), 1);
  assert.match(browser.styleText(), /html\.dark \.gh-flow/);
});

test("light HA parent leaves the light theme", () => {
  // Given an anonymous ingress page whose parent HA theme is light
  const browser = createBrowser({
    session: "absent",
    hash: "",
    parentVars: LIGHT_PARENT
  });

  // When the script loads
  browser.load();

  // Then the dark class is absent, the dark stylesheet stays disabled and
  // the critical styles are still available for a later switch
  assert.equal(browser.darkClass(), false);
  assert.equal(browser.linkDisabled(), true);
  assert.equal(browser.styleCount(), 1);
});

test("--primary-background-color is used when the clear color is missing", () => {
  // Given a parent that exposes only the primary background color
  const browser = createBrowser({
    session: "absent",
    hash: "",
    parentVars: {"--primary-background-color": "#111318"}
  });

  // When the script loads
  browser.load();

  // Then the dark fallback candidate decides the theme
  assert.equal(browser.darkClass(), true);
});

test("standalone tab falls back to prefers-color-scheme", () => {
  // Given a top-level tab (window.parent === window) on a dark OS scheme
  const browser = createBrowser({
    session: "absent",
    hash: "",
    noParent: true,
    localDark: true
  });

  // When the script loads
  browser.load();

  // Then the OS scheme is used
  assert.equal(browser.darkClass(), true);
});

test("cross-origin parent falls back to prefers-color-scheme", () => {
  // Given an inaccessible parent frame and a light OS scheme
  const browser = createBrowser({
    session: "absent",
    hash: "",
    crossOriginParent: true,
    localDark: false
  });

  // When the script loads
  browser.load();

  // Then the script does not throw and the OS scheme decides
  assert.equal(browser.darkClass(), false);
});

test("activation matrix", () => {
  // Given every combination of load-time session state and route hash
  const cases = [
    ["absent", "", true],
    ["present", "", false],
    ["present", "#/signin", true],
    ["absent", "#/dashboard", false],
    ["absent", "#/signin", true],
    ["present", "#/dashboard", false]
  ];

  for (const [session, hash, expected] of cases) {
    const browser = createBrowser({
      session,
      hash,
      parentVars: DARK_PARENT
    });

    // When the script loads
    browser.load();

    // Then only explicit unauthenticated routes, plus the empty hash for an
    // anonymous document, apply the HA theme
    assert.equal(browser.darkClass(), expected, `session=${session} hash=${hash}`);
  }
});

test("hash change to an authenticated route stops applying", () => {
  // Given an active dark HA theme on the sign-in route
  const browser = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: DARK_PARENT
  });
  browser.load();
  assert.equal(browser.darkClass(), true);

  // When the SPA moves to an authenticated route and the HA theme turns light
  browser.setHash("#/dashboard");
  browser.setParentVars(LIGHT_PARENT);
  browser.flushObservers();

  // Then the script leaves the state alone (Ghost owns the authenticated admin)
  assert.equal(browser.darkClass(), true);
});

test("logout returns to the HA theme", () => {
  // Given an active dark HA theme, then an authenticated route
  const browser = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: DARK_PARENT
  });
  browser.load();
  browser.setHash("#/dashboard");

  // When the user signs out and the HA theme is light
  browser.setParentVars(LIGHT_PARENT);
  browser.setHash("#/signin");

  // Then the sign-in screen follows the HA theme again
  assert.equal(browser.darkClass(), false);
  assert.equal(browser.linkDisabled(), true);
});

test("parent theme mutation re-syncs", () => {
  // Given a light page whose parent HA theme later turns dark
  const browser = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: LIGHT_PARENT
  });
  browser.load();
  assert.equal(browser.darkClass(), false);

  // When the parent theme variables change
  browser.setParentVars(DARK_PARENT);
  browser.flushObservers();

  // Then the page follows the new theme
  assert.equal(browser.darkClass(), true);
});

test("media change re-syncs", () => {
  // Given a standalone tab on a light OS scheme
  const browser = createBrowser({
    session: "absent",
    hash: "#/signin",
    noParent: true,
    localDark: false
  });
  browser.load();
  assert.equal(browser.darkClass(), false);

  // When the OS scheme flips to dark
  browser.setLocalDark(true);

  // Then the page follows it
  assert.equal(browser.darkClass(), true);
});

test("critical style is inserted once", () => {
  // Given an active page that re-syncs several times
  const browser = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: DARK_PARENT
  });
  browser.load();

  // When the parent theme changes back and forth
  browser.setParentVars(LIGHT_PARENT);
  browser.flushObservers();
  browser.setParentVars(DARK_PARENT);
  browser.flushObservers();

  // Then the critical stylesheet is not duplicated
  assert.equal(browser.styleCount(), 1);
});

test("submit freezes HA theme changes", () => {
  // Given an active dark sign-in page
  const browser = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: DARK_PARENT
  });
  browser.load();
  assert.equal(browser.darkClass(), true);

  // When the user submits the form and the HA theme turns light while the
  // account is being prepared
  browser.dispatchSubmit();
  browser.setParentVars(LIGHT_PARENT);
  browser.flushObservers();

  // Then the visible spinner screen keeps its theme (Ghost decides it)
  assert.equal(browser.darkClass(), true);
});

test("failed login unfreezes and re-syncs", () => {
  // Given an active dark sign-in page whose submit is in flight
  const browser = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: DARK_PARENT
  });
  browser.load();
  browser.dispatchSubmit();
  browser.setParentVars(LIGHT_PARENT);
  browser.flushObservers();
  assert.equal(browser.darkClass(), true);

  // When the task button returns to idle (the login failed)
  browser.setRunning(false);
  browser.flushObservers();

  // Then the page follows the current HA theme again
  assert.equal(browser.darkClass(), false);
  assert.equal(browser.linkDisabled(), true);
});

test("Ghost takeover stops re-application", () => {
  // Given an active dark sign-in page whose submit is in flight
  const browser = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: DARK_PARENT
  });
  browser.load();
  browser.dispatchSubmit();

  // When Ghost applies the account's (light) Night shift
  browser.setGhostTheme(false, true);
  browser.flushObservers();

  // Then a later HA theme change does not fight it
  browser.setParentVars(DARK_PARENT);
  browser.flushObservers();
  assert.equal(browser.darkClass(), false);
  assert.equal(browser.linkDisabled(), true);
});

test("logout after a takeover re-arms", () => {
  // Given a takeover happened during the post-login wait
  const browser = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: DARK_PARENT
  });
  browser.load();
  browser.dispatchSubmit();
  browser.setGhostTheme(false, true);
  browser.flushObservers();

  // When the SPA moves to an authenticated route and the user signs out
  browser.setHash("#/dashboard");
  browser.setHash("#/signin");

  // Then the sign-in screen follows the HA theme again
  assert.equal(browser.darkClass(), true);
  assert.equal(browser.linkDisabled(), false);
});

test("takeover is only detected while frozen", () => {
  // Given an active dark sign-in page with no submit in flight
  const browser = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: DARK_PARENT
  });
  browser.load();

  // When the theme state changes without a submit in flight
  browser.setGhostTheme(false, true);
  browser.flushObservers();

  // Then the script is not in takeover mode: it still follows the HA theme
  browser.setParentVars(LIGHT_PARENT);
  browser.flushObservers();
  assert.equal(browser.darkClass(), false);
  assert.equal(browser.linkDisabled(), true);
  assert.equal(browser.styleCount(), 1);
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
      session: "absent",
      hash: "#/signin",
      parentVars: {"--clear-background-color": color}
    });

    // When the script loads
    browser.load();

    // Then the color decides the theme
    assert.equal(browser.darkClass(), expected, color);
  }
});

test("invalid parent colors fall back without throwing", () => {
  // Given an unparseable color, the OS scheme decides
  const invalid = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: {"--clear-background-color": "not-a-color"},
    localDark: true
  });
  invalid.load();
  assert.equal(invalid.darkClass(), true);

  // And an out-of-range channel is rejected the same way
  const outOfRange = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: {"--clear-background-color": "rgb(300, 0, 0)"},
    localDark: false
  });
  outOfRange.load();
  assert.equal(outOfRange.darkClass(), false);

  // And the next candidate is used when the first is unparseable
  const fallback = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: {
      "--clear-background-color": "not-a-color",
      "--primary-background-color": "#111318"
    }
  });
  fallback.load();
  assert.equal(fallback.darkClass(), true);
});

test("luminance threshold classifies mid greys", () => {
  // Given the WCAG 0.5 threshold, a dark grey and a light grey
  const darkGrey = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: {"--clear-background-color": "#808080"}
  });
  darkGrey.load();
  assert.equal(darkGrey.darkClass(), true);

  const lightGrey = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: {"--clear-background-color": "#bfbfbf"}
  });
  lightGrey.load();
  assert.equal(lightGrey.darkClass(), false);
});

test("Ghost takeover to dark is detected", () => {
  // Given a light HA sign-in page whose submit is in flight
  const browser = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: LIGHT_PARENT
  });
  browser.load();
  assert.equal(browser.darkClass(), false);
  browser.dispatchSubmit();

  // When Ghost applies the account's dark Night shift
  browser.setGhostTheme(true, false);
  browser.flushObservers();

  // Then a later HA theme change does not fight it
  browser.setParentVars(DARK_PARENT);
  browser.flushObservers();
  assert.equal(browser.darkClass(), true);
  assert.equal(browser.linkDisabled(), false);

  // And logout re-arms the HA theme
  browser.setParentVars(LIGHT_PARENT);
  browser.setHash("#/dashboard");
  browser.setHash("#/signin");
  assert.equal(browser.darkClass(), false);
  assert.equal(browser.linkDisabled(), true);
});

test("freeze survives a body mutation before the running state appears", () => {
  // Given an active dark sign-in page whose submit is in flight, and a body
  // mutation arrives before the task button renders its running state
  const browser = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: DARK_PARENT
  });
  browser.load();
  browser.dispatchSubmit({running: false});
  browser.flushObservers();

  // When the HA theme turns light, the freeze must still hold
  browser.setParentVars(LIGHT_PARENT);
  browser.flushObservers();
  assert.equal(browser.darkClass(), true);

  // And once the button has been seen running and returns to idle, the page
  // follows the HA theme again
  browser.setRunning(true);
  browser.flushObservers();
  browser.setRunning(false);
  browser.flushObservers();
  assert.equal(browser.darkClass(), false);
  assert.equal(browser.linkDisabled(), true);
});

test("critical CSS reaches only the auth flow", () => {
  // Given the critical stylesheet inserted on an ingress admin page
  const browser = createBrowser({
    session: "absent",
    hash: "#/signin",
    parentVars: DARK_PARENT
  });
  browser.load();
  const css = browser.styleText();

  // Then the generic widget rules are scoped to .gh-flow, so the
  // authenticated admin is never restyled
  assert.match(css, /html\.dark \.gh-flow form label/);
  assert.match(css, /html\.dark \.gh-flow \.gh-input::placeholder/);
  assert.match(css, /html\.dark \.gh-flow \.gh-input-icon svg/);
  assert.doesNotMatch(css, /html\.dark form label/);
  assert.doesNotMatch(css, /html\.dark \.gh-input::placeholder/);
  assert.doesNotMatch(css, /html\.dark \.gh-input-icon svg/);
});
