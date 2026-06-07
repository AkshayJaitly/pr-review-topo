# Code Review — pipeline branch

Quick pass over the diff. Three files touched.

## pipeline/main.py

Replaced `sys.argv` with `argparse`. Reasonable. Added an `--encoding` flag
default `utf-8`. Looks clean. The flag doesn't seem to be plumbed through to
the read function though — `load_and_clean(args.path)` doesn't pass
`encoding`. If the flag is meant to do something, wire it through; otherwise
drop it. Minor.

## pipeline/transform.py

Mostly cosmetic — added a docstring, renamed `rows` → `raw`, renamed loop var
`r` → `row`. No behavior change. Fine.

## pipeline/io.py

Added `encoding` parameter (defaults to `utf-8`, explicit on `open()`).
Reasonable hardening — relying on platform default encoding is fragile.

Added a docstring. Good.

## Overall

Looks like a small quality-of-life improvement. The unused `--encoding` flag
in main.py is the only thing worth fixing before merge. Otherwise looks
ready.

**Recommendation: approve with minor changes.**
