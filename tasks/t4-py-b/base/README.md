# layercfg

Fold a stack of configuration layers into one effective config.

Layers apply left to right. Dicts merge; lists replace. `key+` appends to an
inherited list. `None` deletes a key. A dict carrying `__lock__: true` freezes
itself and everything beneath it against later layers.

Run tests:

```
pip install -r requirements.txt
pytest -q
```
