// SPDX-License-Identifier: Apache-2.0
// Local adapter policy only. Encryption and packet authentication remain upstream AWG.
package main

import (
	"errors"
	"sync"
	"sync/atomic"

	"github.com/amnezia-vpn/amneziawg-go/v3/conn"
	"github.com/amnezia-vpn/amneziawg-go/v3/tun"
	"golang.org/x/sys/unix"
)

var errUnauthorized = errors.New("authorization_inactive")

func bootMillis() int64 {
	var ts unix.Timespec
	if unix.ClockGettime(unix.CLOCK_BOOTTIME, &ts) != nil {
		return -1
	}
	return ts.Sec*1000 + ts.Nsec/1_000_000
}

// A packet gate, not an expiry timer. Works even when Java/Go timers were delayed.
// An expired or cancelled grant cannot be revived on the same native handle.
type authorizationGate struct {
	mu         sync.RWMutex
	end        int64
	cancelled  atomic.Bool
	routed     bool
	now        func() int64
	lastData   atomic.Int64
	routeEpoch atomic.Int64
}

func newGate() *authorizationGate { return &authorizationGate{now: bootMillis} }
func (g *authorizationGate) validLocked() bool {
	now := g.now()
	return !g.cancelled.Load() && now >= 0 && g.end > now
}
func (g *authorizationGate) setDeadline(end int64) bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	now := g.now()
	if g.cancelled.Load() || now < 0 || end <= now || end-now > 90_000 ||
		(g.end != 0 && (!g.validLocked() || end < g.end)) {
		return false
	}
	g.end = end
	return true
}
func (g *authorizationGate) cancel() {
	g.cancelled.Store(true)
	g.lastData.Store(0)
}
func (g *authorizationGate) route(ready bool) bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	if ready && !g.validLocked() {
		return false
	}
	g.routed = ready
	if !ready {
		g.routeEpoch.Add(1)
		g.lastData.Store(0)
	}
	return true
}
func (g *authorizationGate) dataAge() int64 {
	g.mu.RLock()
	defer g.mu.RUnlock()
	last := g.lastData.Load()
	if !g.validLocked() || !g.routed || last <= 0 {
		return -1
	}
	age := g.now() - last
	if age < 0 {
		return -1
	}
	return age
}

type authorizedBind struct {
	conn.Bind
	gate *authorizationGate
}

func (b *authorizedBind) PeekLookAtSocketFd4() (int, error) {
	return b.Bind.(conn.PeekLookAtSocketFd).PeekLookAtSocketFd4()
}
func (b *authorizedBind) PeekLookAtSocketFd6() (int, error) {
	return b.Bind.(conn.PeekLookAtSocketFd).PeekLookAtSocketFd6()
}
func (b *authorizedBind) Send(bufs [][]byte, endpoint conn.Endpoint) error {
	b.gate.mu.RLock()
	defer b.gate.mu.RUnlock()
	if !b.gate.validLocked() || !b.gate.routed {
		return errUnauthorized
	}
	// StdNetBind sockets are unconnected ListenPacket UDP sockets. Do not reuse
	// an endpoint's cached PKTINFO source/interface after Android netId changes.
	// A fresh endpoint keeps the current remote address (including AWG roaming).
	destination, err := b.Bind.ParseEndpoint(endpoint.DstToString())
	if err != nil {
		return err
	}
	return b.Bind.Send(bufs, destination)
}

type observedTun struct {
	tun.Device
	gate *authorizationGate
}

func (t *observedTun) Write(bufs [][]byte, offset int) (int, error) {
	t.gate.mu.RLock()
	defer t.gate.mu.RUnlock()
	if !t.gate.validLocked() || !t.gate.routed {
		// Consume/drop decrypted data rather than feeding unauthorized packets
		// into the OS. The outgoing encrypted path is fenced independently.
		return len(bufs), nil
	}
	n, err := t.Device.Write(bufs, offset)
	if n > 0 && err == nil {
		t.gate.lastData.Store(t.gate.now())
	}
	return n, err
}
