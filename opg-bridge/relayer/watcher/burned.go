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

const stateKeyBurned = "og_burned_last_block"

// BurnedWatcher polls Burned events on OG Testnet and releases OPG on Base Sepolia.
type BurnedWatcher struct {
	OGClient     *ethclient.Client
	BaseSender   *sender.TxSender
	BridgeOG     common.Address
	BridgeBase   common.Address
	OGABI        abi.ABI
	BaseABI      abi.ABI
	State        *State
	PollInterval time.Duration
	Confirms     uint64
}

func NewBurnedWatcher(
	ogClient *ethclient.Client,
	baseSender *sender.TxSender,
	bridgeOG, bridgeBase common.Address,
	ogABIJSON, baseABIJSON string,
	state *State,
	pollInterval time.Duration,
	confirms uint64,
) (*BurnedWatcher, error) {
	oABI, err := abi.JSON(strings.NewReader(ogABIJSON))
	if err != nil {
		return nil, err
	}
	bABI, err := abi.JSON(strings.NewReader(baseABIJSON))
	if err != nil {
		return nil, err
	}
	return &BurnedWatcher{
		OGClient:     ogClient,
		BaseSender:   baseSender,
		BridgeOG:     bridgeOG,
		BridgeBase:   bridgeBase,
		OGABI:        oABI,
		BaseABI:      bABI,
		State:        state,
		PollInterval: pollInterval,
		Confirms:     confirms,
	}, nil
}

func (w *BurnedWatcher) Run(ctx context.Context) {
	log.Printf("[burned] watcher started, polling %s", w.PollInterval)
	burnedSig := w.OGABI.Events["Burned"].ID

	for {
		select {
		case <-ctx.Done():
			log.Printf("[burned] watcher stopped")
			return
		default:
		}

		head, err := w.OGClient.BlockNumber(ctx)
		if err != nil {
			log.Printf("[burned] head error: %v", err)
			time.Sleep(w.PollInterval)
			continue
		}
		if head <= w.Confirms {
			time.Sleep(w.PollInterval)
			continue
		}
		safe := head - w.Confirms

		from := w.State.Get(stateKeyBurned)
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
			Addresses: []common.Address{w.BridgeOG},
			Topics:    [][]common.Hash{{burnedSig}},
		}
		logs, err := w.OGClient.FilterLogs(ctx, query)
		if err != nil {
			log.Printf("[burned] filter logs error: %v", err)
			time.Sleep(w.PollInterval)
			continue
		}

		for _, l := range logs {
			if err := w.handle(ctx, l, burnedSig); err != nil {
				log.Printf("[burned] handle error: %v", err)
			}
		}

		if err := w.State.Set(stateKeyBurned, safe+1); err != nil {
			log.Printf("[burned] save state: %v", err)
		}
		time.Sleep(w.PollInterval)
	}
}

func (w *BurnedWatcher) handle(ctx context.Context, l types.Log, burnedSig common.Hash) error {
	if len(l.Topics) < 2 || l.Topics[0] != burnedSig {
		return nil
	}
	from := common.BytesToAddress(l.Topics[1].Bytes())

	values, err := w.OGABI.Events["Burned"].Inputs.NonIndexed().Unpack(l.Data)
	if err != nil {
		return err
	}
	amount := values[0].(*big.Int)
	nonce := values[1].(*big.Int)

	log.Printf("[burned] %s amount=%s nonce=%s -> releasing OPG", from.Hex(), amount.String(), nonce.String())

	_, err = w.BaseSender.SendCall(ctx, w.BridgeBase, w.BaseABI, "release", from, amount, nonce)
	return err
}
