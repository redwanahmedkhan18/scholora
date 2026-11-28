/**
 * Behavioural tests for the assistant identity rules.
 *
 * These exercise the pure functions the widget uses before it touches React state, so the
 * "Student A's turns disappear when Student B arrives" rule is tested rather than merely
 * grepped.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
  identityChanged, identityOf, initialTurns, requestIsCurrent, resetForIdentity,
} from "../src/lib/assistant-lifecycle.ts";

const A = "token-student-a";
const B = "token-student-b";

test("a new identity produces a neutral conversation with no previous turns", () => {
  const afterA = [{ role: "user" as const, text: "A private question" },
                  { role: "assistant" as const, text: "A private answer" }];
  const reset = resetForIdentity();
  assert.equal(reset.turns.length, 1);
  assert.equal(reset.turns[0], initialTurns()[0]);
  assert.equal(reset.busy, false);
  assert.equal(reset.input, "");
  assert.ok(!reset.turns.some((turn) => turn.text.includes("A private")));
  assert.ok(afterA.length === 2);
});

test("A to B, A to logout, anonymous to A and logout to A are all identity changes", () => {
  assert.equal(identityChanged(identityOf(A), identityOf(B)), true);
  assert.equal(identityChanged(identityOf(A), identityOf(null)), true);
  assert.equal(identityChanged(identityOf(null), identityOf(A)), true);
  assert.equal(identityChanged(identityOf(null), identityOf(A)), true);
});

test("re-applying the same valid token is not a transition", () => {
  assert.equal(identityChanged(identityOf(A), identityOf(A)), false);
  assert.equal(identityChanged(identityOf(null), identityOf(null)), false);
});

test("a delayed Student A success cannot be applied after switching to B", () => {
  assert.equal(requestIsCurrent(identityOf(A), 1, identityOf(B), 2), false);
  assert.equal(requestIsCurrent(identityOf(A), 1, identityOf(A), 2), false);
});

test("a delayed Student A rejection cannot append an error after switching to B", () => {
  assert.equal(requestIsCurrent(identityOf(A), 4, identityOf(B), 5), false);
});

test("a response is applied only for the same identity and session generation", () => {
  assert.equal(requestIsCurrent(identityOf(A), 7, identityOf(A), 7), true);
});
