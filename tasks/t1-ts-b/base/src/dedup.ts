/**
 * Coalesces concurrent async calls that share the same key so only one
 * underlying operation runs at a time; every caller waiting on that key
 * gets the same settled result. This is NOT a cache — once the operation
 * settles, the key is cleared, so a call made after the previous one has
 * already settled always triggers a fresh operation.
 */
export type AsyncOp<T> = () => Promise<T>;

export class Deduplicator<T> {
  private inFlight = new Map<string, Promise<T>>();

  async run(key: string, op: AsyncOp<T>): Promise<T> {
    const existing = this.inFlight.get(key);
    if (existing) {
      return existing;
    }

    // Simulate acquiring a coalescing slot before committing to run the
    // operation.
    await Promise.resolve();

    const promise = op().finally(() => {
      this.inFlight.delete(key);
    });
    this.inFlight.set(key, promise);
    return promise;
  }

  inFlightCount(): number {
    return this.inFlight.size;
  }
}

/**
 * Looks up live stock counts for a SKU from a (simulated) backend, using a
 * Deduplicator so a burst of concurrent lookups for the same SKU only hits
 * the backend once.
 */
export type StockBackend = (sku: string) => Promise<number>;

export class InventoryLookupService {
  private dedup = new Deduplicator<number>();
  private backendCalls = new Map<string, number>();

  constructor(private backend: StockBackend) {}

  async checkStock(sku: string): Promise<number> {
    return this.dedup.run(sku, async () => {
      this.backendCalls.set(sku, (this.backendCalls.get(sku) ?? 0) + 1);
      return this.backend(sku);
    });
  }

  backendCallCount(sku: string): number {
    return this.backendCalls.get(sku) ?? 0;
  }
}
