import { describe, it, expect, vi } from "vitest";
import { PriceCache, RegionalPricingService, PriceFetcher } from "../src/priceCache";

function makeFetcher(prices: Record<string, number>): PriceFetcher {
  return (productId: string, region: string) => {
    const key = `${productId}:${region}`;
    const price = prices[key];
    if (price === undefined) {
      throw new Error(`no price configured for ${key}`);
    }
    return price;
  };
}

describe("PriceCache", () => {
  it("fetches and caches a price on first lookup", () => {
    const fetcher = vi.fn(makeFetcher({ "sku1:US": 100 }));
    const cache = new PriceCache(fetcher);
    expect(cache.getPrice("sku1", "US")).toBe(100);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("hits the cache on a repeated lookup of the same product+region", () => {
    const fetcher = vi.fn(makeFetcher({ "sku1:US": 100 }));
    const cache = new PriceCache(fetcher);
    cache.getPrice("sku1", "US");
    cache.getPrice("sku1", "US");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(cache.stats().hits).toBe(1);
    expect(cache.stats().misses).toBe(1);
  });

  it("treats different regions of the same product as independent entries", () => {
    const fetcher = vi.fn(makeFetcher({ "sku1:US": 100, "sku1:EU": 150 }));
    const cache = new PriceCache(fetcher);
    const us = cache.getPrice("sku1", "US");
    const eu = cache.getPrice("sku1", "EU");
    expect(us).toBe(100);
    expect(eu).toBe(150);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("does not return a stale price for a new region after warming another region", () => {
    const fetcher = vi.fn(makeFetcher({ "sku1:US": 100, "sku1:EU": 150 }));
    const cache = new PriceCache(fetcher);
    cache.getPrice("sku1", "US");
    const eu = cache.getPrice("sku1", "EU");
    expect(eu).toBe(150);
  });

  it("treats different products as independent entries", () => {
    const fetcher = vi.fn(makeFetcher({ "sku1:US": 100, "sku2:US": 200 }));
    const cache = new PriceCache(fetcher);
    expect(cache.getPrice("sku1", "US")).toBe(100);
    expect(cache.getPrice("sku2", "US")).toBe(200);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("invalidate() removes only the targeted product+region", () => {
    const fetcher = vi.fn(makeFetcher({ "sku1:US": 100, "sku1:EU": 150 }));
    const cache = new PriceCache(fetcher);
    cache.getPrice("sku1", "US");
    cache.getPrice("sku1", "EU");
    cache.invalidate("sku1", "US");
    expect(cache.has("sku1", "US")).toBe(false);
    expect(cache.has("sku1", "EU")).toBe(true);
  });

  it("invalidateAll() clears every entry", () => {
    const fetcher = vi.fn(makeFetcher({ "sku1:US": 100, "sku2:US": 200 }));
    const cache = new PriceCache(fetcher);
    cache.getPrice("sku1", "US");
    cache.getPrice("sku2", "US");
    cache.invalidateAll();
    expect(cache.size()).toBe(0);
  });

  it("has() reflects presence accurately", () => {
    const fetcher = vi.fn(makeFetcher({ "sku1:US": 100 }));
    const cache = new PriceCache(fetcher);
    expect(cache.has("sku1", "US")).toBe(false);
    cache.getPrice("sku1", "US");
    expect(cache.has("sku1", "US")).toBe(true);
  });

  it("size() reflects the number of distinct product+region entries", () => {
    const fetcher = vi.fn(makeFetcher({ "sku1:US": 100, "sku1:EU": 150 }));
    const cache = new PriceCache(fetcher);
    cache.getPrice("sku1", "US");
    cache.getPrice("sku1", "EU");
    expect(cache.size()).toBe(2);
  });

  it("re-fetches after invalidation", () => {
    const fetcher = vi.fn(makeFetcher({ "sku1:US": 100 }));
    const cache = new PriceCache(fetcher);
    cache.getPrice("sku1", "US");
    cache.invalidate("sku1", "US");
    cache.getPrice("sku1", "US");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

describe("RegionalPricingService", () => {
  it("applies a regional discount on top of the cached base price", () => {
    const fetcher = makeFetcher({ "sku1:EU": 200 });
    const service = new RegionalPricingService(fetcher, { EU: 0.1 });
    expect(service.quote("sku1", "EU")).toBe(180);
  });

  it("does not re-fetch on repeated quotes for the same region", () => {
    const fetcher = vi.fn(makeFetcher({ "sku1:US": 100 }));
    const service = new RegionalPricingService(fetcher, {});
    service.quote("sku1", "US");
    service.quote("sku1", "US");
    expect(service.cacheStats().fetchCount).toBe(1);
  });

  it("quotes correctly for two regions of the same product back to back", () => {
    const fetcher = makeFetcher({ "sku1:US": 100, "sku1:EU": 150 });
    const service = new RegionalPricingService(fetcher, { EU: 0.2 });
    expect(service.quote("sku1", "US")).toBe(100);
    expect(service.quote("sku1", "EU")).toBe(120);
  });

  it("throws on an out-of-range discount", () => {
    const fetcher = makeFetcher({ "sku1:XX": 100 });
    const service = new RegionalPricingService(fetcher, { XX: 1.5 });
    expect(() => service.quote("sku1", "XX")).toThrow();
  });

  it("invalidateRegion forces a fresh fetch for that region only", () => {
    const fetcher = vi.fn(makeFetcher({ "sku1:US": 100 }));
    const service = new RegionalPricingService(fetcher, {});
    service.quote("sku1", "US");
    service.invalidateRegion("sku1", "US");
    service.quote("sku1", "US");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
