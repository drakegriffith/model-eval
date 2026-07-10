import { describe, it, expect, vi } from "vitest";
import { EventBus, Handler } from "../src/eventBus";

type TestEvents = {
  ready: undefined;
  message: string;
  count: number;
};

describe("EventBus (existing contract)", () => {
  it("on + emit calls the handler with the payload", () => {
    const bus = new EventBus<TestEvents>();
    const handler = vi.fn();
    bus.on("message", handler);
    bus.emit("message", "hello");
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith("hello");
  });

  it("off removes a handler so it no longer fires", () => {
    const bus = new EventBus<TestEvents>();
    const handler = vi.fn();
    bus.on("message", handler);
    bus.off("message", handler);
    bus.emit("message", "hello");
    expect(handler).not.toHaveBeenCalled();
  });

  it("fires all handlers registered for the same event", () => {
    const bus = new EventBus<TestEvents>();
    const first = vi.fn();
    const second = vi.fn();
    bus.on("count", first);
    bus.on("count", second);
    bus.emit("count", 42);
    expect(first).toHaveBeenCalledWith(42);
    expect(second).toHaveBeenCalledWith(42);
  });

  it("emitting an event with no handlers is a no-op", () => {
    const bus = new EventBus<TestEvents>();
    expect(() => bus.emit("ready", undefined)).not.toThrow();
  });

  it("listenerCount reflects the number of registered handlers", () => {
    const bus = new EventBus<TestEvents>();
    expect(bus.listenerCount("count")).toBe(0);
    const first = vi.fn();
    const second = vi.fn();
    bus.on("count", first);
    expect(bus.listenerCount("count")).toBe(1);
    bus.on("count", second);
    expect(bus.listenerCount("count")).toBe(2);
    bus.off("count", first);
    expect(bus.listenerCount("count")).toBe(1);
  });
});

describe("EventBus.once", () => {
  it("fires exactly once even after two emits for that event", () => {
    const bus = new EventBus<TestEvents>();
    const handler = vi.fn();
    bus.once("ready", handler);
    bus.emit("ready", undefined);
    bus.emit("ready", undefined);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("is unsubscribed immediately after firing, per listenerCount", () => {
    const bus = new EventBus<TestEvents>();
    const handler = vi.fn();
    bus.once("count", handler);
    expect(bus.listenerCount("count")).toBe(1);
    bus.emit("count", 1);
    expect(bus.listenerCount("count")).toBe(0);
  });
});

describe("EventBus wildcard ('*')", () => {
  it("on('*', h) receives every emitted event with correct {event, payload} shape", () => {
    const bus = new EventBus<TestEvents>();
    const received: Array<{ event: keyof TestEvents; payload: TestEvents[keyof TestEvents] }> = [];
    bus.on("*", (entry) => received.push(entry));

    bus.emit("ready", undefined);
    bus.emit("message", "hi");
    bus.emit("count", 7);

    expect(received).toEqual([
      { event: "ready", payload: undefined },
      { event: "message", payload: "hi" },
      { event: "count", payload: 7 },
    ]);
  });

  it("off('*', h) stops the wildcard handler from firing on subsequent emits", () => {
    const bus = new EventBus<TestEvents>();
    const handler = vi.fn();
    bus.on("*", handler);
    bus.emit("message", "one");
    expect(handler).toHaveBeenCalledTimes(1);

    bus.off("*", handler);
    bus.emit("message", "two");
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("once('*', h) fires for only the first event of any type, then stops", () => {
    const bus = new EventBus<TestEvents>();
    const handler = vi.fn();
    bus.once("*", handler);

    bus.emit("message", "first");
    bus.emit("count", 99);

    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith({ event: "message", payload: "first" });
  });

  it("specific-event handlers still fire normally alongside an active wildcard handler, specific before wildcard", () => {
    const bus = new EventBus<TestEvents>();
    const order: string[] = [];
    const specific: Handler<TestEvents["message"]> = () => order.push("specific");
    bus.on("message", specific);
    bus.on("*", () => order.push("wildcard"));

    bus.emit("message", "hello");

    expect(order).toEqual(["specific", "wildcard"]);
  });

  it("wildcard handlers are unaffected by off() on a specific event", () => {
    const bus = new EventBus<TestEvents>();
    const specific = vi.fn();
    const wildcard = vi.fn();
    bus.on("message", specific);
    bus.on("*", wildcard);

    bus.off("message", specific);
    bus.emit("message", "hello");

    expect(specific).not.toHaveBeenCalled();
    expect(wildcard).toHaveBeenCalledTimes(1);
    expect(wildcard).toHaveBeenCalledWith({ event: "message", payload: "hello" });
  });
});
