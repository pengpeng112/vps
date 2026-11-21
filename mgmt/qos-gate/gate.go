// gate.go: 三类入站 + 大象哨兵逻辑。
// :7895 视频 / :7896 国外下载 / :7897 大象流哨兵（elephant_mb=0 禁用=直通；
// Batch3 置 100：仅当日已处限速态且单连接下行超阈值后转入 elephant 桶，Q0.1）。
package main

import (
	"errors"
	"log"
	"net"
	"sync"
	"sync/atomic"
	"time"
)

type ClassStats struct {
	Down  atomic.Uint64
	Up    atomic.Uint64
	Conns atomic.Int64
}

func (s *ClassStats) addDown(n uint64) { s.Down.Add(n) }
func (s *ClassStats) addUp(n uint64)   { s.Up.Add(n) }

type Gate struct {
	cfg         *Config
	cfgMu       sync.RWMutex
	ctlMu       sync.Mutex
	ctl         *GateCtl
	state       GateState
	connections map[net.Conn]struct{}
	connMu      sync.Mutex
	closing     bool
	video       *Bucket
	dl          *Bucket
	elephant    *Bucket
	stats       struct {
		video    ClassStats
		dl       ClassStats
		elephant ClassStats
	}
	lnVideo, lnDL, lnElephant net.Listener
	quit                      chan struct{}
	wg                        sync.WaitGroup
}

func NewGate(cfg *Config) *Gate {
	g := &Gate{cfg: cfg, quit: make(chan struct{}), connections: make(map[net.Conn]struct{})}
	g.video = NewBucket("video", cfg.Classes.Video.DownBPS, cfg.Classes.Video.Burst)
	g.dl = NewBucket("foreign_download", cfg.Classes.DL.DownBPS, cfg.Classes.DL.Burst)
	g.elephant = NewBucket("elephant", cfg.Classes.Elephant.DownBPS, cfg.Classes.Elephant.Burst)
	st := LoadState(cfg.StateFile)
	g.state = st
	if st.Emergency {
		// Q4.4：重启不复活旧限制——持久化 emergency 优先于配置速率
		log.Printf("state: emergency persisted since %s gen=%d, buckets stay unlimited", st.Since, st.Generation)
		g.video.EmergencyRelease()
		g.dl.EmergencyRelease()
		g.elephant.EmergencyRelease()
	}
	for name, em := range st.ClassEmergency {
		if em {
			if b := g.bucketFor(name); b != nil {
				b.EmergencyRelease()
			}
		}
	}
	return g
}

func (g *Gate) config() *Config {
	g.cfgMu.RLock()
	defer g.cfgMu.RUnlock()
	copy := *g.cfg
	return &copy
}

func (g *Gate) Close() {
	g.connMu.Lock()
	if g.closing {
		g.connMu.Unlock()
		return
	}
	g.closing = true
	close(g.quit)
	for c := range g.connections {
		_ = c.Close()
	}
	g.connMu.Unlock()
	for _, ln := range []net.Listener{g.lnVideo, g.lnDL, g.lnElephant} {
		if ln != nil {
			_ = ln.Close()
		}
	}
	g.video.Stop()
	g.dl.Stop()
	g.elephant.Stop()
	g.wg.Wait()
}

func (g *Gate) bucketFor(class string) *Bucket {
	switch class {
	case "video":
		return g.video
	case "foreign_download":
		return g.dl
	case "elephant":
		return g.elephant
	}
	return nil
}

func (g *Gate) statsFor(class string) *ClassStats {
	switch class {
	case "video":
		return &g.stats.video
	case "foreign_download":
		return &g.stats.dl
	case "elephant":
		return &g.stats.elephant
	}
	return nil
}

// serveListener SOCKS5 入站循环。ln 允许测试注入。
func (g *Gate) serveListener(class string, ln net.Listener) {
	st := g.statsFor(class)
	bucket := g.bucketFor(class)
	g.wg.Add(1)
	go func() {
		defer g.wg.Done()
		for {
			c, err := ln.Accept()
			if err != nil {
				select {
				case <-g.quit:
					return
				default:
				}
				log.Printf("%s accept: %v", class, err)
				return
			}
			g.connMu.Lock()
			if g.closing || len(g.connections) >= 256 {
				g.connMu.Unlock()
				c.Close()
				continue
			}
			g.connections[c] = struct{}{}
			g.wg.Add(1)
			g.connMu.Unlock()
			st.Conns.Add(1)
			go func() {
				defer g.wg.Done()
				defer func() { g.connMu.Lock(); delete(g.connections, c); g.connMu.Unlock() }()
				g.handle(class, c, bucket, st)
			}()
		}
	}()
}

