"use client";

import { useMemo, useState } from "react";
import { parseUnits, formatUnits, formatEther, parseEther } from "viem";
import {
  useAccount,
  useBalance,
  useChainId,
  useReadContract,
  useSwitchChain,
  useWaitForTransactionReceipt,
  useWriteContract,
} from "wagmi";
import { baseSepolia } from "wagmi/chains";

import {
  BRIDGE_BASE_ABI,
  BRIDGE_BASE_ADDR,
  BRIDGE_OG_ABI,
  BRIDGE_OG_ADDR,
  ERC20_ABI,
  ETH_BRIDGE_ABI,
  ETH_BRIDGE_BASE_ADDR,
  ETH_BRIDGE_OG_ADDR,
  OPG_TOKEN,
  WOPG_ADDR,
} from "@/lib/contracts";
import { ogTestnet } from "@/lib/wagmi";
import { TxStatus } from "./TxStatus";

type Direction = "base-to-og" | "og-to-base";
type Asset = "OPG" | "ETH";

export function BridgeForm() {
  const { address, isConnected } = useAccount();
  const chainId = useChainId();
  const { switchChain } = useSwitchChain();

  const [asset, setAsset] = useState<Asset>("OPG");
  const [direction, setDirection] = useState<Direction>("base-to-og");
  const [amount, setAmount] = useState("");
  const [pendingTxHash, setPendingTxHash] = useState<`0x${string}` | undefined>();
  const [pendingKind, setPendingKind] = useState<"approve" | "bridge" | undefined>();

  const fromChain = direction === "base-to-og" ? baseSepolia.id : ogTestnet.id;

  // Asset-specific addresses
  const opgTokenAddr = direction === "base-to-og" ? OPG_TOKEN : WOPG_ADDR;
  const opgBridgeAddr = direction === "base-to-og" ? BRIDGE_BASE_ADDR : BRIDGE_OG_ADDR;
  const ethBridgeAddr = direction === "base-to-og" ? ETH_BRIDGE_BASE_ADDR : ETH_BRIDGE_OG_ADDR;

  const decimals = 18;
  const parsed = useMemo(() => {
    try {
      if (!amount) return 0n;
      return asset === "ETH" ? parseEther(amount) : parseUnits(amount, decimals);
    } catch {
      return 0n;
    }
  }, [amount, asset]);

  // OPG ERC20 balance (only used when asset === OPG)
  const { data: opgBalance } = useReadContract({
    chainId: fromChain,
    address: opgTokenAddr as `0x${string}`,
    abi: ERC20_ABI,
    functionName: "balanceOf",
    args: address ? [address] : undefined,
    query: { enabled: !!address && asset === "OPG" },
  });

  // Native ETH balance (only used when asset === ETH)
  const { data: ethBalanceData } = useBalance({
    chainId: fromChain,
    address,
    query: { enabled: !!address && asset === "ETH" },
  });

  // OPG approval check (only when bridging OPG from Base — OG side burns directly)
  const { data: allowance, refetch: refetchAllowance } = useReadContract({
    chainId: fromChain,
    address: opgTokenAddr as `0x${string}`,
    abi: ERC20_ABI,
    functionName: "allowance",
    args: address ? [address, opgBridgeAddr] : undefined,
    query: { enabled: !!address && asset === "OPG" && direction === "base-to-og" },
  });

  const needsApproval =
    asset === "OPG" &&
    direction === "base-to-og" &&
    parsed > 0n &&
    (allowance ?? 0n) < parsed;

  const { writeContractAsync, isPending: isWriting } = useWriteContract();
  const { isLoading: isMining } = useWaitForTransactionReceipt({
    hash: pendingTxHash,
    chainId: fromChain,
    query: { enabled: !!pendingTxHash },
  });

  const wrongChain = isConnected && chainId !== fromChain;

  async function ensureChain() {
    if (chainId !== fromChain) {
      await switchChain({ chainId: fromChain });
    }
  }

  function resetTx() {
    setPendingTxHash(undefined);
    setPendingKind(undefined);
  }

  async function handleApprove() {
    await ensureChain();
    const hash = await writeContractAsync({
      chainId: fromChain,
      address: opgTokenAddr as `0x${string}`,
      abi: ERC20_ABI,
      functionName: "approve",
      args: [opgBridgeAddr, parsed],
    });
    setPendingKind("approve");
    setPendingTxHash(hash);
    setTimeout(() => refetchAllowance(), 6000);
  }

  async function handleBridge() {
    await ensureChain();

    if (asset === "ETH") {
      // ETHBridge.lock() payable on either chain
      const hash = await writeContractAsync({
        chainId: fromChain,
        address: ethBridgeAddr,
        abi: ETH_BRIDGE_ABI,
        functionName: "lock",
        args: [],
        value: parsed,
      });
      setPendingKind("bridge");
      setPendingTxHash(hash);
      return;
    }

    // OPG flow
    if (direction === "base-to-og") {
      const hash = await writeContractAsync({
        chainId: baseSepolia.id,
        address: BRIDGE_BASE_ADDR,
        abi: BRIDGE_BASE_ABI,
        functionName: "lock",
        args: [parsed],
      });
      setPendingKind("bridge");
      setPendingTxHash(hash);
    } else {
      const hash = await writeContractAsync({
        chainId: ogTestnet.id,
        address: BRIDGE_OG_ADDR,
        abi: BRIDGE_OG_ABI,
        functionName: "burn",
        args: [parsed],
      });
      setPendingKind("bridge");
      setPendingTxHash(hash);
    }
  }

  const balanceLabel =
    asset === "ETH"
      ? ethBalanceData
        ? formatEther(ethBalanceData.value)
        : "0"
      : opgBalance
        ? formatUnits(opgBalance as bigint, decimals)
        : "0";

  const assetLabel =
    asset === "ETH" ? "ETH" : direction === "base-to-og" ? "OPG" : "wOPG";

  const ethEnabled =
    ETH_BRIDGE_BASE_ADDR !== "0x0000000000000000000000000000000000000000" &&
    ETH_BRIDGE_OG_ADDR !== "0x0000000000000000000000000000000000000000";

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6 shadow-xl backdrop-blur">
      {/* Asset toggle */}
      <div className="mb-3 flex items-center gap-2">
        <button
          type="button"
          onClick={() => {
            setAsset("OPG");
            resetTx();
          }}
          className={`flex-1 rounded-lg px-3 py-2 text-sm font-medium transition ${
            asset === "OPG"
              ? "bg-emerald-500 text-white"
              : "bg-slate-800 text-slate-300 hover:bg-slate-700"
          }`}
        >
          OPG
        </button>
        <button
          type="button"
          disabled={!ethEnabled}
          onClick={() => {
            setAsset("ETH");
            resetTx();
          }}
          className={`flex-1 rounded-lg px-3 py-2 text-sm font-medium transition disabled:opacity-40 ${
            asset === "ETH"
              ? "bg-emerald-500 text-white"
              : "bg-slate-800 text-slate-300 hover:bg-slate-700"
          }`}
          title={ethEnabled ? "" : "ETH bridge addresses not configured"}
        >
          ETH
        </button>
      </div>

      {/* Direction toggle */}
      <div className="mb-4 flex items-center gap-2">
        <button
          type="button"
          onClick={() => {
            setDirection("base-to-og");
            resetTx();
          }}
          className={`flex-1 rounded-lg px-3 py-2 text-sm font-medium transition ${
            direction === "base-to-og"
              ? "bg-indigo-500 text-white"
              : "bg-slate-800 text-slate-300 hover:bg-slate-700"
          }`}
        >
          Base Sepolia → OG
        </button>
        <button
          type="button"
          onClick={() => {
            setDirection("og-to-base");
            resetTx();
          }}
          className={`flex-1 rounded-lg px-3 py-2 text-sm font-medium transition ${
            direction === "og-to-base"
              ? "bg-indigo-500 text-white"
              : "bg-slate-800 text-slate-300 hover:bg-slate-700"
          }`}
        >
          OG → Base Sepolia
        </button>
      </div>

      <label className="mb-2 block text-xs uppercase tracking-wider text-slate-400">
        Amount ({assetLabel})
      </label>
      <div className="mb-2 flex items-center gap-2 rounded-xl border border-slate-700 bg-slate-950 px-4 py-3">
        <input
          type="text"
          inputMode="decimal"
          placeholder="0.0"
          value={amount}
          onChange={(e) => setAmount(e.target.value.replace(/[^0-9.]/g, ""))}
          className="flex-1 bg-transparent text-2xl outline-none"
        />
        <button
          type="button"
          className="text-xs text-indigo-400 hover:text-indigo-300"
          onClick={() => {
            if (asset === "ETH" && ethBalanceData) {
              // Leave a tiny buffer for gas
              const v = ethBalanceData.value;
              const buf = parseEther("0.0005");
              setAmount(formatEther(v > buf ? v - buf : 0n));
            } else if (asset === "OPG" && opgBalance) {
              setAmount(formatUnits(opgBalance as bigint, decimals));
            }
          }}
        >
          MAX
        </button>
      </div>
      <div className="mb-6 text-xs text-slate-500">Balance: {balanceLabel}</div>

      {!isConnected ? (
        <p className="rounded-lg bg-slate-800 p-3 text-center text-sm text-slate-300">
          Connect your wallet to bridge.
        </p>
      ) : wrongChain ? (
        <button
          type="button"
          onClick={() => switchChain({ chainId: fromChain })}
          className="w-full rounded-xl bg-amber-500 px-4 py-3 font-semibold text-slate-900 hover:bg-amber-400"
        >
          Switch to {direction === "base-to-og" ? "Base Sepolia" : "OG Testnet"}
        </button>
      ) : needsApproval ? (
        <button
          type="button"
          disabled={isWriting || isMining || parsed === 0n}
          onClick={handleApprove}
          className="w-full rounded-xl bg-indigo-500 px-4 py-3 font-semibold text-white hover:bg-indigo-400 disabled:opacity-50"
        >
          {isWriting || (isMining && pendingKind === "approve")
            ? "Approving..."
            : "Approve OPG"}
        </button>
      ) : (
        <button
          type="button"
          disabled={isWriting || isMining || parsed === 0n}
          onClick={handleBridge}
          className="w-full rounded-xl bg-emerald-500 px-4 py-3 font-semibold text-white hover:bg-emerald-400 disabled:opacity-50"
        >
          {isWriting || (isMining && pendingKind === "bridge")
            ? "Submitting..."
            : asset === "ETH"
              ? "Lock & Bridge ETH"
              : direction === "base-to-og"
                ? "Lock & Bridge"
                : "Burn & Bridge"}
        </button>
      )}

      {pendingTxHash && (
        <div className="mt-6">
          <TxStatus
            asset={asset}
            direction={direction}
            txHash={pendingTxHash}
            kind={pendingKind ?? "bridge"}
            user={address}
          />
        </div>
      )}
    </div>
  );
}
