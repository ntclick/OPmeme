# OPG Bridge — Base Sepolia ↔ OG Testnet (10740)

Lock/Mint + Burn/Release bridge for the OPG token between Base Sepolia (84532)
and the OG Testnet (10740).

```
opg-bridge/
├── contracts/   # Hardhat — WrappedOPG, OPGBridgeBase, OPGBridgeOG
├── relayer/     # Go — watches Locked/Burned events and relays them
└── frontend/    # Next.js 14 + wagmi + RainbowKit
```

## Chain config

| Property    | Base Sepolia                                 | OG Testnet                          |
|-------------|----------------------------------------------|-------------------------------------|
| Chain ID    | 84532                                        | 10740                               |
| RPC         | https://sepolia.base.org                     | https://ogevmdevnet.opengradient.ai |
| Explorer    | https://sepolia.basescan.org                 | https://explorer.opengradient.ai    |
| OPG token   | `0x240b09731D96979f50B2C649C9CE10FcF9C7987F` | wOPG (deployed via `deploy-og.ts`)  |

## Architecture

```
 user                                                        user
   │                                                           ▲
   │ approve + lock(amount)                          mint(...) │
   ▼                                                           │
 OPGBridgeBase (Base Sepolia) ── Locked event ─►  Relayer ─► OPGBridgeOG (OG)
                                                                │
                                                          mint  ▼
                                                         WrappedOPG (OG)

 user                                                        user
   │                                                           ▲
   │ burn(amount)                                  release(...)│
   ▼                                                           │
 OPGBridgeOG (OG) ───── Burned event ─────────►  Relayer ─► OPGBridgeBase (Base)
```

The off-chain relayer holds **two roles**: `relayer` on `OPGBridgeBase` and on
`OPGBridgeOG`. Each role can be a **separate wallet** — the relayer process
loads two private keys and uses each one only on its matching chain:

| Env var                    | Signs tx on        | Must be set as `relayer` on |
|----------------------------|--------------------|-----------------------------|
| `RELAYER_BASE_PRIVATE_KEY` | Base Sepolia 84532 | `OPGBridgeBase`             |
| `RELAYER_OG_PRIVATE_KEY`   | OG Testnet 10740   | `OPGBridgeOG`               |

You can use the same key for both if you want — just paste it twice. Each
`TxSender` keeps an independent nonce manager, so the two keys never collide.

## Deploy order

1. Pick a **deployer** wallet and **two relayer wallets** (one per chain — or
   reuse the same wallet on both). Fund every wallet on its respective chain.
2. Fill in env files:
   - `contracts/.env` → `DEPLOYER_PRIVATE_KEY`, `RELAYER_ADDRESS` (the address
     of the relayer wallet for *that* chain — deploy each side with the
     correct one).
   - `relayer/.env` → `RELAYER_BASE_PRIVATE_KEY` + `RELAYER_OG_PRIVATE_KEY`
     (must match the addresses set as `relayer` on the two bridge contracts).
3. Deploy contracts:
   ```bash
   cd contracts
   npm install
   npx hardhat compile
   npx hardhat run scripts/deploy-base.ts --network baseSepolia
   npx hardhat run scripts/deploy-og.ts   --network ogTestnet
   ```
   Note `BRIDGE_BASE_ADDR`, `WOPG_ADDR`, `BRIDGE_OG_ADDR` from the output.
4. Run the relayer:
   ```bash
   cd ../relayer
   cp .env.example .env   # then edit
   go mod tidy
   go run .
   ```
5. Run the frontend:
   ```bash
   cd ../frontend
   cp .env.local.example .env.local   # then edit
   npm install
   npm run dev
   ```

## How a transfer flows

**Base Sepolia → OG**
1. User calls `OPGBridgeBase.lock(amount)` (after `approve`).
2. `Locked(from, amount, nonce)` is emitted.
3. The relayer's `LockedWatcher` polls Base Sepolia logs from
   `data/state.json#base_locked_last_block`.
4. For each event, the relayer calls
   `OPGBridgeOG.mint(from, amount, nonce)` on chain 10740.
5. `WrappedOPG` mints `amount` wOPG to the user.

**OG → Base Sepolia**
1. User calls `OPGBridgeOG.burn(amount)` — bridge burns the user's wOPG and
   emits `Burned(from, amount, nonce)`.
2. The relayer's `BurnedWatcher` polls OG logs.
3. The relayer calls `OPGBridgeBase.release(from, amount, nonce)` on Base
   Sepolia, which transfers the locked OPG back.

`processedNonces[nonce]` is checked on both bridge contracts so each event can
only be relayed once.

## Notes

- Solidity 0.8.24, OpenZeppelin v5.
- Relayer is intentionally simple: poll-based, file-backed state
  (`relayer/data/state.json`), retries up to 3× per tx with a 20% gas buffer.
- ABIs for the relayer live in `relayer/abi/*.json` (small hand-trimmed copies
  containing only the events + functions the relayer needs). They are embedded
  via `go:embed`, so no codegen step is required.
- The frontend reads bridge events directly via `useWatchContractEvent` — no
  backend API.
- Tests for the contracts can be added under `contracts/test/` following the
  spec: `lock → Locked event`, `mint → balance`, `burn → Burned event`,
  `release → balance`.
