# event-bus

A small typed event bus. Subscribers register a handler for a specific event
name; emitting an event synchronously invokes every handler currently
registered for that event, in registration order.

`Events` maps event names to the payload type delivered for that event, so
`on`/`off`/`emit` are all checked against the same contract at the call site.

Run tests:

```
npm install
npm test
```
