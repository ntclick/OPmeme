// x402 payment signing for server-side use.
// Signs TransferWithAuthorization (EIP-3009) via EIP-712 typed data.

import {
  createWalletClient,
  http,
  type Hex,
  type WalletClient,
  encodeFunctionData,
  parseAbi,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { baseSepolia } from "viem/chains";

const OPG_TOKEN = "0x240b09731D96979f50B2C649C9CE10FcF9C7987F" as const;

// EIP-712 domain for OPG token on Base Sepolia
const DOMAIN = {
  name: "OPG",
  version: "1",
  chainId: 84532,
  verifyingContract: OPG_TOKEN,
} as const;

const TYPES = {
  TransferWithAuthorization: [
    { name: "from", type: "address" },
    { name: "to", type: "address" },
    { name: "value", type: "uint256" },
    { name: "validAfter", type: "uint256" },
    { name: "validBefore", type: "uint256" },
    { name: "nonce", type: "bytes32" },
  ],
} as const;

export interface PaymentRequirements {
  scheme: string;
  network: string;
  maxAmountRequired: string;
  resource: string;
  payTo: string;
  maxTimeoutSeconds: number;
  asset: string;
  extra?: { name: string; version: string };
}

function randomBytes32(): Hex {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return ("0x" +
    Array.from(bytes)
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("")) as Hex;
}

let _wallet: WalletClient | null = null;

function getWallet(): WalletClient {
  if (_wallet) return _wallet;
  const pk = process.env.OPENGRADIENT_PRIVATE_KEY;
  if (!pk) throw new Error("OPENGRADIENT_PRIVATE_KEY not set");
  const key = (pk.startsWith("0x") ? pk : `0x${pk}`) as Hex;
  const account = privateKeyToAccount(key);
  _wallet = createWalletClient({
    account,
    chain: baseSepolia,
    transport: http("https://sepolia.base.org"),
  });
  return _wallet;
}

export function getWalletAddress(): string {
  const wallet = getWallet();
  return wallet.account!.address;
}

export async function signPayment(
  requirements: PaymentRequirements
): Promise<string> {
  const wallet = getWallet();
  const from = wallet.account!.address;
  const to = requirements.payTo as Hex;
  const value = BigInt(requirements.maxAmountRequired);
  const validAfter = 0n;
  const validBefore = BigInt(
    Math.floor(Date.now() / 1000) + (requirements.maxTimeoutSeconds || 300)
  );
  const nonce = randomBytes32();

  const signature = await wallet.signTypedData({
    account: wallet.account!,
    domain: DOMAIN,
    types: TYPES,
    primaryType: "TransferWithAuthorization",
    message: {
      from,
      to,
      value,
      validAfter,
      validBefore,
      nonce,
    },
  });

  const paymentPayload = {
    payload: {
      signature,
      authorization: {
        from,
        to,
        value: value.toString(),
        validAfter: validAfter.toString(),
        validBefore: validBefore.toString(),
        nonce,
      },
    },
  };

  return Buffer.from(JSON.stringify(paymentPayload)).toString("base64");
}
