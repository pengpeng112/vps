// ctl.go: Unix socket 控制口（0600，不监听 TCP，Q3.2.2）。JSON 行协议：
//
//	{"cmd":"status"}
//	{"cmd":"reload"}                                            → 热重载配置
//	{"cmd":"release"}                                           → 紧急解除（先持久化再解除，Q4.3）
//	{"cmd":"release-class","class":"video"}
//	{"cmd":"clear-emergency"}                                   → 恢复限速（新代际）
//	{"cmd":"set-rate","class":"video","down_bps":60000}
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"strings"
	"sync"
	"time"
)

type ctlReq struct {
	Cmd                string `json:"cmd"`
	Class              string `json:"class"`
	DownBPS            int64  `json:"down_bps"`
	ExpectedGeneration *int64 `json:"expected_generation,omitempty"`
}

type GateCtl struct {
	g    *Gate
	sock string
	ln   net.Listener
	gen  int64
	mu   sync.Mutex
}

func NewGateCtl(g *Gate) *GateCtl {
	g.ctlMu.Lock()
	defer g.ctlMu.Unlock()
	if g.ctl != nil {
		return g.ctl
	}
	gen := g.state.Generation
	if gen < 1 {
		gen = 1
	}
	g.ctl = &GateCtl{g: g, sock: g.config().CtlSock, gen: gen}
	return g.ctl
}

func (c *GateCtl) Start() error {
	if info, err := os.Lstat(c.sock); err == nil {
		if info.Mode()&os.ModeSocket == 0 {
			return fmt.Errorf("control path exists and is not a socket")
		}
		if conn, err := net.DialTimeout("unix", c.sock, 200*time.Millisecond); err == nil {
			conn.Close()
			return fmt.Errorf("control socket already active")
		}
		if err := os.Remove(c.sock); err != nil {
			return err
		}
	}
	ln, err := net.Listen("unix", c.sock)
	if err != nil {
		return err
	}
	c.ln = ln
	if err := os.Chmod(c.sock, 0o600); err != nil {
		return err
	}
	go c.acceptLoop()
	return nil
}

func (c *GateCtl) acceptLoop() {
	for {
		conn, err := c.ln.Accept()
		if err != nil {
			return
		}
		go c.handleConn(conn)
	}
}

func (c *GateCtl) handleConn(conn net.Conn) {
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(10 * time.Second))
	sc := bufio.NewScanner(conn)
	sc.Buffer(make([]byte, 8192), 8192)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var req ctlReq
		if err := json.Unmarshal([]byte(line), &req); err != nil {
			reply(conn, map[string]any{"ok": false, "error": "bad json: " + err.Error()})
			return
		}
		resp := c.dispatch(req)
		if !reply(conn, resp) {
			return
		}
		if req.Cmd == "release" || req.Cmd == "reload" {
			return // 单发命令
		}
	}
}

func reply(conn net.Conn, m map[string]any) bool {
	b, _ := json.Marshal(m)
	_, err := conn.Write(append(b, '\n'))
	return err == nil
}

