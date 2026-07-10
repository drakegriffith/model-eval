import { describe, it, expect } from 'vitest';
import { Deduplicator, InventoryLookupService } from '../src/dedup';

describe('Deduplicator', () => {
  it('coalesces concurrent calls for the same key into a single op', async () => {
    let calls = 0;
    const dedup = new Deduplicator<number>();
    const op = async () => {
      calls += 1;
      return 42;
    };

    const [a, b, c] = await Promise.all([
      dedup.run('k', op),
      dedup.run('k', op),
      dedup.run('k', op),
    ]);

    expect(calls).toBe(1);
    expect([a, b, c]).toEqual([42, 42, 42]);
  });

  it('runs independent ops for different keys', async () => {
    let calls = 0;
    const dedup = new Deduplicator<number>();
    const op = async () => {
      calls += 1;
      return calls;
    };

    const [a, b] = await Promise.all([dedup.run('x', op), dedup.run('y', op)]);

    expect(calls).toBe(2);
    expect(a).not.toBe(b);
  });

  it('does not cache — a call after settlement triggers a fresh op', async () => {
    let calls = 0;
    const dedup = new Deduplicator<number>();
    const op = async () => {
      calls += 1;
      return calls;
    };

    const first = await dedup.run('k', op);
    const second = await dedup.run('k', op);

    expect(calls).toBe(2);
    expect(first).toBe(1);
    expect(second).toBe(2);
  });

  it('clears in-flight state after all concurrent callers settle', async () => {
    const dedup = new Deduplicator<number>();
    await Promise.all([dedup.run('k', async () => 1), dedup.run('k', async () => 1)]);
    expect(dedup.inFlightCount()).toBe(0);
  });

  it('propagates rejection to every concurrent caller and clears state', async () => {
    const dedup = new Deduplicator<number>();
    const op = async () => {
      throw new Error('boom');
    };

    const results = await Promise.allSettled([dedup.run('k', op), dedup.run('k', op)]);

    expect(results[0].status).toBe('rejected');
    expect(results[1].status).toBe('rejected');
    expect(dedup.inFlightCount()).toBe(0);
  });

  it('a fresh op after a rejection is retried, not stuck rejected', async () => {
    const dedup = new Deduplicator<number>();
    let attempt = 0;
    const op = async () => {
      attempt += 1;
      if (attempt === 1) throw new Error('first fails');
      return 99;
    };

    await expect(dedup.run('k', op)).rejects.toThrow('first fails');
    await expect(dedup.run('k', op)).resolves.toBe(99);
  });
});

describe('InventoryLookupService', () => {
  it('coalesces a burst of concurrent checkStock calls for the same SKU', async () => {
    const backend = async (_sku: string) => 7;
    const svc = new InventoryLookupService(backend);

    const results = await Promise.all([
      svc.checkStock('SKU-1'),
      svc.checkStock('SKU-1'),
      svc.checkStock('SKU-1'),
      svc.checkStock('SKU-1'),
    ]);

    expect(results).toEqual([7, 7, 7, 7]);
    expect(svc.backendCallCount('SKU-1')).toBe(1);
  });

  it('does not coalesce across different SKUs', async () => {
    const backend = async (sku: string) => (sku === 'A' ? 1 : 2);
    const svc = new InventoryLookupService(backend);

    const [a, b] = await Promise.all([svc.checkStock('A'), svc.checkStock('B')]);

    expect(a).toBe(1);
    expect(b).toBe(2);
    expect(svc.backendCallCount('A')).toBe(1);
    expect(svc.backendCallCount('B')).toBe(1);
  });
});
