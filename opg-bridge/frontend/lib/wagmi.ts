"use client";

import { getDefaultConfig } from "@rainbow-me/rainbowkit";
import { baseSepolia } from "wagmi/chains";
import { defineChain } from "viem";

export const ogTestnet = defineChain({
  id: 10740,
  name: "OG Testnet",
  nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
  rpcUrls: {
    default: { http: ["https://ogevmdevnet.opengradient.ai"] },
  },
  blockExplorers: {
    default: { name: "OG Explorer", url: "https://explorer.opengradient.ai" },
  },
  testnet: true,
});

const projectId =
  process.env.NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID ?? "demo-walletconnect";

export const wagmiConfig = getDefaultConfig({
  appName: "OPG Bridge",
  projectId,
  chains: [baseSepolia, ogTestnet],
  ssr: true,
});
