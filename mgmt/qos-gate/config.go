// config.go: 配置加载/校验（JSON）、原子状态持久化（Q4.3：先持久化 emergency 再解除）。
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"sync"
)

type ClassCfg struct {
	DownBPS    int64 `json:"down_bps"`              // 0=直通
	Burst      int64 `json:"burst_bytes"`           // 0=rate/2
	ElephantMB int   `json:"elephant_mb,omitempty"` // 仅 elephant 类；0=哨兵禁用（Batch2 PoC）
}

type Config struct {
	ListenVideo    string `json:"listen_video"`    // 127.0.0.1:7895
	ListenDL       string `json:"listen_dl"`       // 127.0.0.1:7896
	ListenElephant string `json:"listen_elephant"` // 127.0.0.1:7897（哨兵）
	Upstream       string `json:"upstream"`        // mihomo listener 127.0.0.1:7894 (proxy: AUTO)
	CtlSock        string `json:"ctl_sock"`        // unix 控制口（0600）
	StateFile      string `json:"state_file"`      // emergency 持久化
	Classes        struct {
		Video    ClassCfg `json:"video"`
		DL       ClassCfg `json:"foreign_download"`
		Elephant ClassCfg `json:"elephant"`
	} `json:"classes"`
}

func LoadConfig(path string) (*Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var c Config
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&c); err != nil {
		return nil, fmt.Errorf("config %s: %w", path, err)
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return nil, errors.New("config: trailing JSON")
	}
	if c.ListenVideo == "" || c.ListenDL == "" || c.ListenElephant == "" || c.Upstream == "" {
		return nil, errors.New("config: listen_video/listen_dl/listen_elephant/upstream 必填")
	}
	if c.CtlSock == "" {
		c.CtlSock = "/tmp/qos-gate-ctl.sock"
	}
	if c.StateFile == "" {
		c.StateFile = filepath.Join(filepath.Dir(path), "qos-gate-state.json")
	}
	for _, addr := range []string{c.ListenVideo, c.ListenDL, c.ListenElephant, c.Upstream} {
		host, _, err := net.SplitHostPort(addr)
		if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
			return nil, errors.New("config: all endpoints must use literal loopback addresses")
		}
		if _, err := net.ResolveTCPAddr("tcp", addr); err != nil {
			return nil, errors.New("config: invalid port")
		}
	}
	for _, class := range []ClassCfg{c.Classes.Video, c.Classes.DL, c.Classes.Elephant} {
		if class.DownBPS < 0 || class.DownBPS > 1_000_000_000 || class.Burst < 0 || class.Burst > 1<<20 {
			return nil, errors.New("config: invalid rate/burst")
		}
	}
	if c.Classes.Elephant.ElephantMB != 0 || c.Classes.Elephant.DownBPS != 0 {
		return nil, errors.New("elephant threshold is NOT_IMPLEMENTED: keep elephant_mb=0 and down_bps=0")
	}
	return &c, nil
}

// ---- emergency 持久化（Q4.3/Q4.4：原子写，重启后旧限制不得复活） ----

type GateState struct {
	Emergency      bool            `json:"emergency"`
	Since          string          `json:"since"`
	Generation     int64           `json:"generation"`
	ClassEmergency map[string]bool `json:"class_emergency,omitempty"`
	LoadError      string          `json:"-"`
}

var stateMu sync.Mutex

func SaveState(path string, st GateState) error {
	stateMu.Lock()
	defer stateMu.Unlock()
	raw, err := json.Marshal(st)
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".qos-state-*")
	if err != nil {
		return err
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if err = f.Chmod(0o600); err == nil {
		_, err = f.Write(raw)
	}
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if err = os.Rename(tmp, path); err != nil {
		return err
	}
	if runtime.GOOS != "windows" {
		d, err := os.Open(filepath.Dir(path))
		if err != nil {
			return err
		}
		err = d.Sync()
		d.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

func LoadState(path string) GateState {
	var st GateState
	raw, err := os.ReadFile(path)
	if err == nil {
		err = json.Unmarshal(raw, &st)
		var fields map[string]json.RawMessage
		if err == nil {
			err = json.Unmarshal(raw, &fields)
			if _, ok := fields["emergency"]; !ok {
				err = errors.New("missing emergency state")
			}
		}
		if err == nil && st.Generation < 1 {
			err = errors.New("invalid generation")
		}
	}
	if err != nil {
		return GateState{Emergency: true, LoadError: "missing or invalid persistent state; explicit recovery required"}
	}
	return st
}
