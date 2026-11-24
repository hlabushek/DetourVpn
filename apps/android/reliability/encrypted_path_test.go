// SPDX-License-Identifier: Apache-2.0
package main

// Local real-AWG/UDP integration with TEST ONLY in-memory TUNs. Not Android or egress acceptance.
import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"github.com/amnezia-vpn/amneziawg-go/v3/conn"
	"github.com/amnezia-vpn/amneziawg-go/v3/device"
	"github.com/amnezia-vpn/amneziawg-go/v3/tun"
	"golang.org/x/crypto/curve25519"
	"net"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type memoryTun struct {
	outgoing  chan []byte
	delivered chan []byte
	done      chan struct{}
	events    chan tun.Event
	close     sync.Once
}

func newMemoryTun() *memoryTun {
	return &memoryTun{make(chan []byte, 4), make(chan []byte, 4), make(chan struct{}), make(chan tun.Event), sync.Once{}}
}
func (t *memoryTun) File() *os.File           { return nil }
func (t *memoryTun) MTU() (int, error)        { return 1280, nil }
func (t *memoryTun) Name() (string, error)    { return "TEST_ONLY", nil }
func (t *memoryTun) Events() <-chan tun.Event { return t.events }
func (t *memoryTun) BatchSize() int           { return 1 }
func (t *memoryTun) Close() error             { t.close.Do(func() { close(t.done); close(t.events) }); return nil }
func (t *memoryTun) Read(bufs [][]byte, sizes []int, offset int) (int, error) {
	select {
	case packet := <-t.outgoing:
		sizes[0] = copy(bufs[0][offset:], packet)
		return 1, nil
	case <-t.done:
		return 0, net.ErrClosed
	}
}
func (t *memoryTun) Write(bufs [][]byte, offset int) (int, error) {
	for _, b := range bufs {
		select {
		case t.delivered <- append([]byte(nil), b[offset:]...):
		case <-t.done:
			return 0, net.ErrClosed
		}
	}
	return len(bufs), nil
}

type portBind struct {
	conn.Bind
	port uint16
}

func (b *portBind) Open(port uint16) ([]conn.ReceiveFunc, uint16, error) {
	f, p, e := b.Bind.Open(port)
	b.port = p
	return f, p, e
}
func TestRealEncryptedDeliveryAndNativeExpiry(t *testing.T) {
	var clock atomic.Int64
	clock.Store(1000)
	left, right := newMemoryTun(), newMemoryTun()
	gl, gr := newGate(), newGate()
	gl.now = clock.Load
	gr.now = clock.Load
	gl.setDeadline(2000)
	gr.setDeadline(2000)
	gl.route(true)
	gr.route(true)
	bl, br := &portBind{Bind: conn.NewStdNetBind()}, &portBind{Bind: conn.NewStdNetBind()}
	dl := device.NewDevice(&observedTun{left, gl}, &authorizedBind{bl, gl}, device.NewLogger(device.LogLevelSilent, ""))
	dr := device.NewDevice(&observedTun{right, gr}, &authorizedBind{br, gr}, device.NewLogger(device.LogLevelSilent, ""))
	defer dl.Close()
	defer dr.Close()
	var kl, kr [32]byte
	rand.Read(kl[:])
	rand.Read(kr[:])
	pl, _ := curve25519.X25519(kl[:], curve25519.Basepoint)
	pr, _ := curve25519.X25519(kr[:], curve25519.Basepoint)
	if dl.IpcSet("private_key="+hex.EncodeToString(kl[:])+"\n") != nil || dr.IpcSet("private_key="+hex.EncodeToString(kr[:])+"\n") != nil {
		t.Fatal("test key configuration")
	}
	if dl.Up() != nil || dr.Up() != nil {
		t.Fatal("local UDP startup")
	}
	config := func(key []byte, port uint16, allowed string) string {
		return fmt.Sprintf("public_key=%s\nendpoint=127.0.0.1:%d\nallowed_ip=%s/32\n", hex.EncodeToString(key), port, allowed)
	}
	if dl.IpcSet(config(pr, br.port, "10.77.0.2")) != nil || dr.IpcSet(config(pl, bl.port, "10.77.0.1")) != nil {
		t.Fatal("test peer configuration")
	}
	packet := make([]byte, 28)
	packet[0] = 0x45
	packet[3] = 28
	packet[8] = 64
	packet[9] = 1
	copy(packet[12:16], []byte{10, 77, 0, 1})
	copy(packet[16:20], []byte{10, 77, 0, 2})
	left.outgoing <- packet
	select {
	case got := <-right.delivered:
		if string(got) != string(packet) {
			t.Fatal("data changed")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("real local AWG delivery timed out")
	}
	if gr.dataAge() != 0 {
		t.Fatal("authenticated delivered payload not recorded")
	}
	clock.Store(2000) // No callback or timer is executed to revoke the grant.
	left.outgoing <- packet
	select {
	case <-right.delivered:
		t.Fatal("expired native grant sent traffic")
	case <-time.After(200 * time.Millisecond):
	}
	if gl.setDeadline(3000) || gr.dataAge() != -1 {
		t.Fatal("expired grant or health revived")
	}
}
