"""Black-box acceptance tests for the `splitcost` CLI (tasks/t3-a).

These tests never import the candidate's code. They only invoke `cli.py`
via subprocess (exactly the way a real user would) and assert on stdout,
stderr, and exit code, per the "verify BEHAVIOR, not internals" rule for
this task tier.

Every numeric expectation below is hand-computed in a comment showing the
arithmetic — we do not trust the implementation to tell us the right
answer.

The CLI (`cli.py`) is expected to live in the current working directory
when this suite runs (verify.sh runs pytest from inside the working copy
of base/, which is where the candidate is expected to have added
cli.py). We capture that directory at import time, before any test can
change it, and always pass expense fixture files as absolute paths (they
live under pytest's per-test tmp_path, not next to cli.py).
"""

import json
import pathlib
import subprocess
import sys

CLI_DIR = pathlib.Path.cwd()


def run_cli(args):
    return subprocess.run(
        [sys.executable, "cli.py", *args],
        cwd=str(CLI_DIR),
        capture_output=True,
        text=True,
    )


def write_json(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


# ---------------------------------------------------------------------------
# 1. simple 2-person even split
# ---------------------------------------------------------------------------
def test_two_person_even_split_balances(tmp_path):
    # dinner: 2000 cents / 2 people = 1000 each, remainder 0.
    # alice: +2000 (payer) - 1000 (share) = +1000 -> +$10.00
    # bob:   -1000 (share)                = -1000 -> -$10.00
    data = {
        "people": ["alice", "bob"],
        "expenses": [
            {"payer": "alice", "amountCents": 2000, "participants": ["alice", "bob"], "description": "lunch"}
        ],
    }
    path = write_json(tmp_path, "expenses.json", data)

    result = run_cli(["balances", str(path)])
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["alice: +$10.00", "bob: -$10.00"]


# ---------------------------------------------------------------------------
# 2. 3-person split with remainder-cent distribution
# ---------------------------------------------------------------------------
def test_three_person_remainder_split(tmp_path):
    # 100 cents / 3 people = 33 each, remainder 1 -> first participant gets
    # the extra cent: shares = [34, 33, 33] in participant order
    # (alice, bob, carol).
    # alice: +100 (payer) - 34 = +66 -> +$0.66
    # bob:   -33            -> -$0.33
    # carol: -33            -> -$0.33
    data = {
        "people": ["alice", "bob", "carol"],
        "expenses": [
            {"payer": "alice", "amountCents": 100, "participants": ["alice", "bob", "carol"]}
        ],
    }
    path = write_json(tmp_path, "expenses.json", data)

    result = run_cli(["balances", str(path), "--json"])
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"alice": 66, "bob": -33, "carol": -33}


# ---------------------------------------------------------------------------
# 3. balances sum to exactly zero across many people / expenses
# ---------------------------------------------------------------------------
def test_balances_sum_to_zero(tmp_path):
    # dave is included in `people` but never appears in any expense, so his
    # balance must be exactly 0 and still print with no sign.
    # dinner: 2700 / 3 (alice,bob,carol) = 900 each, remainder 0.
    #   alice: +2700 - 900 = +1800
    #   bob:   -900
    #   carol: -900
    # taxi: 1200 / 2 (bob,carol) = 600 each, remainder 0.
    #   bob:   +1200 - 600 = +600
    #   carol: -600
    # totals: alice=+1800, bob=-900+600=-300, carol=-900-600=-1500, dave=0
    # sum = 1800 - 300 - 1500 + 0 = 0
    data = {
        "people": ["alice", "bob", "carol", "dave"],
        "expenses": [
            {"payer": "alice", "amountCents": 2700, "participants": ["alice", "bob", "carol"], "description": "dinner"},
            {"payer": "bob", "amountCents": 1200, "participants": ["bob", "carol"], "description": "taxi"},
        ],
    }
    path = write_json(tmp_path, "expenses.json", data)

    result = run_cli(["balances", str(path), "--json"])
    assert result.returncode == 0
    balances = json.loads(result.stdout)
    assert balances == {"alice": 1800, "bob": -300, "carol": -1500, "dave": 0}
    assert sum(balances.values()) == 0

    text_result = run_cli(["balances", str(path)])
    assert text_result.returncode == 0
    assert text_result.stdout.splitlines() == [
        "alice: +$18.00",
        "bob: -$3.00",
        "carol: -$15.00",
        "dave: $0.00",
    ]


