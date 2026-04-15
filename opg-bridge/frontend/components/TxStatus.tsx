"use client";

import { useEffect, useState } from "react";
import {
  useWaitForTransactionReceipt,
  useWatchContractEvent,
} from "wagmi";
import { baseSepolia } from "wagmi/chains";

import {
  BRIDGE_BASE_ABI,
  BRIDGE_BASE_ADDR,
  BRIDGE_OG_ABI,
  BRIDGE_OG_ADDR,
  ETH_BRIDGE_ABI,
  ETH_BRIDGE_BASE_ADDR,
  ETH_BRIDGE_OG_ADDR,
} from "@/lib/contracts";
import { ogTestnet } from "@/lib/wagmi";

type Props = {
  asset: "OPG" | "ETH";
  direction: "base-to-og" | "og-to-base";
  txHash: `0x${string}`;
  kind: "approve" | "bridge";
  user?: `0x${string}`;
};

type Stage = "pending" | "submitted" | "relaying" | "completed";

export function TxStatus({ asset, direction, txHash, kind, user }: Props) {
  const [stage, setStage] = useState<Stage>("pending");

  const sourceChain = direction === "base-to-og" ? baseSepolia.id : ogTestnet.id;
  const destChain = direction === "base-to-og" ? ogTestnet.id : baseSepolia.id;

  const { isSuccess: sourceMined } = useWaitForTransactionReceipt({
    hash: txHash,
    chainId: sourceChain,
  });

  useEffect(() => {
    setStage("pending");
  }, [txHash]);

  useEffect(() => {
    if (sourceMined && stage === "pending") {
      setStage(kind === "approve" ? "completed" : "relaying");
    }
  }, [sourceMined, kind, stage]);

  // Pick which destination event to watch
  let destAddr: `0x${string}`;
  let destAbi: readonly unknown[];
  let eventName: string;

  if (asset === "ETH") {
    destAddr = direction === "base-to-og" ? ETH_BRIDGE_OG_ADDR : ETH_BRIDGE_BASE_ADDR;
    destAbi = ETH_BRIDGE_ABI;
    eventName = "ReleasedETH";
  } else if (direction === "base-to-og") {
    destAddr = BRIDGE_OG_ADDR;
    destAbi = BRIDGE_OG_ABI;
    eventName = "Minted";
  } else {
    destAddr = BRIDGE_BASE_ADDR;
    destAbi = BRIDGE_BASE_ABI;
    eventName = "Released";
  }

  useWatchContractEvent({
    chainId: destChain,
    address: destAddr,
    abi: destAbi as any,
    eventName: eventName as any,
    enabled: kind === "bridge" && stage === "relaying",
    onLogs: (logs) => {
      for (const l of logs) {
        const to = (l as unknown as { args?: { to?: string } }).args?.to;
        if (to && user && to.toLowerCase() === user.toLowerCase()) {
          setStage("completed");
        }
      }
    },
  });

  const explorerBase =
    direction === "base-to-og"
      ? "https://sepolia.basescan.org/tx/"
      : "https://explorer.opengradient.ai/tx/";

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-950/60 p-4 text-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-medium text-slate-200">
          {kind === "approve"
            ? "Approval"
            : asset === "ETH"
              ? "ETH bridge transfer"
              : "OPG bridge transfer"}
        </span>
        <a
          href={explorerBase + txHash}
          target="_blank"
          rel="noreferrer"
          className="text-xs text-indigo-400 hover:underline"
        >
          {txHash.slice(0, 10)}…{txHash.slice(-6)}
        </a>
      </div>

      <ol className="space-y-1 text-slate-400">
        <li className={stage !== "pending" ? "text-emerald-400" : ""}>
          {stage !== "pending" ? "✓" : "•"} Source tx submitted
        </li>
        {kind === "bridge" && (
          <>
            <li
              className={
                stage === "relaying" || stage === "completed"
                  ? "text-emerald-400"
                  : ""
              }
            >
              {stage === "relaying" || stage === "completed" ? "✓" : "•"}{" "}
              Waiting for relayer…
            </li>
            <li className={stage === "completed" ? "text-emerald-400" : ""}>
              {stage === "completed" ? "✓" : "•"} Destination tx confirmed
            </li>
          </>
        )}
      </ol>
    </div>
  );
}
