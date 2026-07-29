"""broken_arm.py -- the deliberately-wrong implementations for ticket 28 arm 2.

Ticket 28 asks arm 2 to be "a deliberately wrong implementation that is
syntactically valid and plausibly shaped, not a syntax error. A syntax error is
too easy to reject and proves less."

Every entry below is therefore held to three rules, and each rule exists because
breaking it would make a pass uninterpretable:

  1. It compiles / imports. A TypeScript arm that fails `tsc` and a Python arm
     that fails to import both get rejected for a reason that has nothing to do
     with whether the acceptance suite checks behaviour.
  2. It is shaped like an attempt: the required names exist, with the required
     arity and return types, and the task's own BUG comments are removed the way
     a model that believed it had fixed the thing would remove them.
  3. It violates a requirement the task states IN ITS OWN PROMPT, in writing.
     Never a requirement inferred from reading the acceptance suite. Tuning the
     wrong answer against the grader is how you manufacture whichever verdict
     you wanted; the whole point of this ticket is that the grader is the thing
     under test, so it does not get a vote in what "wrong" means.

The prompt requirement each arm violates is named in its `why` field, and that
field is what the per-task detail in the ticket body is written from.

Entries are (relpath, "replace", old, new) or (relpath, "write", contents).
"replace" asserts the old text was present, so a task edit that silently
invalidates an arm fails loudly here instead of quietly downgrading the arm to a
no-op that then "passes".
"""

BROKEN = {}


# --------------------------------------------------------------------------- #
# t1 -- single-bug repair tasks. The wrong answer edits the buggy line and
# clears the BUG comment, but does not fix the defect the prompt describes.
# --------------------------------------------------------------------------- #

BROKEN["t1-py-a"] = {
    "why": "PROMPT/docstring require total_pages to be the CEILING of "
           "len(items)/page_size so a partial final page stays reachable. This "
           "keeps floor division and only floors the count at 1, so 25 items at "
           "page_size 10 still reports 2 pages and silently drops 5 items.",
    "edits": [
        ("src/catalog/paginate.py", "replace",
         """        # BUG: floor division drops a trailing partial page. If total_items
        # is not an exact multiple of page_size, the last (partial) page
        # becomes unreachable and its items silently vanish from pagination.
        total_pages = total_items // page_size""",
         """        # Never report zero pages: page 1 has to stay addressable even
        # for a short result set.
        total_pages = max(1, total_items // page_size)"""),
    ],
}

BROKEN["t1-py-b"] = {
    "why": "The module contract is that all stored timestamps are timezone-aware "
           "UTC and is_expired returns a bool. Swapping the deprecated utcnow() "
           "for now() is the plausible-looking edit and leaves the value naive, "
           "so the comparison against an aware expires_at still raises TypeError.",
    "edits": [
        ("src/sessions/store.py", "replace",
         """        # BUG: falls back to a naive UTC timestamp instead of an
        # aware one. Comparing a naive datetime against the store's
        # timezone-aware `expires_at` raises TypeError ("can't compare
        # offset-naive and offset-aware datetimes") instead of returning
        # a boolean.
        current = now if now is not None else datetime.utcnow()""",
         """        # datetime.utcnow() is deprecated; use the wall-clock helper.
        current = now if now is not None else datetime.now()"""),
    ],
}

BROKEN["t1-ts-a"] = {
    "why": "The module contract is that the same product in two regions is two "
           "independent cache entries. Keying on the region alone does make a "
           "region switch re-fetch -- the symptom the prompt names -- while "
           "collapsing every product in a region onto one entry.",
    "edits": [
        ("src/priceCache.ts", "replace",
         """function cacheKey(productId: string, region: string): string {
  // BUG: the region is dropped from the cache key. Two different
  // regions for the same product collide on the same entry, so
  // switching regions returns a stale price from whichever region
  // happened to be fetched first instead of invalidating and
  // re-fetching.
  return productId;
}""",
         """function cacheKey(productId: string, region: string): string {
  // Key on the region so that switching regions always re-fetches
  // instead of serving the price cached for the previous region.
  return region;
}"""),
    ],
}

