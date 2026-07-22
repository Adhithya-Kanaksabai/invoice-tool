# CORD-v2 benchmark subset

20 receipts from the CORD-v2 test split, source: https://huggingface.co/datasets/naver-clova-ix/cord-v2 (license: CC-BY-4.0).

Ground truth here is a NARROW, honest subset of CORD's own annotations (total + line-item descriptions only) -- see fetch_cord_benchmark.py's docstring for why merchant/date/tax/subtotal aren't included. This is an external, public benchmark, run separately from and in addition to the hand-verified 29-document set under tests/sample_invoices and tests/sample_receipts, not a replacement for it.
