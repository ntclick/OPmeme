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

const stateKeyLocked = "base_locked_last_block"

// LockedWatcher polls Locked events on Base Sepolia and mints wOPG on OG Testnet.
type LockedWatcher struct {
	BaseClient   *ethclient.Client
	OGSender     *sender.TxSender
	BridgeBase   common.Address
	BridgeOG     common.Address
	BaseABI      abi.ABI
	OGABI        abi.ABI
	State        *State
	PollInterval time.Duration
	Confirms     uint64
}

func NewLockedWatcher(
	baseClient *ethclient.Client,
	ogSender *sender.TxSender,
	bridgeBase, bridgeOG common.Address,
	baseABIJSON, ogABIJSON string,
	state *State,
	pollInterval time.Duration,
	confirms uint64,
) (*LockedWatcher, error) {
	bABI, err := abi.JSON(strings.NewReader(baseABIJSON))
	if err != nil {
		return nil, err
	}
	oABI, err := abi.JSON(strings.NewReader(ogABIJSON))
	if err != nil {
		return nil, err
	}
	return &LockedWatcher{
		BaseClient:   baseClient,
		OGSender:     ogSender,
		BridgeBase:   bridgeBase,
		BridgeOG:     bridgeOG,
		BaseABI:      bABI,
		OGABI:        oABI,
		State:        state,
		PollInterval: pollInterval,
		Confirms:     confirms,
	}, nil
}

func (w *LockedWatcher) Run(ctx context.Context) {
	log.Printf("[locked] watcher started, polling %s", w.PollInterval)
	lockedSig := w.BaseABI.Events["Locked"].ID

	for {
		select {
		case <-ctx.Done():
			log.Printf("[locked] watcher stopped")
			return
		default:
		}

		head, err := w.BaseClient.BlockNumber(ctx)
		if err != nil {
			log.Printf("[locked] head error: %v", err)
			time.Sleep(w.PollInterval)
			continue
		}
		if head <= w.Confirms {
			time.Sleep(w.PollInterval)
			continue
		}
		safe := head - w.Confirms

		from := w.State.Get(stateKeyLocked)
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
			Addresses: []common.Address{w.BridgeBase},
			Topics:    [][]common.Hash{{lockedSig}},
		}
		logs, err := w.BaseClient.FilterLogs(ctx, query)
		if err != nil {
			log.Printf("[locked] filter logs error: %v", err)
			time.Sleep(w.PollInterval)
			continue
		}

		for _, l := range logs {
			if err := w.handle(ctx, l, lockedSig); err != nil {
				log.Printf("[locked] handle error: %v", err)
			}
		}

		if err := w.State.Set(stateKeyLocked, safe+1); err != nil {
			log.Printf("[locked] save state: %v", err)
		}
		time.Sleep(w.PollInterval)
	}
}

func (w *LockedWatcher) handle(ctx context.Context, l types.Log, lockedSig common.Hash) error {
	if len(l.Topics) < 2 || l.Topics[0] != lockedSig {
		return nil
	}
	from := common.BytesToAddress(l.Topics[1].Bytes())

	// non-indexed: amount, nonce
	values, err := w.BaseABI.Events["Locked"].Inputs.NonIndexed().Unpack(l.Data)
	if err != nil {
		return err
	}
	amount := values[0].(*big.Int)
	nonce := values[1].(*big.Int)

	log.Printf("[locked] %s amount=%s nonce=%s -> minting wOPG", from.Hex(), amount.String(), nonce.String())

	_, err = w.OGSender.SendCall(ctx, w.BridgeOG, w.OGABI, "mint", from, amount, nonce)
	return err
}