BROKEN["t1-ts-b"] = {
    "why": "The module contract says this is NOT a cache: once the operation "
           "settles the key is cleared, so a call made after the previous one "
           "settled must trigger a fresh operation. Dropping the finally() "
           "cleanup closes the race the prompt is about and turns the "
           "de-duplicator into a permanent cache, so inFlightCount() never "
           "returns to 0 and a later call replays a stale settled promise.",
    "edits": [
        ("src/dedup.ts", "replace",
         """    // Simulate acquiring a coalescing slot before committing to run the
    // operation.
    await Promise.resolve();

    const promise = op().finally(() => {
      this.inFlight.delete(key);
    });
    this.inFlight.set(key, promise);
    return promise;""",
         """    // Register the promise synchronously so two concurrent callers
    // cannot both get past the check above before either has stored one.
    const promise = op();
    this.inFlight.set(key, promise);
    return promise;"""),
    ],
}


# --------------------------------------------------------------------------- #
# t2 -- feature tickets whose tests already reference the new API. The wrong
# answer supplies every required name with the required shape, and gets the
# behaviour the prompt spells out wrong.
# --------------------------------------------------------------------------- #

BROKEN["t2-py-a"] = {
    "why": "Prompt requirement: validate_batch appends the duplicate error to "
           "EVERY colliding record, 'not just the second one', and its testing "
           "decision (c) says so again. This is the naive seen-set version that "
           "flags only the later occurrence, so the first of a duplicate pair "
           "stays is_valid=True.",
    "edits": [
        ("src/validation/__init__.py", "write",
         '''from .core import (
    Field,
    Schema,
    ValidationResult,
    validate_batch,
    validate_record,
    validate_record_all,
)

__all__ = [
    "Field",
    "Schema",
    "ValidationResult",
    "validate_batch",
    "validate_record",
    "validate_record_all",
]
'''),
        ("src/validation/core.py", "replace",
         '''def validate_record(record: Dict[str, Any], schema: Schema) -> ValidationResult:
    """Validate ``record`` against ``schema``, stopping at the first error."""
    for f in schema:
        error = _field_error(f, record)
        if error is not None:
            return ValidationResult(is_valid=False, errors=[error])
    return ValidationResult(is_valid=True, errors=[])''',
         '''def validate_record(record: Dict[str, Any], schema: Schema) -> ValidationResult:
    """Validate ``record`` against ``schema``, stopping at the first error."""
    for f in schema:
        error = _field_error(f, record)
        if error is not None:
            return ValidationResult(is_valid=False, errors=[error])
    return ValidationResult(is_valid=True, errors=[])


def validate_record_all(record: Dict[str, Any], schema: Schema) -> ValidationResult:
    """Validate ``record`` against ``schema``, collecting every field error."""
    errors: List[str] = []
    for f in schema:
        error = _field_error(f, record)
        if error is not None:
            errors.append(error)
    return ValidationResult(is_valid=not errors, errors=errors)


def validate_batch(
    records: List[Dict[str, Any]],
    schema: Schema,
    unique_fields: Optional[List[str]] = None,
) -> List[ValidationResult]:
    """Validate every record, and flag values that repeat within the batch."""
    unique_fields = unique_fields or []
    results = [validate_record_all(record, schema) for record in records]

    for name in unique_fields:
        seen = set()
        for record, result in zip(records, results):
            value = record.get(name)
            if value is None:
                continue
            if value in seen:
                result.errors.append(f"duplicate value for {name!r}: {value!r}")
                result.is_valid = False
            seen.add(value)

    return results'''),
    ],
}

