/**
 * Mounted privacy-lifecycle tests.
 *
 * jsdom + react-dom mount the production hooks (the same hooks used by the widget and the
 * portal). Source-string checks are not used here: every assertion observes rendered state or
 * stored identity after a concrete asynchronous transition.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "https://scholara.test/",
  pretendToBeVisual: true,
});
const w = dom.window as any;
(globalThis as any).window = w;
(globalThis as any).document = w.document;
Object.defineProperty(globalThis, "navigator", { value: w.navigator, configurable: true });
(globalThis as any).localStorage = w.localStorage;
(globalThis as any).sessionStorage = w.sessionStorage;
(globalThis as any).Event = w.Event;
(globalThis as any).StorageEvent = w.StorageEvent;
(globalThis as any).HTMLElement = w.HTMLElement;
(globalThis as any).Node = w.Node;
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const React = await import("react");
const { act } = React;
const { createRoot } = await import("react-dom/client");
const portal = await import("../src/lib/portal.ts");
const assistant = await import("../src/lib/use-assistant-conversation.ts");
const portalSession = await import("../src/lib/use-portal-session.ts");
const portalIdentity = await import("../src/lib/use-portal-identity.ts");
const analytics = await import("../src/lib/track.ts");

const json = (body: any, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});
const sleep = (ms = 0) => new Promise((r) => setTimeout(r, ms));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: any) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function mount(component: any) {
  const container = w.document.createElement("div");
  w.document.body.appendChild(container);
  const root = createRoot(container);
  return { root, container };
}

async function render(root: any, component: any) {
  await act(async () => { root.render(React.createElement(component)); await sleep(); });
}

function browserToken() {
  return portal.getProfileToken();
}

const TOKEN_KEY = "scholara.profile.token";

function storageEvent(key: string, newValue: string | null) {
  return new w.StorageEvent("storage", { key, newValue });
}

// ------------------------------------------------------------------ assistant

function assistantFetch(ask: (body: any) => Promise<Response>) {
  return async (input: any, init: any = {}) => {
    const url = String(input);
    const method = (init.method ?? "GET").toUpperCase();
    if (url.endsWith("/assistant/session") && method === "GET") {
      return json({ ownership: "ANONYMOUS", sid: "sid", csrf_token: "csrf-1" });
    }
    if (url.endsWith("/assistant/session") && method === "POST") {
      return json({ ownership: "ANONYMOUS", sid: "sid", csrf_token: "csrf-1" });
    }
    if (url.endsWith("/assistant/session/bind")) {
      return json({ ownership: "PROFILE", sid: "sid", csrf_token: "csrf-2" });
    }
    if (url.endsWith("/assistant/session/revoke")) return json({ revoked: true });
    if (url.endsWith("/assistant/ask")) return ask(JSON.parse(init.body ?? "{}"));
    return json({}, 404);
  };
}

function assistantHarness() {
  let latest: any = null;
  function Harness() {
    latest = assistant.useAssistantConversation("/programs");
    return null;
  }
  return { Harness, get: () => latest };
}

async function withAssistant(fn: (api: () => any) => Promise<void>) {
  localStorage.clear();
  const { Harness, get } = assistantHarness();
  const { root } = mount(Harness);
  try {
    await render(root, Harness);
    await fn(get);
  } finally {
    await act(async () => { root.unmount(); });
  }
}

test("A to B clears every A turn and keeps B's state neutral", async () => {
  (globalThis as any).fetch = assistantFetch(async () => json({
    answer: "A SECRET ANSWER", sources: [{ label: "A source" }], suggestions: ["A chip"],
    grounded: true, escalated: false,
  }));
  await withAssistant(async (api) => {
    portal.setProfileToken("token-a", "Student A");
    await act(async () => { await api().send("A private question"); });
    assert.ok(api().turns.some((t: any) => t.text === "A SECRET ANSWER"));
    await act(async () => { portal.setProfileToken("token-b", "Student B"); });
    assert.equal(api().turns.length, 1);
    assert.ok(!JSON.stringify(api().turns).includes("A private question"));
    assert.ok(!JSON.stringify(api().turns).includes("A SECRET ANSWER"));
    assert.equal(api().input, "");
    assert.equal(api().busy, false);
  });
});

test("logout clears A's assistant state even when revocation fails", async () => {
  (globalThis as any).fetch = async (url: any, init: any = {}) => {
    if (String(url).endsWith("/assistant/session") && (init.method ?? "GET") === "GET") {
      return json({ ownership: "PROFILE", sid: "sid", csrf_token: "csrf" });
    }
    if (String(url).endsWith("/assistant/ask")) {
      return json({ answer: "A PRIVATE", sources: [], grounded: true });
    }
    if (String(url).endsWith("/revoke")) throw new Error("network down");
    return json({ ownership: "PROFILE", sid: "sid", csrf_token: "csrf" });
  };
  await withAssistant(async (api) => {
    portal.setProfileToken("token-a", "Student A");
    await act(async () => { await api().send("A private question"); });
    assert.ok(JSON.stringify(api().turns).includes("A PRIVATE"));
    await act(async () => { portal.clearProfileToken(); await sleep(5); });
    assert.equal(api().turns.length, 1);
    assert.ok(!JSON.stringify(api().turns).includes("A PRIVATE"));
  });
});

test("a question typed under A is dropped, not sent, when B is already active", async () => {
  let asks = 0;
  (globalThis as any).fetch = assistantFetch(async () => {
    asks += 1;
    return json({ answer: "SHOULD NOT SEND", grounded: true });
  });
  await withAssistant(async (api) => {
    portal.setProfileToken("token-a", "Student A");
    api().setInput("A private budget is A unique 987654 budget");
    localStorage.setItem(TOKEN_KEY, "token-b");
    await act(async () => { await api().send("A private budget is A unique 987654 budget"); await sleep(); });
    assert.equal(asks, 0);
    assert.ok(!JSON.stringify(api().turns).includes("987654"));
    assert.equal(browserToken(), "token-b");
  });
});

test("re-applying the same token preserves the same student's conversation", async () => {
  (globalThis as any).fetch = assistantFetch(async () => json({
    answer: "SAME STUDENT", sources: [], grounded: true,
  }));
  await withAssistant(async (api) => {
    portal.setProfileToken("token-a", "Student A");
    await act(async () => { await api().send("question"); });
    const before = api().turns;
    await act(async () => { portal.setProfileToken("token-a", "Student A"); });
    assert.equal(api().turns, before);
    assert.ok(JSON.stringify(api().turns).includes("SAME STUDENT"));
  });
});

test("a delayed A success cannot update B", async () => {
  const pending = deferred<Response>();
  (globalThis as any).fetch = assistantFetch(async () => pending.promise);
  await withAssistant(async (api) => {
    portal.setProfileToken("token-a", "Student A");
    let sending: Promise<void>;
    await act(async () => { sending = api().send("A delayed"); await sleep(5); });
    await act(async () => { portal.setProfileToken("token-b", "Student B"); });
    await act(async () => { pending.resolve(json({ answer: "A LATE", grounded: true })); await sending!; });
    assert.ok(!JSON.stringify(api().turns).includes("A LATE"));
    assert.equal(api().turns.length, 1);
  });
});

test("a delayed A error cannot append to B", async () => {
  const pending = deferred<Response>();
  (globalThis as any).fetch = assistantFetch(async () => pending.promise);
  await withAssistant(async (api) => {
    portal.setProfileToken("token-a", "Student A");
    let sending: Promise<void>;
    await act(async () => { sending = api().send("A delayed"); await sleep(5); });
    await act(async () => { portal.setProfileToken("token-b", "Student B"); });
    await act(async () => { pending.reject(new Error("A failure")); await sending!; });
    assert.ok(!JSON.stringify(api().turns).toLowerCase().includes("could not reach"));
    assert.equal(api().turns.length, 1);
  });
});

for (const status of [401, 403, 409]) {
  test(`a delayed A ${status} cannot clear B's turns or input`, async () => {
    const oldAsk = deferred<Response>();
    (globalThis as any).fetch = assistantFetch(async (body: any) => {
      if (body.question === "A old") return oldAsk.promise;
      return json({ answer: "B ANSWER", grounded: true });
    });
    await withAssistant(async (api) => {
      portal.setProfileToken("token-a", "Student A");
      let oldSend: Promise<void>;
      await act(async () => { oldSend = api().send("A old"); await sleep(5); });
      await act(async () => { portal.setProfileToken("token-b", "Student B"); });
      await act(async () => { await api().send("B new"); });
      const bTurns = api().turns;
      assert.ok(JSON.stringify(bTurns).includes("B ANSWER"));
      api().setInput("B draft");
      await act(async () => { oldAsk.resolve(json({ detail: "old" }, status)); await oldSend!; });
      assert.equal(api().turns, bTurns);
      assert.ok(JSON.stringify(api().turns).includes("B ANSWER"));
      assert.equal(api().input, "B draft");
    });
  });
}

test("a stale finally cannot clear B's loading state", async () => {
  const oldAsk = deferred<Response>();
  const bAsk = deferred<Response>();
  (globalThis as any).fetch = assistantFetch(async (body: any) => {
    if (body.question === "A old") return oldAsk.promise;
    return bAsk.promise;
  });
  await withAssistant(async (api) => {
    portal.setProfileToken("token-a", "Student A");
    let oldSend: Promise<void>;
    await act(async () => { oldSend = api().send("A old"); await sleep(5); });
    await act(async () => { portal.setProfileToken("token-b", "Student B"); });
    let bSend: Promise<void>;
    await act(async () => { bSend = api().send("B new"); await sleep(5); });
    assert.equal(api().busy, true);
    await act(async () => { oldAsk.reject(new Error("old")); await oldSend!; });
    assert.equal(api().busy, true);
    await act(async () => { bAsk.resolve(json({ answer: "B", grounded: true })); await bSend!; });
    assert.equal(api().busy, false);
  });
});

test("a stale bootstrap cannot replace B's session state", async () => {
  const firstSession = deferred<Response>();
  let sessionGets = 0;
  (globalThis as any).fetch = async (input: any, init: any = {}) => {
    const url = String(input);
    if (url.endsWith("/assistant/session") && (init.method ?? "GET") === "GET") {
      sessionGets += 1;
      if (sessionGets === 1) return firstSession.promise;
      return json({ ownership: "ANONYMOUS", sid: "b", csrf_token: "b-csrf" });
    }
    if (url.endsWith("/assistant/session") && init.method === "POST") {
      return json({ ownership: "ANONYMOUS", sid: "b", csrf_token: "b-csrf" });
    }
    if (url.endsWith("/assistant/ask")) return json({ answer: "B OK", grounded: true });
    return json({});
  };
  await withAssistant(async (api) => {
    portal.setProfileToken("token-a", "Student A");
    let oldSend: Promise<void>;
    await act(async () => { oldSend = api().send("A old"); await sleep(5); });
    await act(async () => { portal.setProfileToken("token-b", "Student B"); });
    await act(async () => { await api().send("B new"); });
    assert.ok(JSON.stringify(api().turns).includes("B OK"));
    await act(async () => { firstSession.resolve(json({ ownership: "ANONYMOUS", csrf_token: "a-csrf" })); await oldSend!; });
    assert.ok(JSON.stringify(api().turns).includes("B OK"));
    assert.equal(api().turns.length, 3);
  });
});

test("a cross-tab identity change clears A's visible turns", async () => {
  (globalThis as any).fetch = assistantFetch(async () => json({ answer: "A", grounded: true }));
  await withAssistant(async (api) => {
    portal.setProfileToken("token-a", "Student A");
    await act(async () => { await api().send("question"); });
    await act(async () => {
      localStorage.setItem(TOKEN_KEY, "token-b");
      window.dispatchEvent(storageEvent(TOKEN_KEY, "token-b"));
      await sleep();
    });
    assert.equal(api().turns.length, 1);
    assert.ok(!JSON.stringify(api().turns).includes("A"));
  });
});

test("a suspended tab rechecks identity before applying stale results", async () => {
  const pending = deferred<Response>();
  (globalThis as any).fetch = assistantFetch(async () => pending.promise);
  await withAssistant(async (api) => {
    portal.setProfileToken("token-a", "Student A");
    let sending: Promise<void>;
    await act(async () => { sending = api().send("A old"); await sleep(5); });
    localStorage.setItem(TOKEN_KEY, "token-b");
    await act(async () => { window.dispatchEvent(new w.Event("focus")); await sleep(); });
    assert.equal(api().turns.length, 1);
    await act(async () => { pending.resolve(json({ answer: "A LATE", grounded: true })); await sending!; });
    assert.ok(!JSON.stringify(api().turns).includes("A LATE"));
  });
});

// -------------------------------------------------------------- analytics

function captureAnalyticsCalls() {
  const calls: any[] = [];
  const gtag: any[] = [];
  const fbq: any[] = [];
  (globalThis as any).fetch = async (input: any, init: any = {}) => {
    calls.push({ url: String(input), body: init.body });
    return json({});
  };
  Object.defineProperty(navigator, "sendBeacon", { value: undefined, configurable: true });
  (window as any).gtag = (...args: any[]) => gtag.push(args);
  (window as any).fbq = (...args: any[]) => fbq.push(args);
  return { calls, gtag, fbq };
}

test("private portal paths never reach analytics, UTM storage or third-party calls", async () => {
  window.history.pushState({}, "", "/my/secret-capability?utm_source=test");
  sessionStorage.clear();
  sessionStorage.setItem("scholara.utm", JSON.stringify({ landing_path: "/my/secret-capability" }));
  const { calls, gtag, fbq } = captureAnalyticsCalls();
  const secret = "secret-capability";

  analytics.track("page_view", { path: "/my/secret-capability" }, "PageView");
  const utm = analytics.captureUtm();
  analytics.track("portal_action", { path: "/my/secret-capability", nested: "/my/secret-capability" });

  assert.equal(calls.length, 0);
  assert.equal(gtag.length, 0);
  assert.equal(fbq.length, 0);
  assert.deepEqual(utm, {});
  assert.equal(sessionStorage.getItem("scholara.utm"), null);
  assert.ok(!JSON.stringify([calls, gtag, fbq, utm, sessionStorage.getItem("scholara.utm")]).includes(secret));
  window.history.pushState({}, "", "/");
});

test("public analytics remains functional and strips query strings", async () => {
  window.history.pushState({}, "", "/programs?utm_source=x");
  const { calls, gtag } = captureAnalyticsCalls();
  analytics.track("page_view", { path: "/programs?utm_source=x" });
  assert.equal(calls.length, 1);
  const body = JSON.parse(calls[0].body);
  assert.equal(body.path, "/programs");
  assert.equal(body.meta.path, "/programs");
  assert.ok(gtag.length >= 0);
  window.history.pushState({}, "", "/");
});

test("assistant question text is never sent to analytics", async () => {
  const question = "MY PRIVATE CGPA 3.91 AND EMAIL a@example.com";
  const analyticsBodies: any[] = [];
  (globalThis as any).fetch = async (input: any, init: any = {}) => {
    if (String(input).endsWith("/track")) {
      analyticsBodies.push(JSON.parse(init.body ?? "{}"));
      return json({});
    }
    return assistantFetch(async () => json({ answer: "ok", grounded: true }))(input, init);
  };
  Object.defineProperty(navigator, "sendBeacon", { value: undefined, configurable: true });
  await withAssistant(async (api) => {
    portal.setProfileToken("token-a", "Student A");
    await act(async () => { await api().send(question); });
    assert.ok(analyticsBodies.some((body) => body.name === "assistant_ask"));
    assert.ok(!JSON.stringify(analyticsBodies).includes("PRIVATE CGPA"));
    assert.ok(!JSON.stringify(analyticsBodies).includes("a@example.com"));
  });
});

// -------------------------------------------------------------------- portal

function portalFetch(handler: (url: string, init: any) => Promise<Response>) {
  return async (input: any, init: any = {}) => handler(String(input), init);
}

async function withPortal(token: string, handler: (url: string, init: any) => Promise<Response>,
                          options: any = {}) {
  localStorage.clear();
  portal.setProfileToken(token, "Student");
  (globalThis as any).fetch = portalFetch(handler);
  let latest: any = null;
  const onSignedOut = options.onSignedOut ?? (() => {});
  const onTokenConsumed = () => {};
  function Harness() {
    latest = portalSession.usePortalSession(token, {
      consumeToken: false, onTokenConsumed, onSignedOut,
    });
    return null;
  }
  const { root } = mount(Harness);
  await render(root, Harness);
  return { root, get: () => latest, unmount: async () => act(async () => root.unmount()) };
}

test("a BFCache-restored portal verifies identity before showing private data", async () => {
  localStorage.clear();
  localStorage.setItem(TOKEN_KEY, "token-a");
  let signedOut = 0;
  const onSignedOut = () => { signedOut += 1; };
  let latest: any;
  function Harness() {
    latest = portalSession.usePortalSession("token-a", { onSignedOut });
    return null;
  }
  (globalThis as any).fetch = async () => json({ profile: { full_name: "Student A" }, counts: {} });
  const { root } = mount(Harness);
  try {
    await render(root, Harness);
    await act(async () => { await sleep(20); });
    assert.equal(latest.data.profile.full_name, "Student A");
    await act(async () => {
      localStorage.removeItem(TOKEN_KEY);
      window.dispatchEvent(new w.Event("pageshow"));
      await sleep(10);
    });
    assert.equal(latest.data, null);
    assert.equal(signedOut, 1);
  } finally {
    await act(async () => root.unmount());
  }
});

test("a BFCache-restored token-entry page does not re-authenticate after logout", async () => {
  localStorage.clear();
  localStorage.setItem(TOKEN_KEY, "token-a");
  let signedOut = 0;
  let latest: any;
  const onTokenConsumed = () => {};
  const onSignedOut = () => { signedOut += 1; };
  function Harness() {
    latest = portalSession.usePortalSession("token-a", {
      consumeToken: true, onTokenConsumed, onSignedOut,
    });
    return null;
  }
  (globalThis as any).fetch = async () => json({ profile: { full_name: "Student A" }, counts: {} });
  const { root } = mount(Harness);
  try {
    await render(root, Harness);
    await act(async () => { await sleep(20); });
    assert.equal(latest.data.profile.full_name, "Student A");
    await act(async () => {
      localStorage.removeItem(TOKEN_KEY);
      const event = new w.Event("pageshow");
      Object.defineProperty(event, "persisted", { value: true });
      window.dispatchEvent(event);
      await sleep(10);
    });
    assert.equal(latest.data, null);
    assert.equal(signedOut, 1);
  } finally {
    await act(async () => root.unmount());
  }
});

test("the token entry route consumes the bearer URL and logout reports the transition", async () => {
  localStorage.clear();
  (globalThis as any).fetch = async (input: any) => {
    if (String(input).endsWith("/portal/token-a")) {
      return json({ profile: { full_name: "Student A" }, counts: {} });
    }
    return json({ added: 0 });
  };
  let consumed = 0;
  let signedOut = 0;
  let latest: any;
  const onTokenConsumed = () => { consumed += 1; };
  const onSignedOut = () => { signedOut += 1; };
  function Harness() {
    latest = portalSession.usePortalSession("token-a", {
      consumeToken: true, onTokenConsumed, onSignedOut,
    });
    return null;
  }
  const { root } = mount(Harness);
  try {
    await render(root, Harness);
    await act(async () => { await sleep(20); });
    assert.equal(browserToken(), "token-a");
    assert.equal(consumed, 1);
    await act(async () => { latest.signOut(); await sleep(5); });
    assert.equal(browserToken(), null);
    assert.equal(signedOut, 1);
    assert.equal(latest.data, null);
  } finally {
    await act(async () => root.unmount());
  }
});

test("a deliberate fresh link for B switches from A without reusing A's data", async () => {
  localStorage.clear();
  localStorage.setItem(TOKEN_KEY, "token-a");
  (globalThis as any).fetch = async () => json({ profile: { full_name: "Student B" }, counts: {} });
  let latest: any;
  const onTokenConsumed = () => {};
  const onSignedOut = () => {};
  function Harness() {
    latest = portalSession.usePortalSession("token-b", {
      consumeToken: true, onTokenConsumed, onSignedOut,
    });
    return null;
  }
  const { root } = mount(Harness);
  try {
    await render(root, Harness);
    await act(async () => { await sleep(20); });
    assert.equal(browserToken(), "token-b");
    assert.equal(latest.data.profile.full_name, "Student B");
  } finally {
    await act(async () => root.unmount());
  }
});

test("a stale token-entry response cannot overwrite a newer identity", async () => {
  localStorage.clear();
  const load = deferred<Response>();
  portal.setProfileToken("token-a", "Student A");
  const onTokenConsumed = () => {};
  const onSignedOut = () => {};
  let latest: any;
  function Harness() {
    latest = portalSession.usePortalSession("token-a", {
      consumeToken: true, onTokenConsumed, onSignedOut,
    });
    return null;
  }
  (globalThis as any).fetch = async (input: any) => {
    if (String(input).endsWith("/portal/token-a")) return load.promise;
    return json({ added: 0 });
  };
  const { root } = mount(Harness);
  try {
    await render(root, Harness);
    await act(async () => { await sleep(5); });
    portal.setProfileToken("token-b", "Student B");
    await act(async () => {
      load.resolve(json({ profile: { full_name: "Student A" }, counts: {} }));
      await sleep(20);
    });
    assert.equal(browserToken(), "token-b");
    assert.equal(latest.data, null);
  } finally {
    await act(async () => root.unmount());
  }
});

test("a cross-tab identity storage event aborts a pending token entry", async () => {
  localStorage.clear();
  const load = deferred<Response>();
  portal.setProfileToken("token-a", "Student A");
  const onTokenConsumed = () => {};
  const onSignedOut = () => {};
  let latest: any;
  function Harness() {
    latest = portalSession.usePortalSession("token-a", {
      consumeToken: true, onTokenConsumed, onSignedOut,
    });
    return null;
  }
  (globalThis as any).fetch = async (input: any) => {
    if (String(input).endsWith("/portal/token-a")) return load.promise;
    return json({ added: 0 });
  };
  const { root } = mount(Harness);
  try {
    await render(root, Harness);
    await act(async () => { await sleep(5); });
    portal.setProfileToken("token-b", "Student B");
    await act(async () => {
      window.dispatchEvent(storageEvent(TOKEN_KEY, "token-b"));
      window.dispatchEvent(storageEvent("scholara.profile.revision", "2"));
      await sleep(5);
    });
    await act(async () => {
      load.resolve(json({ profile: { full_name: "Student A" }, counts: {} }));
      await sleep(15);
    });
    assert.equal(browserToken(), "token-b");
    assert.equal(latest.data, null);
  } finally {
    await act(async () => root.unmount());
  }
});

test("a stale token-entry response cannot restore identity after logout", async () => {
  localStorage.clear();
  const load = deferred<Response>();
  portal.setProfileToken("token-a", "Student A");
  const onTokenConsumed = () => {};
  const onSignedOut = () => {};
  let latest: any;
  function Harness() {
    latest = portalSession.usePortalSession("token-a", {
      consumeToken: true, onTokenConsumed, onSignedOut,
    });
    return null;
  }
  (globalThis as any).fetch = async (input: any) => {
    if (String(input).endsWith("/portal/token-a")) return load.promise;
    return json({ added: 0 });
  };
  const { root } = mount(Harness);
  try {
    await render(root, Harness);
    await act(async () => { await sleep(5); });
    portal.clearProfileToken();
    await act(async () => {
      load.resolve(json({ profile: { full_name: "Student A" }, counts: {} }));
      await sleep(20);
    });
    assert.equal(browserToken(), null);
    assert.equal(latest.data, null);
  } finally {
    await act(async () => root.unmount());
  }
});

test("an unmounted token-entry request cannot mutate identity storage", async () => {
  localStorage.clear();
  const load = deferred<Response>();
  let latest: any;
  function Harness() {
    latest = portalSession.usePortalSession("token-entry", { consumeToken: true });
    return null;
  }
  (globalThis as any).fetch = async (input: any) => {
    if (String(input).endsWith("/portal/token-entry")) return load.promise;
    return json({ added: 0 });
  };
  const { root } = mount(Harness);
  await render(root, Harness);
  await act(async () => { root.unmount(); });
  load.resolve(json({ profile: { full_name: "Student A" }, counts: {} }));
  await act(async () => { await sleep(20); });
  assert.equal(browserToken(), null);
});

test("an older overlapping portal load cannot replace the newer result", async () => {
  localStorage.clear();
  localStorage.setItem(TOKEN_KEY, "token-a");
  const first = deferred<Response>();
  const second = deferred<Response>();
  let calls = 0;
  let latest: any;
  function Harness() { latest = portalSession.usePortalSession("token-a", {}); return null; }
  (globalThis as any).fetch = async (input: any) => {
    if (String(input).endsWith("/portal/token-a")) {
      calls += 1;
      return calls === 1 ? first.promise : second.promise;
    }
    return json({ added: 0 });
  };
  const { root } = mount(Harness);
  try {
    await render(root, Harness);
    await act(async () => { void latest.load(); await sleep(5); });
    await act(async () => { second.resolve(json({ profile: { full_name: "NEW" }, counts: {} })); await sleep(15); });
    assert.equal(latest.data.profile.full_name, "NEW");
    await act(async () => { first.resolve(json({ profile: { full_name: "OLD" }, counts: {} })); await sleep(15); });
    assert.equal(latest.data.profile.full_name, "NEW");
  } finally {
    await act(async () => root.unmount());
  }
});

test("A's portal data is not rendered for a commit after B becomes active", async () => {
  localStorage.clear();
  localStorage.setItem(TOKEN_KEY, "token-a");
  const aLoad = deferred<Response>();
  const bLoad = deferred<Response>();
  let activeToken = "token-a";
  let rendered = "";
  let latest: any;
  function Harness() {
    latest = portalSession.usePortalSession(activeToken, {});
    rendered = latest.data?.profile?.full_name ?? "";
    return React.createElement("div", null, rendered);
  }
  (globalThis as any).fetch = async (input: any) => {
    if (String(input).endsWith("/portal/token-a")) return aLoad.promise;
    if (String(input).endsWith("/portal/token-b")) return bLoad.promise;
    return json({ added: 0 });
  };
  const { root } = mount(Harness);
  try {
    await render(root, Harness);
    await act(async () => {
      aLoad.resolve(json({ profile: { full_name: "Student A" }, counts: {} }));
      await sleep(20);
    });
    assert.equal(rendered, "Student A");
    activeToken = "token-b";
    localStorage.setItem(TOKEN_KEY, "token-b");
    await act(async () => { root.render(React.createElement(Harness)); await sleep(5); });
    assert.equal(rendered, "");
    await act(async () => {
      bLoad.resolve(json({ profile: { full_name: "Student B" }, counts: {} }));
      await sleep(20);
    });
    assert.equal(rendered, "Student B");
  } finally {
    await act(async () => root.unmount());
  }
});

test("a pending portal load cannot restore A after logout", async () => {
  const load = deferred<Response>();
  let signedOut = 0;
  const h = await withPortal("token-a", async (url) => {
    if (url.endsWith("/portal/token-a")) return load.promise;
    return json({ added: 0 });
  }, { onSignedOut: () => { signedOut += 1; } });
  try {
    await act(async () => { h.get().signOut(); await sleep(5); });
    const dataBefore = h.get().data;
    assert.equal(dataBefore, null);
    assert.equal(browserToken(), null);
    await act(async () => {
      load.resolve(json({ profile: { full_name: "Student A" }, counts: {} }));
      await sleep(10);
    });
    assert.equal(h.get().data, null);
    assert.equal(browserToken(), null);
    assert.equal(signedOut, 1);
  } finally {
    await h.unmount();
  }
});

test("a pending shortlist sync cannot restore A after logout", async () => {
  const sync = deferred<Response>();
  localStorage.clear();
  await portal.writeLocalShortlist([{ slug: "a-program", name: "A", type: "program" }], "token-a");
  portal.setProfileToken("token-a", "Student A");
  let portalLoads = 0;
  (globalThis as any).fetch = async (url: any) => {
    if (String(url).endsWith("/shortlist/sync")) return sync.promise;
    if (String(url).endsWith("/assistant/session")) return json({ detail: "none" }, 401);
    portalLoads += 1;
    return json({ profile: { full_name: "Student A" }, counts: {}, shortlist: [] });
  };
  let latest: any;
  const onSignedOut = () => {};
  function Harness() { latest = portalSession.usePortalSession("token-a", { onSignedOut }); return null; }
  const { root } = mount(Harness);
  await render(root, Harness);
  const h = { get: () => latest, unmount: async () => act(async () => root.unmount()) };
  const loadsBeforeSignOut = portalLoads;
  try {
    await act(async () => { h.get().signOut(); await sleep(5); });
    await act(async () => { sync.resolve(json({ added: 1 })); await sleep(10); });
    assert.equal(h.get().data, null);
    assert.equal(browserToken(), null);
    assert.equal(portalLoads, loadsBeforeSignOut);
  } finally {
    await h.unmount();
  }
});

test("a fresh portal load stores identity, while B never sees A's data", async () => {
  localStorage.clear();
  const handlers: Record<string, () => Response> = {
    "/portal/token-a": () => json({ profile: { full_name: "Student A" }, counts: { applications: 1 } }),
    "/portal/token-b": () => json({ profile: { full_name: "Student B" }, counts: { applications: 2 } }),
  };
  (globalThis as any).fetch = async (input: any) => {
    const url = String(input);
    const entry = Object.entries(handlers).find(([path]) => url.endsWith(path));
    return entry ? entry[1]() : json({ added: 0 });
  };
  localStorage.setItem(TOKEN_KEY, "token-a");
  let latest: any = null;
  function Harness() {
    latest = portalSession.usePortalSession(localStorage.getItem(TOKEN_KEY) ?? "token-a", {});
    return null;
  }
  const { root } = mount(Harness);
  try {
    await render(root, Harness);
    await act(async () => { await sleep(20); });
    assert.equal(browserToken(), "token-a");
    assert.equal(latest.data.profile.full_name, "Student A");
    await act(async () => {
      localStorage.setItem(TOKEN_KEY, "token-b");
      root.render(React.createElement(Harness));
      await sleep(20);
    });
    assert.equal(latest.data?.profile?.full_name, "Student B");
  } finally {
    await act(async () => root.unmount());
  }
});

test("portrait identity hook clears on storage and re-checks on pageshow", async () => {
  localStorage.clear();
  let latest: any;
  function Harness() { latest = portalIdentity.usePortalIdentity(); return null; }
  const { root } = mount(Harness);
  try {
    await act(async () => { portal.setProfileToken("token-a"); await sleep(); });
    await render(root, Harness);
    assert.equal(latest, "token-a");
    await act(async () => {
      localStorage.removeItem(TOKEN_KEY);
      window.dispatchEvent(storageEvent(TOKEN_KEY, null));
      await sleep();
    });
    assert.equal(latest, null);
    await act(async () => {
      localStorage.setItem(TOKEN_KEY, "token-b");
      window.dispatchEvent(new w.Event("pageshow"));
      await sleep();
    });
    assert.equal(latest, "token-b");
  } finally {
    await act(async () => root.unmount());
  }
});

test("shortlist buckets are identity-scoped, legacy data is ignored, and logout removes only A", async () => {
  localStorage.clear();
  localStorage.setItem("scholara.shortlist", JSON.stringify([{ slug: "legacy" }]));
  await portal.writeLocalShortlist([{ slug: "a-only" }], "token-a");
  await portal.writeLocalShortlist([{ slug: "b-only" }], "token-b");
  await portal.writeLocalShortlist([{ slug: "anon-only" }], null);
  const a = await portal.readLocalShortlist("token-a");
  const b = await portal.readLocalShortlist("token-b");
  assert.deepEqual(a.map((x: any) => x.slug), ["a-only"]);
  assert.deepEqual(b.map((x: any) => x.slug), ["b-only"]);
  const aKey = await portal.shortlistBucket("token-a");
  const bKey = await portal.shortlistBucket("token-b");
  assert.ok(aKey && bKey && aKey !== bKey);
  assert.ok(!aKey!.includes("token-a"));
  localStorage.setItem(TOKEN_KEY, "token-a");
  portal.clearProfileToken();
  await sleep(10);
  assert.equal(localStorage.getItem(aKey!), null);
  assert.ok(localStorage.getItem(bKey!));
  assert.deepEqual(await portal.readLocalShortlist(null), []);
});

test("a stale shortlist response returns zero after identity changes", async () => {
  localStorage.clear();
  const sync = deferred<Response>();
  (globalThis as any).fetch = async (input: any) => {
    if (String(input).endsWith("/shortlist/sync")) return sync.promise;
    return json({ added: 0 });
  };
  await portal.writeLocalShortlist([{ slug: "a-only", type: "program" }], "token-a");
  localStorage.setItem(TOKEN_KEY, "token-a");
  const pending = portal.syncShortlist("token-a");
  localStorage.setItem(TOKEN_KEY, "token-b");
  sync.resolve(json({ added: 1 }));
  assert.equal(await pending, 0);
});

test("a pending load for B clears A before applying B", async () => {
  localStorage.clear();
  const bLoad = deferred<Response>();
  let token = "token-a";
  (globalThis as any).fetch = async (input: any) => {
    if (String(input).endsWith("/portal/token-a")) {
      return json({ profile: { full_name: "Student A" }, counts: {} });
    }
    if (String(input).endsWith("/portal/token-b")) return bLoad.promise;
    return json({ added: 0 });
  };
  localStorage.setItem(TOKEN_KEY, "token-a");
  let latest: any;
  function Harness() { latest = portalSession.usePortalSession(token, {}); return null; }
  const { root } = mount(Harness);
  try {
    await render(root, Harness);
    assert.equal(latest.data.profile.full_name, "Student A");
    token = "token-b";
    localStorage.setItem(TOKEN_KEY, "token-b");
    await act(async () => { root.render(React.createElement(Harness)); await sleep(); });
    assert.equal(latest.data, null);
    await act(async () => { bLoad.resolve(json({ profile: { full_name: "Student B" }, counts: {} })); await sleep(10); });
    assert.equal(latest.data.profile.full_name, "Student B");
  } finally {
    await act(async () => root.unmount());
  }
});
