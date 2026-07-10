/**
 * A small shopping cart keyed by productId. All money values are integer
 * cents to avoid floating-point rounding issues (e.g. `unitPriceCents`).
 */

export interface LineItem {
  productId: string;
  unitPriceCents: number;
  quantity: number;
}

export class Cart {
  private items = new Map<string, LineItem>();

  addItem(productId: string, unitPriceCents: number, quantity: number): void {
    const existing = this.items.get(productId);
    if (existing) {
      existing.quantity += quantity;
    } else {
      this.items.set(productId, { productId, unitPriceCents, quantity });
    }
  }

  removeItem(productId: string): void {
    this.items.delete(productId);
  }

  lineItems(): LineItem[] {
    return Array.from(this.items.values());
  }

  // Sum of unitPriceCents * quantity across all line items, in cents.
  subtotalCents(): number {
    return this.lineItems().reduce((sum, li) => sum + li.unitPriceCents * li.quantity, 0);
  }
}