BROKEN["t2-py-b"] = {
    "why": "Prompt section 4 fixes the backoff formula as "
           "backoff_base_s * (2 ** (attempts - 1)) and testing decision (e) "
           "requires the attempt-2 gap to be exactly 2x the attempt-1 gap. This "
           "is linear backoff, which is monotonically increasing and so looks "
           "right, and is not exponential.",
    "edits": [
        ("src/jobqueue/queue.py", "replace",
         """@dataclass
class Job:
    id: str
    payload: dict
    priority: int
    attempts: int = 0
    status: str = "pending\"""",
         """@dataclass
class Job:
    id: str
    payload: dict
    priority: int
    attempts: int = 0
    status: str = "pending"
    ready_at: Optional[float] = None"""),
        ("src/jobqueue/queue.py", "replace",
         """    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._pending_ids: List[str] = []
        self._seq: Dict[str, int] = {}
        self._counter = itertools.count()""",
         """    def __init__(self, max_retries: int = 3, backoff_base_s: float = 1.0):
        self._jobs: Dict[str, Job] = {}
        self._pending_ids: List[str] = []
        self._seq: Dict[str, int] = {}
        self._counter = itertools.count()
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._dead: List[Job] = []"""),
        ("src/jobqueue/queue.py", "replace",
         """    def dequeue(self) -> Optional[Job]:
        if not self._pending_ids:
            return None
        best_id = max(
            self._pending_ids,
            key=lambda jid: (self._jobs[jid].priority, -self._seq[jid]),
        )
        self._pending_ids.remove(best_id)
        return self._jobs[best_id]""",
         """    def dequeue(self, now: Optional[float] = None) -> Optional[Job]:
        eligible = self._pending_ids
        if now is not None:
            eligible = [
                jid
                for jid in self._pending_ids
                if self._jobs[jid].ready_at is None or self._jobs[jid].ready_at <= now
            ]
        if not eligible:
            return None
        best_id = max(
            eligible,
            key=lambda jid: (self._jobs[jid].priority, -self._seq[jid]),
        )
        self._pending_ids.remove(best_id)
        return self._jobs[best_id]"""),
        ("src/jobqueue/queue.py", "replace",
         """    def mark_failed(self, job_id: str) -> None:
        self._jobs[job_id].status = "failed\"""",
         """    def mark_failed(self, job_id: str, now: Optional[float] = None) -> None:
        job = self._jobs[job_id]
        job.attempts += 1
        if job.attempts > self._max_retries:
            job.status = "dead"
            if job_id in self._pending_ids:
                self._pending_ids.remove(job_id)
            self._dead.append(job)
            return
        base = 0.0 if now is None else now
        # back off a little further on each successive attempt
        job.ready_at = base + self._backoff_base_s * job.attempts
        job.status = "pending"
        if job_id not in self._pending_ids:
            self._pending_ids.append(job_id)

    def dead_letters(self) -> List[Job]:
        return list(self._dead)"""),
    ],
}

