#!/usr/bin/env python3
"""
stuck_sol_scan.py — Identify "stuck" SOL bricked inside Solana SPL token mint accounts.

Background
----------
When someone accidentally sends native SOL to a *token mint account* (which looks
like any other base58 address), the extra lamports sit above the account's
rent-exempt minimum and cannot be moved by a normal wallet. The Anza "p-token"
program added a `withdraw_excess_lamports` instruction that can pull this excess out.

Who can recover it:
  * If the mint still has a `mintAuthority` -> that authority can withdraw now.
  * If the mint authority was revoked (mintAuthority = null) -> the ONLY way to
    withdraw is to sign with the mint account's OWN private key (the keypair the
    mint was created from). Most projects discarded that key, making recovery
    impossible in practice.

This script READS public chain data only. It never asks for or touches a private
key. It just tells you, per mint: balance, rent-exempt floor, excess (stuck) SOL,
and whether a mint authority still exists.

Usage
-----
  python3 stuck_sol_scan.py                       # scan the built-in sample list
  python3 stuck_sol_scan.py <MINT1> <MINT2> ...   # scan specific mints
  python3 stuck_sol_scan.py --file mints.txt      # batch sweep from a file
  cat mints.txt | python3 stuck_sol_scan.py --file -   # ...or from stdin
  python3 stuck_sol_scan.py --file mints.txt --top 50 # show only the top 50

  RPC_URL=https://your-rpc python3 stuck_sol_scan.py   # use a paid RPC for big sweeps

File format (one mint per line; blank lines and '#' comments ignored):
    ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82
    BOME,ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82   # optional LABEL,MINT form

No third-party packages required (uses urllib).
NOTE: public RPCs rate-limit hard. For a full 869k-account sweep use a paid RPC
(Helius/Triton/QuickNode) or the Dune query in stuck_sol_dune.sql.
"""

import json
import os
import sys
import urllib.request

RPC_URL = os.environ.get("RPC_URL", "https://api.mainnet-beta.solana.com")
LAMPORTS_PER_SOL = 1_000_000_000
MAX_ACCOUNTS_PER_CALL = 100  # getMultipleAccounts hard limit

# Canonical mainnet mints for the high-profile tokens named in the "stuck SOL" post.
# (Verified via public explorers — always re-verify before acting on anything.)
DEFAULT_MINTS = {
    "BOME":    "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82",
    "TRUMP":   "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN",
    "MELANIA": "FUAfBo2jgks6gB4Z4LfZkqSZgzNucisEHqnNebaRxM1P",
    "WSOL":    "So11111111111111111111111111111111111111112",
}

_rent_cache = {}  # data_len -> rent-exempt minimum lamports


def rpc(method, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC_URL, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.loads(r.read())
    if "error" in out:
        raise RuntimeError(out["error"])
    return out["result"]


def rent_exempt_minimum(data_len):
    if data_len not in _rent_cache:
        _rent_cache[data_len] = rpc("getMinimumBalanceForRentExemption", [data_len])
    return _rent_cache[data_len]


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def evaluate(label, mint, val):
    """Turn a raw getAccountInfo 'value' into a stuck-SOL record."""
    if val is None:
        return {"label": label, "mint": mint, "error": "account not found"}

    lamports = val["lamports"]
    owner = val["owner"]
    data = val.get("data", {})
    parsed = data.get("parsed", {}) if isinstance(data, dict) else {}
    is_mint = parsed.get("type") == "mint"
    space = val.get("space") or parsed.get("info", {}).get("space") or 82

    rent_floor = rent_exempt_minimum(space)
    excess = lamports - rent_floor
    mint_auth = parsed.get("info", {}).get("mintAuthority") if is_mint else None

    return {
        "label": label,
        "mint": mint,
        "owner": owner,
        "is_mint": is_mint,
        "lamports": lamports,
        "excess_lamports": excess,
        "excess_sol": excess / LAMPORTS_PER_SOL,
        "mint_authority": mint_auth,
        "recoverable": (
            "by mint authority (active)" if mint_auth
            else "needs the mint's own private key (authority revoked)"
        ),
    }


def scan(targets):
    """targets: list of (label, mint). Batched via getMultipleAccounts."""
    results = []
    mints = [m for _, m in targets]
    for batch in chunks(targets, MAX_ACCOUNTS_PER_CALL):
        addrs = [m for _, m in batch]
        res = rpc("getMultipleAccounts", [addrs, {"encoding": "jsonParsed"}])
        values = res.get("value", [])
        for (label, mint), val in zip(batch, values):
            try:
                results.append(evaluate(label, mint, val))
            except Exception as e:
                results.append({"label": label, "mint": mint, "error": str(e)})
    return results


def load_targets(args):
    """Resolve CLI args / file / stdin into a list of (label, mint)."""
    if "--file" in args:
        idx = args.index("--file")
        path = args[idx + 1]
        lines = (sys.stdin if path == "-" else open(path)).read().splitlines()
        targets = []
        for ln in lines:
            ln = ln.split("#", 1)[0].strip()
            if not ln:
                continue
            if "," in ln:
                label, mint = (p.strip() for p in ln.split(",", 1))
            else:
                label, mint = ln[:8], ln
            targets.append((label, mint))
        return targets

    positional = [a for a in args if not a.startswith("--")]
    if positional:
        return [(m[:8], m) for m in positional]
    return list(DEFAULT_MINTS.items())


def main():
    args = sys.argv[1:]
    top = None
    if "--top" in args:
        i = args.index("--top")
        top = int(args[i + 1])
        args = args[:i] + args[i + 2:]

    targets = load_targets(args)
    print(f"RPC: {RPC_URL}")
    print(f"Scanning {len(targets)} mint(s)...\n")

    results = scan(targets)
    ok = [r for r in results if not r.get("error")]
    ok.sort(key=lambda r: r["excess_sol"], reverse=True)
    if top:
        shown = ok[:top]
    else:
        shown = ok

    header = f"{'TOKEN':<12} {'STUCK SOL':>14} {'BALANCE SOL':>14}  RECOVERABLE"
    print(header)
    print("-" * len(header))
    for r in shown:
        print(f"{r['label']:<12} {r['excess_sol']:>14.4f} {r['lamports']/LAMPORTS_PER_SOL:>14.4f}  {r['recoverable']}")
        if not r["is_mint"]:
            print(f"{'':<12} (note: not an SPL mint; owner={r['owner']})")

    total = sum(max(r["excess_sol"], 0) for r in ok)
    errs = [r for r in results if r.get("error")]
    print("-" * len(header))
    print(f"{'TOTAL':<12} {total:>14.4f} SOL stuck across {len(ok)} scanned mint(s)"
          + (f"  ({len(shown)} shown)" if top and len(shown) < len(ok) else ""))
    if errs:
        print(f"\n{len(errs)} account(s) errored (e.g. not found / rate-limited):")
        for r in errs[:10]:
            print(f"  {r['mint']}: {r['error']}")


if __name__ == "__main__":
    main()
