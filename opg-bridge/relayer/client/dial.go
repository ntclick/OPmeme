package client

import (
	"context"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/ethclient"
	"github.com/ethereum/go-ethereum/rpc"
)

// DNSOverride pins a hostname to a specific IP for HTTP(S) dialing. This is
// needed when the local resolver refuses to resolve a host (e.g. some
// home-router DNS servers + ogevmdevnet.opengradient.ai).
type DNSOverride struct {
	Host string // e.g. "ogevmdevnet.opengradient.ai"
	IP   string // e.g. "3.142.32.45"
}

// Dial returns an *ethclient.Client whose underlying HTTP transport rewrites
// DNS lookups for any hostname listed in `overrides` to the corresponding IP.
// All other hostnames go through the system resolver as usual.
func Dial(ctx context.Context, rpcURL string, overrides ...DNSOverride) (*ethclient.Client, error) {
	dialer := &net.Dialer{
		Timeout:   30 * time.Second,
		KeepAlive: 30 * time.Second,
	}

	pinned := map[string]string{}
	for _, o := range overrides {
		pinned[strings.ToLower(o.Host)] = o.IP
	}

	dialContext := func(ctx context.Context, network, addr string) (net.Conn, error) {
		host, port, err := net.SplitHostPort(addr)
		if err == nil {
			if ip, ok := pinned[strings.ToLower(host)]; ok {
				return dialer.DialContext(ctx, network, net.JoinHostPort(ip, port))
			}
		}
		return dialer.DialContext(ctx, network, addr)
	}

	transport := &http.Transport{
		Proxy:                 http.ProxyFromEnvironment,
		DialContext:           dialContext,
		ForceAttemptHTTP2:     true,
		MaxIdleConns:          100,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   30 * time.Second,
		ExpectContinueTimeout: 1 * time.Second,
	}

	httpClient := &http.Client{
		Transport: transport,
		Timeout:   60 * time.Second,
	}

	rpcClient, err := rpc.DialOptions(ctx, rpcURL, rpc.WithHTTPClient(httpClient))
	if err != nil {
		return nil, err
	}
	return ethclient.NewClient(rpcClient), nil
}
