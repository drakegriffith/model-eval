# Relay conventions

**Read this before writing a stage or a driver.** The relay inverts the usual
control-flow convention on purpose, and code written to the ordinary one is
silently wrong here.

## The Done protocol

| a stage does this | it means |
|---|---|
| `raise Done(payload)` | **finished successfully**, with `payload` as its result |
| `raise Done()` | finished successfully, payload `None` — still a success |
| `return hint` | **not finished.** `hint` is fed back into the same stage |
| `return None` | abort the whole pipeline |
| `raise Abort(reason)` | abort the whole pipeline |
| raises anything else | not ours — it propagates untouched |

The protocol came from a generator-based pipeline where completion was
signalled the way `StopIteration` signals it. When the generators went away
the signal stayed, because every stage in the tree was written against it.

## Consequences that bite

- **Never wrap a stage call in `except Exception`.** It swallows `Done`,
  which is the success path, and it swallows the errors rule 4 requires to
  propagate.
- **A returned value is never a result.** It is an argument for the next
  invocation of the same stage. It does not cross a stage boundary; only a
  `Done` payload does.
- **`None` is overloaded and the overload is intentional.** A *returned*
  `None` aborts. A `Done(None)` — or a bare `Done()` — is success.
- **Attempts are invocations, not retries.** `max_attempts=1` means the stage
  is called once and then, if it has not raised `Done`, it is exhausted.
