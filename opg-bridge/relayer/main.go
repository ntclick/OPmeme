package main

import (
	"context"
	_ "embed"
	"log"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/coincheckgo/opg-bridge/relayer/client"
	"github.com/coincheckgo/opg-bridge/relayer/config"
	"github.com/coincheckgo/opg-bridge/relayer/sender"
	"github.com/coincheckgo/opg-bridge/relayer/watcher"
)

// Pinned DNS for OG Testnet (some local resolvers refuse this hostname).
var ogDNSOverride = client.DNSOverride{
	Host: "ogevmdevnet.opengradient.ai",
	IP:   "3.142.32.45",
}

//go:embed abi/bridge_base.json
var bridgeBaseABI string

//go:embed abi/bridge_og.json
var bridgeOGABI string

//go:embed abi/eth_bridge.json
var ethBridgeABI string

func main() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)

	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("config: %v", err)
	}

	dialCtx, dialCancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer dialCancel()

	baseClient, err := client.Dial(dialCtx, cfg.BaseRPC)
	if err != nil {
		log.Fatalf("dial base sepolia: %v", err)
	}
	defer baseClient.Close()

	ogClient, err := client.Dial(dialCtx, cfg.OGRPC, ogDNSOverride)
	if err != nil {
		log.Fatalf("dial og testnet: %v", err)
	}
	defer ogClient.Close()

	senderBase, err := sender.New(baseClient, cfg.RelayerBaseKey)
	if err != nil {
		log.Fatalf("base sender: %v", err)
	}
	senderOG, err := sender.New(ogClient, cfg.RelayerOGKey)
	if err != nil {
		log.Fatalf("og sender: %v", err)
	}
	log.Printf("relayer base address: %s", senderBase.From().Hex())
	log.Printf("relayer og   address: %s", senderOG.From().Hex())

	state, err := watcher.LoadState(cfg.StateDir)
	if err != nil {
		log.Fatalf("load state: %v", err)
	}

	// Base Sepolia Locked → mint on OG (signed by senderOG)
	lockedW, err := watcher.NewLockedWatcher(
		baseClient, senderOG,
		cfg.BridgeBaseAddr, cfg.BridgeOGAddr,
		bridgeBaseABI, bridgeOGABI,
		state, cfg.PollInterval, cfg.Confirmations,
	)
	if err != nil {
		log.Fatalf("locked watcher: %v", err)
	}

	// OG Burned → release on Base Sepolia (signed by senderBase)
	burnedW, err := watcher.NewBurnedWatcher(
		ogClient, senderBase,
		cfg.BridgeOGAddr, cfg.BridgeBaseAddr,
		bridgeOGABI, bridgeBaseABI,
		state, cfg.PollInterval, cfg.Confirmations,
	)
	if err != nil {
		log.Fatalf("burned watcher: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	var wg sync.WaitGroup
	wg.Add(2)
	go func() { defer wg.Done(); lockedW.Run(ctx) }()
	go func() { defer wg.Done(); burnedW.Run(ctx) }()

	if cfg.EthBridgeEnabled() {
		// ETH lock on Base → release on OG (signed by senderOG)
		ethBaseToOG, err := watcher.NewEthBridgeWatcher(
			"eth-base→og",
			"eth_base_to_og_last_block",
			baseClient, senderOG,
			cfg.EthBridgeBaseAddr, cfg.EthBridgeOGAddr,
			ethBridgeABI,
			state, cfg.PollInterval, cfg.Confirmations,
		)
		if err != nil {
			log.Fatalf("eth base→og watcher: %v", err)
		}

		// ETH lock on OG → release on Base (signed by senderBase)
		ethOGToBase, err := watcher.NewEthBridgeWatcher(
			"eth-og→base",
			"eth_og_to_base_last_block",
			ogClient, senderBase,
			cfg.EthBridgeOGAddr, cfg.EthBridgeBaseAddr,
			ethBridgeABI,
			state, cfg.PollInterval, cfg.Confirmations,
		)
		if err != nil {
			log.Fatalf("eth og→base watcher: %v", err)
		}

		wg.Add(2)
		go func() { defer wg.Done(); ethBaseToOG.Run(ctx) }()
		go func() { defer wg.Done(); ethOGToBase.Run(ctx) }()
		log.Printf("ETH bridge watchers enabled (base=%s og=%s)",
			cfg.EthBridgeBaseAddr.Hex(), cfg.EthBridgeOGAddr.Hex())
	} else {
		log.Printf("ETH bridge watchers disabled (set ETH_BRIDGE_BASE_ADDR + ETH_BRIDGE_OG_ADDR to enable)")
	}

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	log.Printf("shutdown signal received")
	cancel()
	wg.Wait()
	log.Printf("relayer stopped")
}
