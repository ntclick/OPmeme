package config

import (
	"fmt"
	"os"
	"strconv"
	"time"

	"github.com/ethereum/go-ethereum/common"
	"github.com/joho/godotenv"
)

type Config struct {
	BaseRPC           string
	OGRPC             string
	BridgeBaseAddr    common.Address
	BridgeOGAddr      common.Address
	EthBridgeBaseAddr common.Address // optional — empty disables ETH watchers
	EthBridgeOGAddr   common.Address // optional — empty disables ETH watchers
	RelayerBaseKey    string         // hex without 0x prefix — signs tx on Base Sepolia (84532)
	RelayerOGKey      string         // hex without 0x prefix — signs tx on OG Testnet (10740)
	PollInterval      time.Duration
	Confirmations     uint64
	BaseChainID       int64
	OGChainID         int64
	StateDir          string
}

// EthBridgeEnabled returns true when both ETH bridge addresses are set.
func (c *Config) EthBridgeEnabled() bool {
	zero := common.Address{}
	return c.EthBridgeBaseAddr != zero && c.EthBridgeOGAddr != zero
}

func Load() (*Config, error) {
	_ = godotenv.Load()

	cfg := &Config{
		BaseRPC:       getenv("BASE_SEPOLIA_RPC", "https://sepolia.base.org"),
		OGRPC:         getenv("OG_TESTNET_RPC", "https://ogevmdevnet.opengradient.ai"),
		BaseChainID:   84532,
		OGChainID:     10740,
		Confirmations: uint64(getenvInt("CONFIRMATIONS", 2)),
		StateDir:      getenv("STATE_DIR", "./data"),
	}

	bridgeBase := os.Getenv("BRIDGE_BASE_ADDR")
	if bridgeBase == "" {
		return nil, fmt.Errorf("BRIDGE_BASE_ADDR is required")
	}
	cfg.BridgeBaseAddr = common.HexToAddress(bridgeBase)

	bridgeOG := os.Getenv("BRIDGE_OG_ADDR")
	if bridgeOG == "" {
		return nil, fmt.Errorf("BRIDGE_OG_ADDR is required")
	}
	cfg.BridgeOGAddr = common.HexToAddress(bridgeOG)

	if v := os.Getenv("ETH_BRIDGE_BASE_ADDR"); v != "" {
		cfg.EthBridgeBaseAddr = common.HexToAddress(v)
	}
	if v := os.Getenv("ETH_BRIDGE_OG_ADDR"); v != "" {
		cfg.EthBridgeOGAddr = common.HexToAddress(v)
	}

	baseKey := os.Getenv("RELAYER_BASE_PRIVATE_KEY")
	if baseKey == "" {
		return nil, fmt.Errorf("RELAYER_BASE_PRIVATE_KEY is required")
	}
	cfg.RelayerBaseKey = stripHexPrefix(baseKey)

	ogKey := os.Getenv("RELAYER_OG_PRIVATE_KEY")
	if ogKey == "" {
		return nil, fmt.Errorf("RELAYER_OG_PRIVATE_KEY is required")
	}
	cfg.RelayerOGKey = stripHexPrefix(ogKey)

	pollMs := getenvInt("POLL_INTERVAL_MS", 3000)
	cfg.PollInterval = time.Duration(pollMs) * time.Millisecond

	return cfg, nil
}

func stripHexPrefix(s string) string {
	if len(s) > 2 && (s[:2] == "0x" || s[:2] == "0X") {
		return s[2:]
	}
	return s
}

func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func getenvInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}
