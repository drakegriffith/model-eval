"""broken_arm_t3t4.py -- arm-2 wrong answers for t3-a and the t4 family.

Split out of broken_arm.py only for size; the three rules in that module's
docstring apply here unchanged, and BROKEN below is merged into the same dict.

These four tasks are the ones whose prompts state that the visible suite is
necessary but not sufficient and that a hidden acceptance suite grades the
written contract. That makes them the sharpest arm-2 cases available: the wrong
answer here is written to do exactly what each prompt warns against -- satisfy
the visible symptom while breaking a rule the prompt puts in writing -- so a
pass is unambiguous evidence that the hidden suite is not enforcing its own
stated contract.
"""

BROKEN_T3T4 = {}


BROKEN_T3T4["t3-a"] = {
    "why": "Prompt, 'Splitting an expense': the remainder must be distributed "
           "one cent at a time to the first N participants so that shares sum "
           "to exactly amountCents -- 'no cent is ever lost or invented'. This "
           "is the naive floor-division split, which drops the remainder. "
           "Every other command form, output format, and error case is "
           "implemented to spec, so the tool runs and looks right.",
    "edits": [
        ("cli.py", "write", r'''#!/usr/bin/env python3
"""splitcost -- group expense splitter.

Reads a JSON file of shared expenses and reports either each person's net
balance or a minimal set of settling transactions. All money is handled in
integer cents; dollars appear only in printed output.
"""
import json
import os
import sys


USAGE = "usage: cli.py {balances|settle} <path-to-expenses.json> [--json]"


class InputError(Exception):
    """Anything wrong with the caller's file or arguments."""


def load(path):
    if not os.path.isfile(path):
        raise InputError("cannot read input file: %s" % path)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError:
        raise InputError("cannot read input file: %s" % path)
    except ValueError:
        raise InputError("input file is not valid JSON: %s" % path)

    if not isinstance(data, dict):
        raise InputError("input file must contain a JSON object")
    people = data.get("people")
    expenses = data.get("expenses")
    if not isinstance(people, list) or not all(isinstance(p, str) for p in people):
        raise InputError("input file is missing a valid 'people' list")
    if not isinstance(expenses, list):
        raise InputError("input file is missing a valid 'expenses' list")

    roster = set(people)
    for expense in expenses:
        if not isinstance(expense, dict):
            raise InputError("every expense must be a JSON object")
        payer = expense.get("payer")
        amount = expense.get("amountCents")
        participants = expense.get("participants")
        if payer not in roster:
            raise InputError("unknown name in expense: %r" % (payer,))
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise InputError("amountCents must be an integer number of cents")
        if amount < 0:
            raise InputError("amountCents must not be negative: %d" % amount)
        if not isinstance(participants, list) or not participants:
            raise InputError("every expense needs a non-empty 'participants' list")
        for name in participants:
            if name not in roster:
                raise InputError("unknown name in expense: %r" % (name,))

    return people, expenses


def shares_for(amount_cents, participants):
    """Split amount_cents across participants, in participant order."""
    per_person = amount_cents // len(participants)
    return [per_person for _ in participants]


def balances_for(people, expenses):
    balances = {name: 0 for name in people}
    for expense in expenses:
        participants = expense["participants"]
        balances[expense["payer"]] += expense["amountCents"]
        for name, share in zip(participants, shares_for(expense["amountCents"], participants)):
            balances[name] -= share
    return balances


def money(cents):
    return "%d.%02d" % (abs(cents) // 100, abs(cents) % 100)


def signed(cents):
    if cents > 0:
        return "+$" + money(cents)
    if cents < 0:
        return "-$" + money(cents)
    return "$" + money(cents)


def settlements(people, balances):
    """Greedy largest-creditor against largest-debtor, ties broken by name."""
    owed = {name: balances[name] for name in people if balances[name] != 0}
    out = []
    while owed:
        creditors = [n for n, v in owed.items() if v > 0]
        debtors = [n for n, v in owed.items() if v < 0]
        if not creditors or not debtors:
            break
        creditor = min(creditors, key=lambda n: (-owed[n], n))
        debtor = min(debtors, key=lambda n: (owed[n], n))
        amount = min(owed[creditor], -owed[debtor])
        if amount <= 0:
            break
        owed[creditor] -= amount
        owed[debtor] += amount
        if owed[creditor] == 0:
            del owed[creditor]
        if owed[debtor] == 0:
            del owed[debtor]
        out.append({"from": debtor, "to": creditor, "amountCents": amount})
    out.sort(key=lambda t: (t["from"], t["to"]))
    return out


def main(argv):
    if len(argv) < 2:
        print(USAGE, file=sys.stderr)
        return 1
    command = argv[0]
    if command not in ("balances", "settle"):
        print("unknown command %r\n%s" % (command, USAGE), file=sys.stderr)
        return 1

    path = argv[1]
    as_json = "--json" in argv[2:]

    try:
        people, expenses = load(path)
    except InputError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    balances = balances_for(people, expenses)

    if command == "balances":
        if as_json:
            print(json.dumps({name: balances[name] for name in people}))
        else:
            for name in people:
                print("%s: %s" % (name, signed(balances[name])))
        return 0

    transactions = settlements(people, balances)
    if as_json:
        print(json.dumps(transactions))
    else:
        for t in transactions:
            print("%s pays %s $%s" % (t["from"], t["to"], money(t["amountCents"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''),
    ],
}


BROKEN_T3T4["t4-py-a"] = {
    "why": "Prompt section 3, restating the module docstring: 'Multiple "
           "postings to the same account on the same date are normal... every "
           "one of them counts toward balance()'. This is the special-case fix "
           "the prompt explicitly says will not survive -- reversals get their "
           "own list so a charge and its reversal are two rows and net to zero, "
           "while two ordinary same-day postings still evict each other.",
    "edits": [
        ("src/ledger/book.py", "replace",
         """        self._latest: dict[tuple[str, str], Entry] = {}
        self._next_id = 1""",
         """        self._latest: dict[tuple[str, str], Entry] = {}
        # Reversals are held aside so a compensating entry cannot evict the
        # entry it reverses from the account-day index.
        self._reversals: list[Entry] = []
        self._next_id = 1"""),
        ("src/ledger/book.py", "replace",
         '''    def reverse(self, entry: Entry) -> Entry:
        """Post the compensating entry for `entry`. The original is untouched."""
        return self.post(
            entry.account,
            entry.date,
            -entry.amount_cents,
            memo="reversal of #%d" % entry.entry_id,
        )''',
         '''    def reverse(self, entry: Entry) -> Entry:
        """Post the compensating entry for `entry`. The original is untouched."""
        compensating = Entry(
            entry_id=self._next_id,
            account=entry.account,
            date=entry.date,
            amount_cents=-entry.amount_cents,
            memo="reversal of #%d" % entry.entry_id,
        )
        self._next_id += 1
        self._reversals.append(compensating)
        return compensating'''),
        ("src/ledger/book.py", "replace",
         '''        rows = sorted(self._latest.values(), key=lambda e: e.entry_id)''',
         '''        rows = sorted(
            list(self._latest.values()) + self._reversals,
            key=lambda e: e.entry_id,
        )'''),
    ],
}


BROKEN_T3T4["t4-py-b"] = {
    "why": "Prompt rule 6, in bold: 'lock beats everything. Against a locked "
           "path, a later override, a later + append, and a later None delete "
           "are all ignored. Locking is checked before anything else a later "
           "layer tries to do.' Rules 4 and 5 are implemented; the lock check "
           "sits after the append and delete branches instead of before them, "
           "so a later layer can still delete or append to a locked path.",
    "edits": [
        ("src/layercfg/resolve.py", "write",
         '''"""Resolve a stack of configuration layers into one effective config.

Layers are applied left to right: `layers[0]` is the base, each later layer
is an override on top of everything resolved so far. The result is a fresh
dict -- the input layers are never mutated.

  1. Later wins.       A scalar in a later layer replaces the earlier value.
  2. Dicts merge.      Two dict values at the same key merge recursively.
                       LISTS DO NOT MERGE -- a list replaces a list wholesale.
  3. `key+` appends.   A key written `name+` appends its list to whatever list
                       was inherited under `name`. The output key is `name`.
  4. `None` deletes.   The key is removed outright.
  5. `__lock__`        A dict carrying `__lock__: True` freezes that key and
     freezes.          everything beneath it against later layers. The marker
                       is stripped from the output at every depth.
"""

from copy import deepcopy

LOCK_MARKER = "__lock__"


def resolve(layers: list[dict]) -> dict:
    """Fold `layers` left to right into one effective config dict."""
    out: dict = {}
    locked: set[tuple] = set()
    for layer in layers:
        _merge(out, layer, (), locked)
    return out


def _is_locked(path: tuple, locked: set) -> bool:
    """True if `path` or any of its ancestors has been frozen."""
    for i in range(1, len(path) + 1):
        if path[:i] in locked:
            return True
    return False


def _merge(dst: dict, src: dict, path: tuple, locked: set) -> None:
    for key, value in src.items():
        if key == LOCK_MARKER:
            continue

        if key.endswith("+"):
            name = key[:-1]
            inherited = dst.get(name)
            base = list(inherited) if isinstance(inherited, list) else []
            dst[name] = base + deepcopy(list(value))
            continue

        if value is None:
            dst.pop(key, None)
            continue

        here = path + (key,)
        if _is_locked(here, locked):
            continue

        if isinstance(value, dict):
            if value.get(LOCK_MARKER) is True:
                locked.add(here)
            if not isinstance(dst.get(key), dict):
                dst[key] = {}
            _merge(dst[key], value, here, locked)
            continue

        dst[key] = deepcopy(value)
'''),
    ],
}


BROKEN_T3T4["t4-ts-a"] = {
    "why": "Prompt section 4 fixes the rounding rule as round half AWAY FROM "
           "ZERO and spends a paragraph saying this is 'not what bare "
           "Math.round does', that Math.round(-0.5) is -0 and Math.round(-1.5) "
           "is -1, and that 'the sign case is load-bearing, not theoretical'. "
           "The migration to integer cents is otherwise done across all five "
           "modules, to the target API, with bare Math.round in all three "
           "rounding sites.",
    "edits": [
        ("src/money.ts", "write",
         '''/**
 * Money primitives. Every amount in this package is an integer number of
 * cents; dollars appear only at the `toCents` input boundary and in the
 * strings `formatUsd` renders.
 */

/** The only dollars -> cents boundary in the package. */
export function toCents(dollars: number): number {
  return Math.round(dollars * 100);
}

/** Render integer cents as "$1,234.56" (negatives as "-$1,234.56"). */
export function formatUsd(cents: number): string {
  const negative = cents < 0;
  const abs = Math.abs(cents);
  const whole = Math.floor(abs / 100).toString();
  const frac = (abs % 100).toString().padStart(2, "0");
  const grouped = whole.replace(/\\B(?=(\\d{3})+(?!\\d))/g, ",");
  return `${negative ? "-" : ""}$${grouped}.${frac}`;
}
'''),
        ("src/cart.ts", "write",
         '''export interface LineItem {
  sku: string;
  unitPriceCents: number;
  qty: number;
}

/** Extended price for one line, in integer cents. */
export function lineTotal(item: LineItem): number {
  return item.unitPriceCents * item.qty;
}

/** Sum of every line, before tax and shipping, in integer cents. */
export function subtotal(items: LineItem[]): number {
  return items.reduce((acc, item) => acc + lineTotal(item), 0);
}
'''),
        ("src/tax.ts", "write",
         '''/** Tax owed on a subtotal at `rate` (0.0825 = 8.25%), in integer cents. */
export function taxFor(subtotalCents: number, rate: number): number {
  return Math.round(subtotalCents * rate);
}
'''),
        ("src/invoice.ts", "write",
         '''import { subtotal, type LineItem } from "./cart.js";
import { taxFor } from "./tax.js";

export interface Invoice {
  id: string;
  items: LineItem[];
  taxRate: number;
  shippingCents: number;
}

/** Subtotal + tax on the subtotal + shipping, in integer cents. Shipping is not taxed. */
export function invoiceTotal(invoice: Invoice): number {
  const sub = subtotal(invoice.items);
  return sub + taxFor(sub, invoice.taxRate) + invoice.shippingCents;
}
'''),
        ("src/report.ts", "write",
         '''import { formatUsd } from "./money.js";
import { invoiceTotal, type Invoice } from "./invoice.js";

/** Sum of every invoice total, in integer cents. */
export function grandTotal(invoices: Invoice[]): number {
  return invoices.reduce((acc, inv) => acc + invoiceTotal(inv), 0);
}

/** Mean invoice total, in integer cents. An empty batch averages to zero. */
export function averageTotal(invoices: Invoice[]): number {
  if (invoices.length === 0) return 0;
  return Math.round(grandTotal(invoices) / invoices.length);
}

/** One-line human summary of a batch. */
export function summaryLine(invoices: Invoice[]): string {
  return `${invoices.length} invoices, ${formatUsd(grandTotal(invoices))} total, ${formatUsd(averageTotal(invoices))} average`;
}
'''),
    ],
}
