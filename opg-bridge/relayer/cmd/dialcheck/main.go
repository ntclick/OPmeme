// Tiny dialer smoke test: confirm both RPC endpoints are reachable.
// Run: go run ./cmd/dialcheck
package main

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/coincheckgo/opg-bridge/relayer/client"
	"github.com/coincheckgo/opg-bridge/relayer/config"
)

func main() {
	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("config: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	baseClient, err := client.Dial(ctx, cfg.BaseRPC)
	if err != nil {
		log.Fatalf("dial base: %v", err)
	}
	defer baseClient.Close()
	bid, err := baseClient.ChainID(ctx)
	if err != nil {
		log.Fatalf("base chain id: %v", err)
	}
	fmt.Println("base chain id:", bid.String())
	bn, _ := baseClient.BlockNumber(ctx)
	fmt.Println("base block   :", bn)

	ogClient, err := client.Dial(ctx, cfg.OGRPC, client.DNSOverride{
		Host: "ogevmdevnet.opengradient.ai",
		IP:   "3.142.32.45",
	})
	if err != nil {
		log.Fatalf("dial og: %v", err)
	}
	defer ogClient.Close()
	oid, err := ogClient.ChainID(ctx)
	if err != nil {
		log.Fatalf("og chain id: %v", err)
	}
	fmt.Println("og chain id  :", oid.String())
	on, _ := ogClient.BlockNumber(ctx)
	fmt.Println("og block     :", on)
}
