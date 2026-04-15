"use client";

import Link from "next/link";
import { ConnectButton } from "@rainbow-me/rainbowkit";
import { BridgeForm } from "@/components/BridgeForm";

export default function Page() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col items-center px-6 py-12">
      <header className="mb-10 flex w-full items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">OPG Bridge</h1>
          <p className="text-sm text-slate-400">
            Base Sepolia (84532) ↔ OG Testnet (10740)
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/chat"
            className="rounded-lg bg-slate-800 px-3 py-2 text-sm text-slate-300 hover:bg-slate-700"
          >
            x402 Chat
          </Link>
          <ConnectButton />
        </div>
      </header>

      <section className="w-full">
        <BridgeForm />
      </section>

      <footer className="mt-16 text-center text-xs text-slate-500">
        Lock / Mint · Burn / Release · Powered by an off-chain relayer
      </footer>
    </main>
  );
}
