# relay

A staged message relay. `run_stage` drives one stage, `drain` drives a
pipeline of them.

**The control-flow convention is inverted and it is load-bearing.** A stage
signals success by *raising* `Done(payload)`; a normal *return* means it has
not finished and the returned value is a retry hint. `CONVENTIONS.md` is the
contract and `src/relay/protocol.py` is frozen.

Run tests:

```
pip install -r requirements.txt
pytest -q
```
