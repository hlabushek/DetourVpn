// SPDX-License-Identifier: Apache-2.0
package main

import (
	"errors"
	"github.com/amnezia-vpn/amneziawg-go/v3/conn"
	"github.com/amnezia-vpn/amneziawg-go/v3/tun"
	"golang.org/x/sys/unix"
	"net"
	"sync"
	"testing"
)

// TEST ONLY adapters; never compiled into the APK.
type fakeBind struct {
	conn.Bind
	sent     int
	original conn.Endpoint
	reused   bool
}

func (b *fakeBind) Send(p [][]byte, e conn.Endpoint) error {
	b.sent++
	b.reused = e == b.original
	return nil
}

type fakeTun struct {
	tun.Device
	writes int
	fail   bool
}

func (t *fakeTun) Write(p [][]byte, offset int) (int, error) {
	if t.fail {
		return 0, errors.New("TEST ONLY write failure")
	}
	t.writes += len(p)
	return len(p), nil
}
func fixture() (*authorizationGate, *int64) {
	now := int64(1000)
	g := newGate()
	g.now = func() int64 { return now }
	return g, &now
}
func TestDeadlineWithoutTimer(t *testing.T) {
	g, now := fixture()
	raw := &fakeBind{Bind: conn.NewStdNetBind()}
	b := &authorizedBind{raw, g}
	ep, _ := raw.ParseEndpoint("192.0.2.1:51820")
	if b.Send(nil, ep) == nil {
		t.Fatal("default must be closed")
	}
	if !g.setDeadline(2000) || !g.route(true) {
		t.Fatal("grant")
	}
	if b.Send(nil, ep) != nil {
		t.Fatal("valid packet")
	}
	*now = 2000 // Simulate suspension: no expiration callback ran.
	if b.Send(nil, ep) == nil || raw.sent != 1 {
		t.Fatal("expired packet sent")
	}
	if g.setDeadline(3000) {
		t.Fatal("expired native handle revived")
	}
}
func TestCancellationAndRouteFence(t *testing.T) {
	g, _ := fixture()
	g.setDeadline(2000)
	g.route(true)
	g.route(false)
	b := &authorizedBind{&fakeBind{Bind: conn.NewStdNetBind()}, g}
	ep, _ := b.ParseEndpoint("192.0.2.1:1")
	if b.Send(nil, ep) == nil {
		t.Fatal("handover bypass")
	}
	g.cancel()
	g.cancel()
	if g.setDeadline(3000) || g.route(true) {
		t.Fatal("cancel resurrected")
	}
}
func TestHealthRequiresDeliveredPayload(t *testing.T) {
	g, now := fixture()
	g.setDeadline(5000)
	g.route(true)
	raw := &fakeTun{}
	device := &observedTun{raw, g}
	if g.dataAge() != -1 {
		t.Fatal("startup is not health")
	}
	device.Write([][]byte{{0x45}}, 0)
	*now += 500
	if g.dataAge() != 500 {
		t.Fatal("delivered data not observed")
	}
	g.route(false)
	g.route(true)
	if g.dataAge() != -1 {
		t.Fatal("old route confirmed new route")
	}
	raw.fail = true
	device.Write([][]byte{{0x45}}, 0)
	if g.dataAge() != -1 {
		t.Fatal("failed delivery confirmed health")
	}
	*now = 5000
	raw.fail = false
	device.Write([][]byte{{0x45}}, 0)
	if raw.writes != 1 {
		t.Fatal("expired packet delivered")
	}
}
func TestCachedSourceIsNotReused(t *testing.T) {
	g, _ := fixture()
	g.setDeadline(2000)
	g.route(true)
	raw := &fakeBind{Bind: conn.NewStdNetBind()}
	raw.original, _ = raw.ParseEndpoint("192.0.2.1:1")
	b := &authorizedBind{raw, g}
	b.Send(nil, raw.original)
	if raw.reused {
		t.Fatal("cached source endpoint reused")
	}
}
func TestPinnedAWGSocketsAreUnconnected(t *testing.T) {
	b := conn.NewStdNetBind()
	defer b.Close()
	if _, _, err := b.Open(0); err != nil {
		t.Fatal(err)
	}
	fd, err := b.(conn.PeekLookAtSocketFd).PeekLookAtSocketFd4()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := unix.Getpeername(fd); !errors.Is(err, unix.ENOTCONN) {
		t.Fatalf("connected: %v", err)
	}
	receiver, err := net.ListenPacket("udp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer receiver.Close()
	ep, _ := b.ParseEndpoint(receiver.LocalAddr().String())
	if err := b.Send([][]byte{{1}}, ep); err != nil {
		t.Fatal(err)
	}
	if _, err := unix.Getpeername(fd); !errors.Is(err, unix.ENOTCONN) {
		t.Fatal("send connected socket")
	}
}
func TestConcurrentCancelAndObservation(t *testing.T) {
	g := newGate()
	g.setDeadline(bootMillis() + 60_000)
	g.route(true)
	var group sync.WaitGroup
	for i := 0; i < 8; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			for j := 0; j < 100; j++ {
				g.dataAge()
				g.route(false)
				g.cancel()
			}
		}()
	}
	group.Wait()
	if g.route(true) {
		t.Fatal("cancel revived")
	}
}
