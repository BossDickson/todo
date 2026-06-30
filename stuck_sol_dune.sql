-- stuck_sol_dune.sql
-- Reproduce the "stuck SOL in mint accounts" leaderboard on Dune Analytics.
--
-- IDEA: an SPL token mint is rent-exempt at ~0.00146 SOL (1,461,600 lamports for a
-- standard 82-byte mint). Any balance ABOVE that floor is "excess" — SOL that was
-- (almost always accidentally) sent to the mint and is now bricked.
--
-- CAVEAT: Dune's Solana schema/table names change over time. Treat the table and
-- column names below as a STARTING POINT and adjust to whatever your workspace
-- exposes (check the Data Explorer: tokens_solana.*, solana.account_activity,
-- solana_utils.*). The logic — latest balance per mint minus the rent floor — is
-- what matters and is stable.

WITH mint_accounts AS (
    -- All SPL token mints. Adjust source table to your schema's token list.
    SELECT DISTINCT token_mint_address AS account
    FROM tokens_solana.token  -- e.g. tokens_solana.fungible / spl_token mints
),

latest_balance AS (
    -- Most recent post-transaction lamport balance per account.
    SELECT
        address,
        post_balance AS lamports,
        ROW_NUMBER() OVER (PARTITION BY address ORDER BY block_time DESC) AS rn
    FROM solana.account_activity
    WHERE address IN (SELECT account FROM mint_accounts)
)

SELECT
    address                                   AS mint,
    lamports / 1e9                            AS balance_sol,
    (lamports - 1461600) / 1e9                AS stuck_sol  -- excess above 82-byte rent floor
FROM latest_balance
WHERE rn = 1
  AND lamports > 1461600                      -- only mints holding more than the rent floor
ORDER BY stuck_sol DESC
LIMIT 100;

-- To get the grand total (~176,961 SOL per the Helius p-token writeup):
--   SELECT SUM((lamports - 1461600) / 1e9) AS total_stuck_sol
--   FROM latest_balance WHERE rn = 1 AND lamports > 1461600;
