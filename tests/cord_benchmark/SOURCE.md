# CORD-v2 benchmark subset

20 receipts from the CORD-v2 test split, source: https://huggingface.co/datasets/naver-clova-ix/cord-v2 (license: CC-BY-4.0).

Ground truth here is a NARROW, honest subset of CORD's own annotations. Every document is scored on `total` and line-item descriptions; the 14 of 20 that CORD also annotates a subtotal for are additionally scored on `subtotal`. merchant/date/tax are excluded because CORD annotates them too inconsistently to trust as ground truth -- see fetch_cord_benchmark.py's docstring. This is an external, public benchmark, run separately from and in addition to the hand-verified 29-document set under tests/sample_invoices and tests/sample_receipts, not a replacement for it.
