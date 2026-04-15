export const OPG_TOKEN = "0x240b09731D96979f50B2C649C9CE10FcF9C7987F" as const;

export const BRIDGE_BASE_ADDR = (process.env.NEXT_PUBLIC_BRIDGE_BASE_ADDR ??
  "0x0000000000000000000000000000000000000000") as `0x${string}`;
export const BRIDGE_OG_ADDR = (process.env.NEXT_PUBLIC_BRIDGE_OG_ADDR ??
  "0x0000000000000000000000000000000000000000") as `0x${string}`;
export const WOPG_ADDR = (process.env.NEXT_PUBLIC_WOPG_ADDR ??
  "0x0000000000000000000000000000000000000000") as `0x${string}`;

export const ETH_BRIDGE_BASE_ADDR = (process.env.NEXT_PUBLIC_ETH_BRIDGE_BASE_ADDR ??
  "0x0000000000000000000000000000000000000000") as `0x${string}`;
export const ETH_BRIDGE_OG_ADDR = (process.env.NEXT_PUBLIC_ETH_BRIDGE_OG_ADDR ??
  "0x0000000000000000000000000000000000000000") as `0x${string}`;

export const ERC20_ABI = [
  {
    type: "function",
    name: "approve",
    stateMutability: "nonpayable",
    inputs: [
      { name: "spender", type: "address" },
      { name: "amount", type: "uint256" },
    ],
    outputs: [{ type: "bool" }],
  },
  {
    type: "function",
    name: "allowance",
    stateMutability: "view",
    inputs: [
      { name: "owner", type: "address" },
      { name: "spender", type: "address" },
    ],
    outputs: [{ type: "uint256" }],
  },
  {
    type: "function",
    name: "balanceOf",
    stateMutability: "view",
    inputs: [{ name: "account", type: "address" }],
    outputs: [{ type: "uint256" }],
  },
  {
    type: "function",
    name: "decimals",
    stateMutability: "view",
    inputs: [],
    outputs: [{ type: "uint8" }],
  },
] as const;

export const BRIDGE_BASE_ABI = [
  {
    type: "function",
    name: "lock",
    stateMutability: "nonpayable",
    inputs: [{ name: "amount", type: "uint256" }],
    outputs: [],
  },
  {
    type: "event",
    name: "Locked",
    inputs: [
      { name: "from", type: "address", indexed: true },
      { name: "amount", type: "uint256", indexed: false },
      { name: "nonce", type: "uint256", indexed: false },
    ],
  },
  {
    type: "event",
    name: "Released",
    inputs: [
      { name: "to", type: "address", indexed: true },
      { name: "amount", type: "uint256", indexed: false },
      { name: "nonce", type: "uint256", indexed: false },
    ],
  },
] as const;

export const BRIDGE_OG_ABI = [
  {
    type: "function",
    name: "burn",
    stateMutability: "nonpayable",
    inputs: [{ name: "amount", type: "uint256" }],
    outputs: [],
  },
  {
    type: "event",
    name: "Burned",
    inputs: [
      { name: "from", type: "address", indexed: true },
      { name: "amount", type: "uint256", indexed: false },
      { name: "nonce", type: "uint256", indexed: false },
    ],
  },
  {
    type: "event",
    name: "Minted",
    inputs: [
      { name: "to", type: "address", indexed: true },
      { name: "amount", type: "uint256", indexed: false },
      { name: "nonce", type: "uint256", indexed: false },
    ],
  },
] as const;

// ETHBridge — same ABI on both chains.
export const ETH_BRIDGE_ABI = [
  {
    type: "function",
    name: "lock",
    stateMutability: "payable",
    inputs: [],
    outputs: [],
  },
  {
    type: "event",
    name: "LockedETH",
    inputs: [
      { name: "from", type: "address", indexed: true },
      { name: "amount", type: "uint256", indexed: false },
      { name: "nonce", type: "uint256", indexed: false },
    ],
  },
  {
    type: "event",
    name: "ReleasedETH",
    inputs: [
      { name: "to", type: "address", indexed: true },
      { name: "amount", type: "uint256", indexed: false },
      { name: "nonce", type: "uint256", indexed: false },
    ],
  },
] as const;