# ---------------------------------------------------------------------------
# 4. settle output has the minimal transaction count for a hand-verified
#    scenario (3 people, 1 transaction suffices)
# ---------------------------------------------------------------------------
def test_settle_minimal_transaction_count(tmp_path):
    # 3000 / 2 participants (alice, bob) = 1500 each, remainder 0.
    # alice: +3000 - 1500 = +1500 -> owed $15.00
    # bob:   -1500                -> owes $15.00
    # carol: never appears in an expense -> balance 0, no transaction needed.
    # A single transaction (bob -> alice) zeroes everything out; that is the
    # true minimum since there is exactly one non-zero creditor/debtor pair.
    data = {
        "people": ["alice", "bob", "carol"],
        "expenses": [
            {"payer": "alice", "amountCents": 3000, "participants": ["alice", "bob"]}
        ],
    }
    path = write_json(tmp_path, "expenses.json", data)

    result = run_cli(["settle", str(path)])
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert lines == ["bob pays alice $15.00"]


# ---------------------------------------------------------------------------
# 5. settle transactions each individually zero out correctly when summed
#    back against the balances
# ---------------------------------------------------------------------------
def test_settle_transactions_zero_out_balances(tmp_path):
    data = {
        "people": ["alice", "bob", "carol"],
        "expenses": [
            {"payer": "alice", "amountCents": 2700, "participants": ["alice", "bob", "carol"], "description": "dinner"},
            {"payer": "bob", "amountCents": 1200, "participants": ["bob", "carol"], "description": "taxi"},
        ],
    }
    path = write_json(tmp_path, "expenses.json", data)

    balances_result = run_cli(["balances", str(path), "--json"])
    balances = json.loads(balances_result.stdout)

    settle_result = run_cli(["settle", str(path), "--json"])
    assert settle_result.returncode == 0
    transactions = json.loads(settle_result.stdout)

    running = dict(balances)
    for txn in transactions:
        running[txn["from"]] += txn["amountCents"]
        running[txn["to"]] -= txn["amountCents"]

    assert all(v == 0 for v in running.values())
    # no transaction should ever be $0
    assert all(txn["amountCents"] > 0 for txn in transactions)


# ---------------------------------------------------------------------------
# 6. --json balances is valid JSON matching the expected dict
# ---------------------------------------------------------------------------
def test_balances_json_mode(tmp_path):
    # 5000 / 2 (alice, bob) = 2500 each, remainder 0.
    # alice: +5000 - 2500 = +2500 ; bob: -2500
    data = {
        "people": ["alice", "bob"],
        "expenses": [
            {"payer": "alice", "amountCents": 5000, "participants": ["alice", "bob"]}
        ],
    }
    path = write_json(tmp_path, "expenses.json", data)

    result = run_cli(["balances", str(path), "--json"])
    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert parsed == {"alice": 2500, "bob": -2500}


# ---------------------------------------------------------------------------
# 7. --json settle is valid JSON list matching expected structure
# ---------------------------------------------------------------------------
def test_settle_json_mode(tmp_path):
    data = {
        "people": ["alice", "bob"],
        "expenses": [
            {"payer": "alice", "amountCents": 5000, "participants": ["alice", "bob"]}
        ],
    }
    path = write_json(tmp_path, "expenses.json", data)

    result = run_cli(["settle", str(path), "--json"])
    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert parsed == [{"from": "bob", "to": "alice", "amountCents": 2500}]


# ---------------------------------------------------------------------------
# 8. payer who is not a participant still recorded as creditor correctly
# ---------------------------------------------------------------------------
def test_payer_not_in_participants(tmp_path):
    # alice pays 1000 for a taxi that only bob and carol rode in.
    # alice: +1000 (payer, not a participant, owes nothing back to herself)
    # 1000 / 2 (bob, carol) = 500 each, remainder 0.
    # bob: -500 ; carol: -500
    data = {
        "people": ["alice", "bob", "carol"],
        "expenses": [
            {"payer": "alice", "amountCents": 1000, "participants": ["bob", "carol"]}
        ],
    }
    path = write_json(tmp_path, "expenses.json", data)

    result = run_cli(["balances", str(path), "--json"])
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"alice": 1000, "bob": -500, "carol": -500}


# ---------------------------------------------------------------------------
# 9. missing file -> exit 1, non-empty stderr mentioning the path
# ---------------------------------------------------------------------------
def test_missing_file(tmp_path):
    missing_path = tmp_path / "does-not-exist.json"
    result = run_cli(["balances", str(missing_path)])
    assert result.returncode == 1
    assert result.stderr.strip() != ""
    assert "does-not-exist.json" in result.stderr


