LMUAI provider probe — 2026-10-24

/models: HTTP 200; total IDs: 5; matching deepseek|qwen|mimo|minimax:
deepseek-v4-pro, deepseek-v4-flash, deepseek-v4-flash-vision-exp, deepseek-flash, deepseek-v4.1-flash.
context_window=1048576 for all except deepseek-flash (not supplied). No owned_by, created, pricing, or other context-length metadata supplied. No Qwen/MiMo/MiniMax IDs in this credential’s listing.

Usage below: prompt/completion/total tokens; reasoning tokens in parentheses. Cached tokens: 0 in normalized usage for all three.

deepseek-v4.1-flash: HTTP 200; returned=deepseek-flash; reply=""; usage=36/16/52 (reasoning 16); 9.54s; ledger=$0.00003000 (complete).
qwen3.7-plus: HTTP 200; returned=qwen3.7-plus; reply="OK."; usage=21/99/120 (reasoning 95); 11.46s; ledger=$0.00033360 (complete).
mimo-v2.6-flash: HTTP 200; returned=mimo-v2.6-flash; reply="OK."; usage=17/15/32 (reasoning 11); 12.99s; ledger=$0.00001316 (complete).

Numeric cost/rate-limit headers: none for /models or the three configured completions. system_fingerprint=null for all three. Ledger: runs/budget_lmuai.sqlite; these three rows verified, total $0.00037676. Costs are local estimates, not provider-confirmed charges.

Extra deepseek-flash identity request: user-only requested prompt; max_tokens=40, temperature=0. HTTP 200; returned=deepseek-flash; system_fingerprint=null; reply=""; 9.21s. Usage: 43/40/83; cached=0, image=0, prompt text=43, reasoning=40, completion text=0. No identity text returned. This direct request was not recorded in the ledger.

Raw probe output was sed-redacted. No Docker or sweeps.