func (c *GateCtl) dispatch(req ctlReq) map[string]any {
	c.mu.Lock()
	defer c.mu.Unlock()
	if req.ExpectedGeneration != nil && *req.ExpectedGeneration != c.gen {
		return map[string]any{"ok": false, "error": "stale generation", "generation": c.gen}
	}
	switch req.Cmd {
	case "status":
		return map[string]any{"ok": true, "video": c.g.video.Stats(), "foreign_download": c.g.dl.Stats(),
			"elephant": c.g.elephant.Stats(),
			"stats": map[string]any{
				"video":            map[string]uint64{"down": c.g.stats.video.Down.Load(), "up": c.g.stats.video.Up.Load()},
				"foreign_download": map[string]uint64{"down": c.g.stats.dl.Down.Load(), "up": c.g.stats.dl.Up.Load()},
				"elephant":         map[string]uint64{"down": c.g.stats.elephant.Down.Load(), "up": c.g.stats.elephant.Up.Load()},
			},
			"generation": c.gen, "state_error": c.g.state.LoadError, "tcp_only": true, "elephant_implemented": false}
	case "reload":
		cfg, err := LoadConfig(cfgPath)
		if err != nil {
			return map[string]any{"ok": false, "error": err.Error()}
		}
		old := c.g.config()
		if cfg.StateFile != old.StateFile || cfg.CtlSock != old.CtlSock ||
			cfg.ListenVideo != old.ListenVideo || cfg.ListenDL != old.ListenDL ||
			cfg.ListenElephant != old.ListenElephant || cfg.Upstream != old.Upstream {
			return map[string]any{"ok": false, "error": "reload 仅支持速率/elephant_mb 变更；拓扑变更需重启"}
		}
		c.g.cfgMu.Lock()
		c.g.cfg = cfg
		c.g.cfgMu.Unlock()
		// Parameter refresh never clears a human emergency lock.
		c.g.video.SetRate(cfg.Classes.Video.DownBPS, cfg.Classes.Video.Burst)
		c.g.dl.SetRate(cfg.Classes.DL.DownBPS, cfg.Classes.DL.Burst)
		c.g.elephant.SetRate(0, 0)
		return map[string]any{"ok": true, "reloaded": true,
			"rates": map[string]int64{"video": cfg.Classes.Video.DownBPS, "foreign_download": cfg.Classes.DL.DownBPS}}
	case "release":
		// Q4.3 顺序：先原子持久化 emergency（新代际），再解除并验证
		st := GateState{Emergency: true, Since: time.Now().Format(time.RFC3339), Generation: c.gen + 1}
		if err := SaveState(c.g.config().StateFile, st); err != nil {
			return map[string]any{"ok": false, "error": "persist before release FAILED: " + err.Error()}
		}
		c.gen = st.Generation
		c.g.state = st
		c.g.video.EmergencyRelease()
		c.g.dl.EmergencyRelease()
		c.g.elephant.EmergencyRelease()
		return map[string]any{"ok": true, "released": "all", "generation": c.gen, "persisted": true}
	case "release-class":
		b := c.g.bucketFor(req.Class)
		if b == nil {
			return map[string]any{"ok": false, "error": "unknown class"}
		}
		st := c.g.state
		st.ClassEmergency = copyClasses(st.ClassEmergency)
		st.ClassEmergency[req.Class] = true
		st.Generation = c.gen + 1
		st.Since = time.Now().Format(time.RFC3339)
		st.LoadError = ""
		if err := SaveState(c.g.config().StateFile, st); err != nil {
			return map[string]any{"ok": false, "error": err.Error()}
		}
		c.gen = st.Generation
		c.g.state = st
		b.EmergencyRelease()
		return map[string]any{"ok": true, "released": req.Class, "generation": c.gen}
	case "clear-emergency":
		if req.ExpectedGeneration == nil {
			return map[string]any{"ok": false, "error": "explicit expected_generation required to resume", "generation": c.gen}
		}
		cfg := c.g.config()
		if req.Class != "" && c.g.bucketFor(req.Class) == nil {
			return map[string]any{"ok": false, "error": "unknown class"}
		}
		st := c.g.state
		st.ClassEmergency = copyClasses(st.ClassEmergency)
		if req.Class == "" {
			st.Emergency = false
			st.ClassEmergency = nil
		} else {
			if st.Emergency {
				for _, name := range []string{"video", "foreign_download", "elephant"} {
					st.ClassEmergency[name] = true
				}
				st.Emergency = false
			}
			delete(st.ClassEmergency, req.Class)
		}
		st.Generation = c.gen + 1
		st.Since = time.Now().Format(time.RFC3339)
		st.LoadError = ""
		if err := SaveState(cfg.StateFile, st); err != nil {
			return map[string]any{"ok": false, "error": err.Error()}
		}
		c.gen = st.Generation
		c.g.state = st
		for _, item := range []struct {
			name string
			cfg  ClassCfg
		}{{"video", cfg.Classes.Video}, {"foreign_download", cfg.Classes.DL}, {"elephant", cfg.Classes.Elephant}} {
			b := c.g.bucketFor(item.name)
			if st.Emergency || st.ClassEmergency[item.name] {
				b.EmergencyRelease()
			} else {
				b.Resume(item.cfg.DownBPS, item.cfg.Burst)
			}
		}
		return map[string]any{"ok": true, "resumed": true, "generation": c.gen,
			"rates": map[string]int64{"video": cfg.Classes.Video.DownBPS, "foreign_download": cfg.Classes.DL.DownBPS}}
	case "set-rate":
		b := c.g.bucketFor(req.Class)
		if b == nil {
			return map[string]any{"ok": false, "error": "unknown class"}
		}
		if req.DownBPS < 0 || req.DownBPS > 1_000_000_000 || (req.Class == "elephant" && req.DownBPS != 0) {
			return map[string]any{"ok": false, "error": "invalid or unsupported rate"}
		}
		b.SetRate(req.DownBPS, 0)
		cfg := c.g.config()
		switch req.Class {
		case "video":
			cfg.Classes.Video.DownBPS = req.DownBPS
		case "foreign_download":
			cfg.Classes.DL.DownBPS = req.DownBPS
		case "elephant":
			cfg.Classes.Elephant.DownBPS = req.DownBPS
		}
		c.g.cfgMu.Lock()
		c.g.cfg = cfg
		c.g.cfgMu.Unlock()
		return map[string]any{"ok": true, "class": req.Class, "down_bps": req.DownBPS}
	default:
		return map[string]any{"ok": false, "error": fmt.Sprintf("unknown cmd %q", req.Cmd)}
	}
}

func copyClasses(src map[string]bool) map[string]bool {
	dst := make(map[string]bool, len(src))
	for k, v := range src {
		dst[k] = v
	}
	return dst
}
