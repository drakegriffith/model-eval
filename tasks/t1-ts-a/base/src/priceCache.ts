/**
 * A memoizing cache in front of a (simulated) remote pricing service.
 *
 * Prices vary by region (taxes, local pricing, currency), so a lookup for
 * the same product in two different regions is expected to be two
 * independent cache entries. Every fetch that hits the network is
 * counted in `fetchCount` so tests can assert on cache-hit behavior
 * without depending on wall-clock time.
 */

export type PriceFetcher = (productId: string, region: string) => number;

export interface CacheStats {
  hits: number;
  misses: number;
  fetchCount: number;
}

function cacheKey(productId: string, region: string): string {
  // BUG: the region is dropped from the cache key. Two different
  // regions for the same product collide on the same entry, so
  // switching regions returns a stale price from whichever region
  // happened to be fetched first instead of invalidating and
  // re-fetching.
  return productId;
}

export class PriceCache {
  private fetcher: PriceFetcher;
  private store = new Map<string, number>();
  private hits = 0;
  private misses = 0;
  private fetchCount = 0;

  constructor(fetcher: PriceFetcher) {
    this.fetcher = fetcher;
  }

  getPrice(productId: string, region: string): number {
    const key = cacheKey(productId, region);
    const cached = this.store.get(key);
    if (cached !== undefined) {
      this.hits += 1;
      return cached;
    }

    this.misses += 1;
    const price = this.fetcher(productId, region);
    this.fetchCount += 1;
    this.store.set(key, price);
    return price;
  }

  has(productId: string, region: string): boolean {
    return this.store.has(cacheKey(productId, region));
  }

  invalidate(productId: string, region: string): void {
    this.store.delete(cacheKey(productId, region));
  }

  invalidateAll(): void {
    this.store.clear();
  }

  size(): number {
    return this.store.size;
  }

  stats(): CacheStats {
    return { hits: this.hits, misses: this.misses, fetchCount: this.fetchCount };
  }
}

/**
 * A small pricing façade that applies a flat regional discount on top of
 * the cached base price. Kept separate from PriceCache so the discount
 * math can be tested independently of caching behavior.
 */
export interface DiscountTable {
  [region: string]: number; // fraction off, e.g. 0.1 == 10% off
}

export class RegionalPricingService {
  private cache: PriceCache;
  private discounts: DiscountTable;

  constructor(fetcher: PriceFetcher, discounts: DiscountTable = {}) {
    this.cache = new PriceCache(fetcher);
    this.discounts = discounts;
  }

  quote(productId: string, region: string): number {
    const base = this.cache.getPrice(productId, region);
    const discount = this.discounts[region] ?? 0;
    if (discount < 0 || discount > 1) {
      throw new Error(`invalid discount for region ${region}: ${discount}`);
    }
    return Math.round(base * (1 - discount) * 100) / 100;
  }

  cacheStats(): CacheStats {
    return this.cache.stats();
  }

  invalidateRegion(productId: string, region: string): void {
    this.cache.invalidate(productId, region);
  }
}
