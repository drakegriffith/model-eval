You are given a small TypeScript project (a typed event bus) at the root of your
working directory. This is a ticket, not a bug report: the bus currently works
correctly for its existing feature set, and you're adding new functionality.
Its test suite currently fails to build/pass because it already covers the
new functionality described below — implement the feature so the entire test
suite passes.

Do not modify any file under `test/`. Do not weaken, delete, or skip any test.
Do not change the signature or behavior of the existing `on`, `off`, `emit`, or
`listenerCount` methods for non-wildcard usage.

## Problem statement

The event bus currently requires subscribing to each event name individually, and
every subscription is permanent until manually `off()`'d. Two things are blocking a
logging/analytics feature: (a) there's no way to subscribe to EVERY event for
debugging/audit purposes, and (b) one-shot listeners (e.g. "wait for the next
'ready' event") require manual boilerplate (call `off` inside the handler itself),
which is easy to get wrong.

## Solution

Add `once<K extends keyof Events>(event: K, handler: Handler<Events[K]>): void` that
behaves exactly like `on` except the handler is automatically unsubscribed (via the
equivalent of `off`) right after it fires for the first time — a second `emit` for
that event must NOT call it again.

Add wildcard subscription support: `on('*', handler)` where the wildcard handler
receives `{ event: keyof Events; payload: Events[keyof Events] }` for EVERY event
emitted (in addition to that event's normal specific handlers still firing);
wildcard handlers are unaffected by `off(specificEvent, ...)` calls and must be
removable via `off('*', wildcardHandler)`. `once` must also work with the wildcard
event (`once('*', handler)` fires for the very next emitted event of ANY type, then
unsubscribes).

## User stories

- As a feature developer, I want `bus.once('ready', cb)` so I don't have to
  manually manage unsubscription for a one-shot listener.
- As a platform engineer, I want `bus.on('*', logEvent)` so I can log every event
  flowing through the bus without subscribing to each event name individually.

## Implementation decisions

- `'*'` is a reserved wildcard event name; it is not a real member of `Events` —
  implement its typing pragmatically (e.g. an overload or a widened internal type)
  without breaking the existing generic `on`/`off`/`emit` signatures for real event
  names.
- Wildcard handlers fire AFTER the event's specific handlers, in the order emit
  was called.
- `once` must be implemented via composition on top of `on`/`off` (do not duplicate
  storage) so `listenerCount` still reflects `once` subscriptions until they fire.
- Do not change the signature or behavior of `on`, `off`, `emit`, or
  `listenerCount` for existing (non-wildcard) usage.

## Testing decisions

New tests must prove:
- (a) `once` handler fires exactly once even after two `emit` calls for that
  event.
- (b) `on('*', h)` receives every emitted event with correct `{event, payload}`
  shape across multiple different event types.
- (c) `off('*', h)` stops the wildcard handler from firing on subsequent emits.
- (d) `once('*', h)` fires for only the first event emitted (of any type) then
  stops.
- (e) existing specific-event handlers still fire normally alongside an active
  wildcard handler (both fire, order: specific then wildcard).

## Out of scope

No async/Promise-based event handling. No event namespacing/regex patterns beyond
the single `'*'` wildcard. No error handling/isolation between handlers (if one
handler throws, current behavior — whatever it is — is preserved, not specified
further).

Run the tests with:

```
npm install
npm test
```
