# Legacy: the Go data service

The data service was first written in Go (`fpl_data_service_go/`), then ported
to Python (`../fpl_data_service/`) so the whole project is one language.

This copy is **not built, tested or run by anything**. It is kept so you can:

- compare behaviour: both services serve the same `proto/squadscout/v1/fpl.proto`,
  and the port was checked by sending 45 requests to both against live FPL data
  and diffing the answers (they were identical);
- read the original if a Python function looks odd: the porting notes in
  `docs/architecture.md` say how each Go package maps to a Python module.

To run it anyway (needs Go installed):

```bash
cd legacy/fpl_data_service_go && go run ./cmd/api   # listens on :50051
```

It is safe to delete this folder once you no longer need the comparison.
