// main.go: qos-gate 入口。
//
//	qos-gate -config /tmp/qos-poc/qos-gate.json
//
// 信号：SIGHUP 热重载速率；SIGTERM/SIGINT 优雅退出（关 listener，保留 state）。
package main

import (
	"flag"
	"log"
	"net"
	"os"
	"os/signal"
	"syscall"
)

var cfgPath string

func main() {
	flag.StringVar(&cfgPath, "config", "/tmp/qos-poc/qos-gate.json", "配置文件路径（JSON）")
	flag.Parse()
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)

	cfg, err := LoadConfig(cfgPath)
	if err != nil {
		log.Fatalf("load config: %v", err)
	}
	g := NewGate(cfg)

	lnV, err := net.Listen("tcp", cfg.ListenVideo)
	if err != nil {
		log.Fatalf("listen video %s: %v", cfg.ListenVideo, err)
	}
	lnD, err := net.Listen("tcp", cfg.ListenDL)
	if err != nil {
		log.Fatalf("listen dl %s: %v", cfg.ListenDL, err)
	}
	lnE, err := net.Listen("tcp", cfg.ListenElephant)
	if err != nil {
		log.Fatalf("listen elephant %s: %v", cfg.ListenElephant, err)
	}
	g.lnVideo, g.lnDL, g.lnElephant = lnV, lnD, lnE
	g.serveListener("video", lnV)
	g.serveListener("foreign_download", lnD)
	g.serveListener("elephant", lnE)

	ctl := NewGateCtl(g)
	if err := ctl.Start(); err != nil {
		log.Fatalf("ctl %s: %v", cfg.CtlSock, err)
	}
	log.Printf("qos-gate up: video=%s dl=%s elephant=%s upstream=%s ctl=%s state=%s",
		cfg.ListenVideo, cfg.ListenDL, cfg.ListenElephant, cfg.Upstream, cfg.CtlSock, cfg.StateFile)

	sig := make(chan os.Signal, 4)
	signal.Notify(sig, syscall.SIGHUP, syscall.SIGTERM, syscall.SIGINT)
	for s := range sig {
		switch s {
		case syscall.SIGHUP:
			log.Printf("SIGHUP reload: %v", ctl.dispatch(ctlReq{Cmd: "reload"}))
		default:
			log.Printf("signal %v, shutting down", s)
			ctl.ln.Close()
			os.Remove(cfg.CtlSock)
			g.Close()
			return
		}
	}
}
