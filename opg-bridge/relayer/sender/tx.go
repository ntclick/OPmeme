package sender

import (
	"context"
	"crypto/ecdsa"
	"fmt"
	"log"
	"math/big"
	"sync"
	"time"

	"github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/accounts/abi/bind"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/ethclient"
)

const maxRetries = 3

// TxSender wraps an ethclient with a nonce manager + retry logic.
type TxSender struct {
	client  *ethclient.Client
	chainID *big.Int
	privKey *ecdsa.PrivateKey
	from    common.Address

	mu    sync.Mutex
	nonce uint64
}

func New(client *ethclient.Client, privHex string) (*TxSender, error) {
	pk, err := crypto.HexToECDSA(privHex)
	if err != nil {
		return nil, fmt.Errorf("invalid private key: %w", err)
	}
	pub := pk.Public().(*ecdsa.PublicKey)
	from := crypto.PubkeyToAddress(*pub)

	chainID, err := client.ChainID(context.Background())
	if err != nil {
		return nil, fmt.Errorf("chain id: %w", err)
	}

	pending, err := client.PendingNonceAt(context.Background(), from)
	if err != nil {
		return nil, fmt.Errorf("pending nonce: %w", err)
	}

	return &TxSender{
		client:  client,
		chainID: chainID,
		privKey: pk,
		from:    from,
		nonce:   pending,
	}, nil
}

func (s *TxSender) From() common.Address { return s.from }

// SendCall packs `method(args...)` from `parsedABI`, signs it with the manager's
// nonce/gas estimate (+20% buffer), and waits for inclusion. Retries up to maxRetries.
func (s *TxSender) SendCall(
	ctx context.Context,
	to common.Address,
	parsedABI abi.ABI,
	method string,
	args ...interface{},
) (*types.Receipt, error) {
	data, err := parsedABI.Pack(method, args...)
	if err != nil {
		return nil, fmt.Errorf("pack %s: %w", method, err)
	}

	var lastErr error
	for attempt := 1; attempt <= maxRetries; attempt++ {
		receipt, err := s.sendOnce(ctx, to, data)
		if err == nil {
			return receipt, nil
		}
		lastErr = err
		log.Printf("[sender] %s attempt %d/%d failed: %v", method, attempt, maxRetries, err)

		// Refresh nonce in case of nonce drift before retrying.
		if pending, perr := s.client.PendingNonceAt(ctx, s.from); perr == nil {
			s.mu.Lock()
			s.nonce = pending
			s.mu.Unlock()
		}
		time.Sleep(time.Duration(attempt) * 2 * time.Second)
	}
	return nil, fmt.Errorf("send %s after %d retries: %w", method, maxRetries, lastErr)
}

func (s *TxSender) sendOnce(ctx context.Context, to common.Address, data []byte) (*types.Receipt, error) {
	s.mu.Lock()
	nonce := s.nonce
	s.nonce++
	s.mu.Unlock()

	gasPrice, err := s.client.SuggestGasPrice(ctx)
	if err != nil {
		return nil, fmt.Errorf("gas price: %w", err)
	}

	gasLimit, err := s.client.EstimateGas(ctx, ethereum.CallMsg{
		From: s.from,
		To:   &to,
		Data: data,
	})
	if err != nil {
		return nil, fmt.Errorf("estimate gas: %w", err)
	}
	// +20% buffer
	gasLimit = gasLimit * 120 / 100

	tx := types.NewTx(&types.LegacyTx{
		Nonce:    nonce,
		To:       &to,
		Value:    big.NewInt(0),
		Gas:      gasLimit,
		GasPrice: gasPrice,
		Data:     data,
	})

	signedTx, err := types.SignTx(tx, types.NewEIP155Signer(s.chainID), s.privKey)
	if err != nil {
		return nil, fmt.Errorf("sign tx: %w", err)
	}

	if err := s.client.SendTransaction(ctx, signedTx); err != nil {
		return nil, fmt.Errorf("send tx: %w", err)
	}
	log.Printf("[sender] submitted tx %s (nonce=%d)", signedTx.Hash().Hex(), nonce)

	receipt, err := bind.WaitMined(ctx, s.client, signedTx)
	if err != nil {
		return nil, fmt.Errorf("wait mined: %w", err)
	}
	if receipt.Status != types.ReceiptStatusSuccessful {
		return receipt, fmt.Errorf("tx reverted: %s", signedTx.Hash().Hex())
	}
	log.Printf("[sender] mined tx %s in block %d", signedTx.Hash().Hex(), receipt.BlockNumber.Uint64())
	return receipt, nil
}
