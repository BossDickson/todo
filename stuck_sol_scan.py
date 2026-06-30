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
  python3 stuck_sol_scan.py                      # scans the built-in list below
  python3 stuck_sol_scan.py <MINT1> <MINT2> ...  # scans specific mints
  RPC_URL=https://your-rpc python3 stuck_sol_scan.py

No third-party packages required (uses urllib).
NOTE: public RPCs rate-limit. For a full 869k-account sweep use a paid RPC
(Helius/Triton/QuickNode) or the Dune query in stuck_sol_dune.sql instead.
"""

import json
import os
import sys
import urllib.request

RPC_URL = os.environ.get("RPC_URL", "https://api.mainnet-beta.solana.com")
LAMPORTS_PER_SOL = 1_000_000_000

# Canonical mainnet mints for the high-profile tokens named in the "stuck SOL" post.
# (Verified via public explorers — always re-verify before acting on anything.)
DEFAULT_MINTS = {
    "BOME":    "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82",
    "TRUMP":   "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN",
    "MELANIA": "FUAfBo2jgks6gB4Z4LfZkqSZgzNucisEHqnNebaRxM1P",
    "WSOL":    "So11111111111111111111111111111111111111112",
}


def rpc(method, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC_URL, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read())
    if "error" in out:
        raise RuntimeError(out["error"])
    return out["result"]


def rent_exempt_minimum(data_len):
    return rpc("getMinimumBalanceForRentExemption", [data_len])


def scan_mint(label, mint):
    info = rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
    val = info.get("value")
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
        "rent_floor": rent_floor,
        "excess_lamports": excess,
        "excess_sol": excess / LAMPORTS_PER_SOL,
        "mint_authority": mint_auth,
        "recoverable": (
            "by mint authority (active)" if mint_auth
            else "only with the mint's own private key (authority revoked)"
        ),
    }


def main():
    args = sys.argv[1:]
    targets = {m: m for m in args} if args else DEFAULT_MINTS

    print(f"RPC: {RPC_URL}\n")
    header = f"{'TOKEN':<10} {'STUCK SOL':>14} {'BALANCE SOL':>14}  RECOVERABLE"
    print(header)
    print("-" * len(header))

    total = 0.0
    for label, mint in targets.items():
        try:
            r = scan_mint(label, mint)
        except Exception as e:
            print(f"{label:<10} {'ERROR':>14}  {e}")
            continue
        if r.get("error"):
            print(f"{label:<10} {'N/A':>14}  {r['error']}")
            continue
        total += max(r["excess_sol"], 0)
        print(f"{r['label']:<10} {r['excess_sol']:>14.4f} {r['lamports']/LAMPORTS_PER_SOL:>14.4f}  {r['recoverable']}")
        if not r["is_mint"]:
            print(f"{'':<10} (note: account is not an SPL mint; owner={r['owner']})")

    print("-" * len(header))
    print(f"{'TOTAL':<10} {total:>14.4f} SOL of stuck/excess lamports across scanned mints")


if __name__ == "__main__":
    main()