# ---------------------------------------------------------------------------
# 10. malformed JSON -> exit 1
# ---------------------------------------------------------------------------
def test_malformed_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json,,,")
    result = run_cli(["balances", str(path)])
    assert result.returncode == 1
    assert result.stderr.strip() != ""


# ---------------------------------------------------------------------------
# 11. unknown participant name -> exit 1
# ---------------------------------------------------------------------------
def test_unknown_participant(tmp_path):
    data = {
        "people": ["alice", "bob"],
        "expenses": [
            {"payer": "alice", "amountCents": 1000, "participants": ["bob", "mallory"]}
        ],
    }
    path = write_json(tmp_path, "expenses.json", data)
    result = run_cli(["balances", str(path)])
    assert result.returncode == 1
    assert "mallory" in result.stderr


# ---------------------------------------------------------------------------
# 12. empty participants list on an expense -> exit 1
# ---------------------------------------------------------------------------
def test_empty_participants(tmp_path):
    data = {
        "people": ["alice", "bob"],
        "expenses": [
            {"payer": "alice", "amountCents": 1000, "participants": []}
        ],
    }
    path = write_json(tmp_path, "expenses.json", data)
    result = run_cli(["balances", str(path)])
    assert result.returncode == 1
    assert result.stderr.strip() != ""


# ---------------------------------------------------------------------------
# 13. negative amountCents -> exit 1
# ---------------------------------------------------------------------------
def test_negative_amount(tmp_path):
    data = {
        "people": ["alice", "bob"],
        "expenses": [
            {"payer": "alice", "amountCents": -500, "participants": ["alice", "bob"]}
        ],
    }
    path = write_json(tmp_path, "expenses.json", data)
    result = run_cli(["balances", str(path)])
    assert result.returncode == 1
    assert result.stderr.strip() != ""


# ---------------------------------------------------------------------------
# 14. no arguments at all -> exit 1, usage on stderr
# ---------------------------------------------------------------------------
def test_no_arguments(tmp_path):
    result = run_cli([])
    assert result.returncode == 1
    assert result.stderr.strip() != ""


# ---------------------------------------------------------------------------
# 15. settle output sort order is alphabetical by (debtor, creditor) with
#     multiple transactions
# ---------------------------------------------------------------------------
def test_settle_sort_order(tmp_path):
    # Constructed via three direct pairwise transfers so the target
    # balances are exact and hand-verifiable:
    #   dave pays 800, assigned entirely to grace  -> dave +800, grace -800
    #   dave pays 200, assigned entirely to frank   -> dave +200, frank -200
    #   erin pays 500, assigned entirely to frank   -> erin +500, frank -500
    # totals: dave=+1000, erin=+500, frank=-700, grace=-800 (sums to 0)
    #
    # Greedy largest-creditor-vs-largest-debtor:
    #   round 1: creditor dave(1000) vs debtor grace(800) -> settle 800
    #            dave -> 200, grace -> 0 (removed)
    #            txn: grace pays dave $8.00
    #   round 2: creditor erin(500) [now > dave's 200] vs debtor frank(700)
    #            -> settle 500; erin -> 0 (removed), frank -> 200
    #            txn: frank pays erin $5.00
    #   round 3: creditor dave(200) vs debtor frank(200) -> settle 200
    #            txn: frank pays dave $2.00
    #
    # Final printed order sorted by (debtor, creditor):
    #   frank pays dave  $2.00
    #   frank pays erin  $5.00
    #   grace pays dave  $8.00
    data = {
        "people": ["dave", "erin", "frank", "grace"],
        "expenses": [
            {"payer": "dave", "amountCents": 800, "participants": ["grace"]},
            {"payer": "dave", "amountCents": 200, "participants": ["frank"]},
            {"payer": "erin", "amountCents": 500, "participants": ["frank"]},
        ],
    }
    path = write_json(tmp_path, "expenses.json", data)

    result = run_cli(["settle", str(path)])
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "frank pays dave $2.00",
        "frank pays erin $5.00",
        "grace pays dave $8.00",
    ]


# ---------------------------------------------------------------------------
# 16. unknown command -> exit 1, usage on stderr (bonus coverage beyond the
#     15 required cases, since the spec calls this out as a distinct error)
# ---------------------------------------------------------------------------
def test_unknown_command(tmp_path):
    data = {"people": ["alice"], "expenses": []}
    path = write_json(tmp_path, "expenses.json", data)
    result = run_cli(["nope", str(path)])
    assert result.returncode == 1
    assert result.stderr.strip() != ""
