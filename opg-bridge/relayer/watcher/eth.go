package watcher

import (
	"context"
	"log"
	"math/big"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/ethclient"

	"github.com/coincheckgo/opg-bridge/relayer/sender"
)

// EthBridgeWatcher polls LockedETH events on a source ETHBridge and calls
// release(...) on the destination ETHBridge. The same struct handles both
// directions — instantiate two of them with swapped arguments.
type EthBridgeWatcher struct {
	Name         string // for logs, e.g. "eth-base→og"
	StateKey     string // unique key in state.json
	SourceClient *ethclient.Client
	DestSender   *sender.TxSender
	SourceBridge common.Address
	DestBridge   common.Address
	BridgeABI    abi.ABI
	State        *State
	PollInterval time.Duration
	Confirms     uint64
}

func NewEthBridgeWatcher(
	name string,
	stateKey string,
	sourceClient *ethclient.Client,
	destSender *sender.TxSender,
	sourceBridge common.Address,
	destBridge common.Address,
	bridgeABIJSON string,
	state *State,
	pollInterval time.Duration,
	confirms uint64,
) (*EthBridgeWatcher, error) {
	parsed, err := abi.JSON(strings.NewReader(bridgeABIJSON))
	if err != nil {
		return nil, err
	}
	return &EthBridgeWatcher{
		Name:         name,
		StateKey:     stateKey,
		SourceClient: sourceClient,
		DestSender:   destSender,
		SourceBridge: sourceBridge,
		DestBridge:   destBridge,
		BridgeABI:    parsed,
		State:        state,
		PollInterval: pollInterval,
		Confirms:     confirms,
	}, nil
}

func (w *EthBridgeWatcher) Run(ctx context.Context) {
	log.Printf("[%s] watcher started, polling %s", w.Name, w.PollInterval)
	lockedSig := w.BridgeABI.Events["LockedETH"].ID

	for {
		select {
		case <-ctx.Done():
			log.Printf("[%s] watcher stopped", w.Name)
			return
		default:
		}

		head, err := w.SourceClient.BlockNumber(ctx)
		if err != nil {
			log.Printf("[%s] head error: %v", w.Name, err)
			time.Sleep(w.PollInterval)
			continue
		}
		if head <= w.Confirms {
			time.Sleep(w.PollInterval)
			continue
		}
		safe := head - w.Confirms

		from := w.State.Get(w.StateKey)
		if from == 0 {
			from = safe
		}
		if from > safe {
			time.Sleep(w.PollInterval)
			continue
		}

		query := ethereum.FilterQuery{
			FromBlock: new(big.Int).SetUint64(from),
			ToBlock:   new(big.Int).SetUint64(safe),
			Addresses: []common.Address{w.SourceBridge},
			Topics:    [][]common.Hash{{lockedSig}},
		}
		logs, err := w.SourceClient.FilterLogs(ctx, query)
		if err != nil {
			log.Printf("[%s] filter logs error: %v", w.Name, err)
			time.Sleep(w.PollInterval)
			continue
		}

		for _, l := range logs {
			if err := w.handle(ctx, l, lockedSig); err != nil {
				log.Printf("[%s] handle error: %v", w.Name, err)
			}
		}

		if err := w.State.Set(w.StateKey, safe+1); err != nil {
			log.Printf("[%s] save state: %v", w.Name, err)
		}
		time.Sleep(w.PollInterval)
	}
}

func (w *EthBridgeWatcher) handle(ctx context.Context, l types.Log, lockedSig common.Hash) error {
	if len(l.Topics) < 2 || l.Topics[0] != lockedSig {
		return nil
	}
	from := common.BytesToAddress(l.Topics[1].Bytes())

	values, err := w.BridgeABI.Events["LockedETH"].Inputs.NonIndexed().Unpack(l.Data)
	if err != nil {
		return err
	}
	amount := values[0].(*big.Int)
	nonce := values[1].(*big.Int)

	log.Printf("[%s] %s amount=%s nonce=%s -> releasing on dest",
		w.Name, from.Hex(), amount.String(), nonce.String())

	_, err = w.DestSender.SendCall(ctx, w.DestBridge, w.BridgeABI, "release", from, amount, nonce)
	return err
}
