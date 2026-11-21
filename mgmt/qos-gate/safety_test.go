package main

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func safetyGate(t *testing.T) (*Gate, *GateCtl) {
	t.Helper()
	d := t.TempDir()
	cfgPath = filepath.Join(d, "cfg.json")
	writeCfg(t, cfgPath, "127.0.0.1:0", "127.0.0.1:0", "127.0.0.1:0", "127.0.0.1:7894", 60000, d)
	g := NewGate(mustCfg(t, cfgPath))
	t.Cleanup(g.Close)
	return g, NewGateCtl(g)
}

func TestEmergencyPersistsAcrossParameterChangesAndRestart(t *testing.T) {
	g, c := safetyGate(t)
	c.dispatch(ctlReq{Cmd: "release"})
	generation := c.gen
	c.dispatch(ctlReq{Cmd: "set-rate", Class: "video", DownBPS: 100000})
	if r := c.dispatch(ctlReq{Cmd: "reload"}); r["ok"] != true {
		t.Fatal(r)
	}
	if !g.video.Stats()["emergency"].(bool) {
		t.Fatal("parameter changes cleared emergency")
	}
	g2 := NewGate(g.config())
	defer g2.Close()
	c2 := NewGateCtl(g2)
	if c2.gen != generation || !g2.video.Stats()["unlimited"].(bool) {
		t.Fatal("restart lost persistent lock/generation")
	}
	stale := generation - 1
	if r := c2.dispatch(ctlReq{Cmd: "clear-emergency", ExpectedGeneration: &stale}); r["ok"] != false {
		t.Fatal("stale resume accepted")
	}
	if r := c2.dispatch(ctlReq{Cmd: "clear-emergency"}); r["ok"] != false {
		t.Fatal("unversioned resume accepted")
	}
	if r := c2.dispatch(ctlReq{Cmd: "clear-emergency", ExpectedGeneration: &generation}); r["ok"] != true {
		t.Fatal(r)
	}
	if g2.video.Stats()["emergency"].(bool) || c2.gen <= generation {
		t.Fatal("explicit resume failed")
	}
}

func TestSingleClassReleaseScopeSurvivesRestart(t *testing.T) {
	g, c := safetyGate(t)
	c.dispatch(ctlReq{Cmd: "release-class", Class: "video"})
	g2 := NewGate(g.config())
	defer g2.Close()
	if !g2.video.Stats()["emergency"].(bool) || g2.dl.Stats()["emergency"].(bool) {
		t.Fatal("class scope changed on restart")
	}
	c.dispatch(ctlReq{Cmd: "release"})
	gen := c.gen
	if r := c.dispatch(ctlReq{Cmd: "clear-emergency", Class: "video", ExpectedGeneration: &gen}); r["ok"] != true {
		t.Fatal(r)
	}
	g3 := NewGate(g.config())
	defer g3.Close()
	if g3.video.Stats()["emergency"].(bool) || !g3.dl.Stats()["emergency"].(bool) {
		t.Fatal("single class resume changed another class")
	}
}

func TestConcurrentControlGenerationIsMonotonic(t *testing.T) {
	g, c := safetyGate(t)
	var wg sync.WaitGroup
	for i := 0; i < 24; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); c.dispatch(ctlReq{Cmd: "release"}); c.dispatch(ctlReq{Cmd: "status"}) }()
	}
	wg.Wait()
	if st := LoadState(g.config().StateFile); st.Generation != 25 {
		t.Fatalf("generation=%d want25", st.Generation)
	}
}

func TestReloadThenResumeUsesNewConfig(t *testing.T) {
	g, c := safetyGate(t)
	c.dispatch(ctlReq{Cmd: "release"})
	cfg := g.config()
	cfg.Classes.Video.DownBPS = 100000
	raw, _ := json.Marshal(cfg)
	os.WriteFile(cfgPath, raw, 0600)
	c.dispatch(ctlReq{Cmd: "reload"})
	gen := c.gen
	if r := c.dispatch(ctlReq{Cmd: "clear-emergency", ExpectedGeneration: &gen}); r["ok"] != true {
		t.Fatal(r)
	}
	if g.video.Stats()["down_bps"] != float64(100000) {
		t.Fatal("resume reverted to stale config")
	}
}

func TestMissingAndMalformedStateFailOpen(t *testing.T) {
	g, _ := safetyGate(t)
	for _, raw := range []string{"{broken", `{"generation":7}`, `{"generation":0,"emergency":false}`} {
		os.WriteFile(g.config().StateFile, []byte(raw), 0600)
		g2 := NewGate(g.config())
		if !g2.video.Stats()["unlimited"].(bool) || g2.state.LoadError == "" {
			t.Fatal("invalid state enabled cap")
		}
		g2.Close()
	}
	os.Remove(g.config().StateFile)
	g2 := NewGate(g.config())
	defer g2.Close()
	if !g2.video.Stats()["unlimited"].(bool) {
		t.Fatal("missing state enabled cap")
	}
}

func TestCancelledWaitAndStopWake(t *testing.T) {
	b := NewBucket("cancel", 8, 100)
	defer b.Stop()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { _, err := b.TakeContext(ctx, 100); done <- err }()
	time.Sleep(40 * time.Millisecond)
	cancel()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("cancel returned success")
		}
	case <-time.After(time.Second):
		t.Fatal("cancel leaked waiter")
	}
	done = make(chan error, 1)
	go func() { _, err := b.TakeContext(context.Background(), 100); done <- err }()
	time.Sleep(40 * time.Millisecond)
	b.Stop()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("Stop leaked waiter")
	}
}

func tcpPair(t *testing.T) (*net.TCPConn, *net.TCPConn) {
	t.Helper()
	ln, err := net.ListenTCP("tcp", &net.TCPAddr{IP: net.ParseIP("127.0.0.1")})
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	a, err := net.DialTCP("tcp", nil, ln.Addr().(*net.TCPAddr))
	if err != nil {
		t.Fatal(err)
	}
	b, err := ln.AcceptTCP()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { a.Close(); b.Close() })
	return a, b
}

func TestHalfCloseAllowsResponse(t *testing.T) {
	peer, client := tcpPair(t)
	up, server := tcpPair(t)
	bucket := NewBucket("halfclose", 0, 0)
	defer bucket.Stop()
	done := make(chan struct{})
	go func() { pipeConn(client, up, bucket, &ClassStats{}); close(done) }()
	go func() {
		io.ReadAll(server)
		time.Sleep(30 * time.Millisecond)
		server.Write([]byte("response"))
		server.CloseWrite()
	}()
	peer.Write([]byte("request"))
	peer.CloseWrite()
	peer.SetReadDeadline(time.Now().Add(time.Second))
	raw, err := io.ReadAll(peer)
	if err != nil || string(raw) != "response" {
		t.Fatalf("response=%q err=%v", raw, err)
	}
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("pipe did not finish")
	}
}