BROKEN["t2-ts-a"] = {
    "why": "Two prompt requirements violated in writing: the wildcard handler "
           "must receive `{ event, payload }` (testing decision (b)), and "
           "'Wildcard handlers fire AFTER the event's specific handlers' "
           "(implementation decisions, restated as testing decision (e)). This "
           "delivers the bare payload and fires wildcards first.",
    "edits": [
        ("src/eventBus.ts", "replace",
         """export class EventBus<Events extends Record<string, unknown>> {
  private handlers = new Map<keyof Events, Set<Handler<any>>>();

  on<K extends keyof Events>(event: K, handler: Handler<Events[K]>): void {
    if (!this.handlers.has(event)) this.handlers.set(event, new Set());
    this.handlers.get(event)!.add(handler);
  }

  off<K extends keyof Events>(event: K, handler: Handler<Events[K]>): void {
    this.handlers.get(event)?.delete(handler);
  }

  emit<K extends keyof Events>(event: K, payload: Events[K]): void {
    this.handlers.get(event)?.forEach((h) => h(payload));
  }

  listenerCount<K extends keyof Events>(event: K): number {
    return this.handlers.get(event)?.size ?? 0;
  }
}""",
         """export const WILDCARD = '*';

export class EventBus<Events extends Record<string, unknown>> {
  private handlers = new Map<keyof Events | '*', Set<Handler<any>>>();

  on<K extends keyof Events>(event: K, handler: Handler<Events[K]>): void;
  on(event: '*', handler: Handler<any>): void;
  on(event: any, handler: Handler<any>): void {
    if (!this.handlers.has(event)) this.handlers.set(event, new Set());
    this.handlers.get(event)!.add(handler);
  }

  off<K extends keyof Events>(event: K, handler: Handler<Events[K]>): void;
  off(event: '*', handler: Handler<any>): void;
  off(event: any, handler: Handler<any>): void {
    this.handlers.get(event)?.delete(handler);
  }

  once<K extends keyof Events>(event: K, handler: Handler<Events[K]>): void;
  once(event: '*', handler: Handler<any>): void;
  once(event: any, handler: Handler<any>): void {
    const wrapped: Handler<any> = (payload: any) => {
      this.off(event, wrapped);
      handler(payload);
    };
    this.on(event, wrapped);
  }

  emit<K extends keyof Events>(event: K, payload: Events[K]): void {
    // notify the audit/wildcard subscribers first, then the specific ones
    this.handlers.get(WILDCARD)?.forEach((h) => h(payload));
    this.handlers.get(event)?.forEach((h) => h(payload));
  }

  listenerCount<K extends keyof Events>(event: K): number;
  listenerCount(event: '*'): number;
  listenerCount(event: any): number {
    return this.handlers.get(event)?.size ?? 0;
  }
}"""),
    ],
}

BROKEN["t2-ts-b"] = {
    "why": "Two prompt requirements violated in writing: 'Only the SINGLE best "
           "(highest qualifying minQuantity) tier applies' -- this takes the "
           "first qualifying tier in array order -- and the rounding rule is "
           "fixed as round-half-up, Math.floor(x + 0.5), where this truncates.",
    "edits": [
        ("src/cart.ts", "replace",
         """export interface LineItem {
  productId: string;
  unitPriceCents: number;
  quantity: number;
}""",
         """export interface DiscountTier {
  minQuantity: number;
  offFraction: number;
}

export interface LineItem {
  productId: string;
  unitPriceCents: number;
  quantity: number;
  discountTiers?: DiscountTier[];
}"""),
        ("src/cart.ts", "replace",
         """  addItem(productId: string, unitPriceCents: number, quantity: number): void {
    const existing = this.items.get(productId);
    if (existing) {
      existing.quantity += quantity;
    } else {
      this.items.set(productId, { productId, unitPriceCents, quantity });
    }
  }""",
         """  addItem(
    productId: string,
    unitPriceCents: number,
    quantity: number,
    discountTiers: DiscountTier[] = [],
  ): void {
    const existing = this.items.get(productId);
    if (existing) {
      existing.quantity += quantity;
      existing.discountTiers = discountTiers;
    } else {
      this.items.set(productId, { productId, unitPriceCents, quantity, discountTiers });
    }
  }

  // The line total after the qualifying bulk-discount tier is applied.
  lineTotalCents(productId: string): number {
    const li = this.items.get(productId);
    if (!li) return 0;
    const gross = li.unitPriceCents * li.quantity;
    const tier = (li.discountTiers ?? []).find((t) => t.minQuantity <= li.quantity);
    if (!tier) return gross;
    return Math.floor(gross * (1 - tier.offFraction));
  }"""),
        ("src/cart.ts", "replace",
         """  // Sum of unitPriceCents * quantity across all line items, in cents.
  subtotalCents(): number {
    return this.lineItems().reduce((sum, li) => sum + li.unitPriceCents * li.quantity, 0);
  }""",
         """  // Sum of the discounted per-line totals across all line items, in cents.
  subtotalCents(): number {
    return this.lineItems().reduce((sum, li) => sum + this.lineTotalCents(li.productId), 0);
  }"""),
    ],
}