func (g *Gate) handle(class string, c net.Conn, bucket *Bucket, st *ClassStats) {
	defer st.Conns.Add(-1)
	_ = c.SetDeadline(time.Now().Add(upDialTO))
	t, err := socksHandshake(c)
	if err != nil {
		code := byte(1)
		if errors.Is(err, errSocksCmd) {
			code = 7
		}
		if errors.Is(err, errSocksAtyp) {
			code = 8
		}
		socksReply(c, code)
		c.Close()
		return
	}
	upstream := g.config().Upstream
	up, err := net.DialTimeout("tcp", upstream, upDialTO)
	if err != nil {
		log.Printf("%s upstream dial %s: %v", class, upstream, err)
		socksReply(c, 0x05) // connection refused
		c.Close()
		return
	}
	_ = up.SetDeadline(time.Now().Add(upDialTO))
	if err := socksDialUpstream(up, t); err != nil {
		log.Printf("%s upstream request failed: %v", class, err)
		socksReply(c, 0x01)
		up.Close()
		c.Close()
		return
	}
	socksReply(c, 0x00)
	c.SetDeadline(time.Time{})
	up.SetDeadline(time.Time{})
	pipeConn(c, up, bucket, st)
}

// socksDialUpstream 向 mihomo :7894（proxy: AUTO）发起 SOCKS5 请求，校验应答码。
func socksDialUpstream(up net.Conn, t *socksTarget) error {
	if _, err := up.Write([]byte{5, 1, 0}); err != nil { // VER=5, 1 method: NO_AUTH
		return err
	}
	rb := make([]byte, 2)
	if _, err := readFull(up, rb); err != nil {
		return err
	}
	if rb[0] != 5 || rb[1] != 0 {
		return errSocksVersion
	}
	host := t.host
	req := []byte{5, 1, 0}
	if ip := parseIP(host); ip != nil {
		if ip4 := ip.To4(); ip4 != nil {
			req = append(req, 1)
			req = append(req, ip4...)
		} else {
			req = append(req, 4)
			req = append(req, ip...)
		}
	} else {
		req = append(req, 3, byte(len(host)))
		req = append(req, host...)
	}
	var pb [2]byte
	pb[0] = byte(t.port >> 8)
	pb[1] = byte(t.port)
	req = append(req, pb[:]...)
	if _, err := up.Write(req); err != nil {
		return err
	}
	ans := make([]byte, 4)
	if _, err := readFull(up, ans); err != nil {
		return err
	}
	if ans[0] != 5 || ans[2] != 0 {
		return errSocksVersion
	}
	if ans[1] != 0 {
		return &UpstreamError{Code: ans[1]}
	}
	// 跳过 BND.ADDR/PORT
	var skip int
	switch ans[3] {
	case 1:
		skip = 4
	case 3:
		b := make([]byte, 1)
		if _, err := readFull(up, b); err != nil {
			return err
		}
		skip = int(b[0])
	case 4:
		skip = 16
	default:
		return errSocksAtyp
	}
	if skip > 0 {
		b := make([]byte, skip)
		if _, err := readFull(up, b); err != nil {
			return err
		}
	}
	if _, err := readFull(up, make([]byte, 2)); err != nil {
		return err
	}
	return nil
}

type UpstreamError struct{ Code byte }

func (e *UpstreamError) Error() string {
	reason := map[byte]string{
		1: "general failure", 2: "not allowed", 3: "network unreachable",
		4: "host unreachable", 5: "refused", 6: "TTL expired", 7: "command not supported",
		8: "address type not supported",
	}[e.Code]
	if reason == "" {
		reason = "unknown"
	}
	return "upstream socks reply " + reason
}

func parseIP(s string) net.IP {
	if ip := net.ParseIP(s); ip != nil {
		return ip
	}
	return nil
}

func readFull(c net.Conn, buf []byte) (int, error) {
	total := 0
	for total < len(buf) {
		n, err := c.Read(buf[total:])
		if err != nil {
			return total, err
		}
		total += n
	}
	return total, nil
}
