import { HardhatUserConfig } from "hardhat/config";
import "@nomicfoundation/hardhat-toolbox";
import * as dotenv from "dotenv";

dotenv.config();

// Note: OG Testnet (10740) is deployed via scripts/deploy-og-direct.ts because
// the local DNS resolver refuses ogevmdevnet.opengradient.ai. The direct
// script bypasses Hardhat's HTTP provider and uses an https.Agent with a
// pinned DNS lookup.

const PK = process.env.DEPLOYER_PRIVATE_KEY;
const accounts = PK ? [PK] : [];

const config: HardhatUserConfig = {
  solidity: {
    version: "0.8.24",
    settings: {
      optimizer: { enabled: true, runs: 200 },
    },
  },
  networks: {
    baseSepolia: {
      url: process.env.BASE_SEPOLIA_RPC || "https://sepolia.base.org",
      chainId: 84532,
      accounts,
    },
    ogTestnet: {
      url: process.env.OG_TESTNET_RPC || "https://ogevmdevnet.opengradient.ai",
      chainId: 10740,
      accounts,
    },
  },
};

export default config;
