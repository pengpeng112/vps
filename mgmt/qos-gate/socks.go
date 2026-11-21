// socks.go: 最小 SOCKS5 入站（CONNECT：域名/IPv4/IPv6 原样转发上游，不做本地解析——
// 禁止把目标域名先解析成污染 IP，Q3.2.3）。UDP ASSOCIATE 明确拒绝（本 PoC 标 TCP_ONLY，
// golden 既有 UDP443 REJECT 不动，Q3.2.6/T09 由真机验证另行记录）。
package main

import (
	"encoding/binary"
	"errors"
	"io"
	"net"
)

var (
	errSocksVersion = errors.New("socks: bad version")
	errSocksCmd     = errors.New("socks: command not supported (CONNECT only; UDP ASSOCIATE rejected)")
	errSocksAtyp    = errors.New("socks: bad address type")
	errSocksShort   = errors.New("socks: truncated request")
)

// socksTarget 解析后的请求目标。
type socksTarget struct {
	host string // 域名或 IP 字面量（不做解析）
	port int
}

func socksHandshake(c net.Conn) (*socksTarget, error) {
	// ---- 方法协商：只支持无鉴权 ----
	hdr := make([]byte, 2)
	if _, err := io.ReadFull(c, hdr); err != nil {
		return nil, errSocksShort
	}
	if hdr[0] != 5 {
		return nil, errSocksVersion
	}
	nmethods := int(hdr[1])
	methods := make([]byte, nmethods)
	if _, err := io.ReadFull(c, methods); err != nil {
		return nil, errSocksShort
	}
	found := false
	for _, method := range methods {
		if method == 0 {
			found = true
		}
	}
	if !found {
		c.Write([]byte{5, 255})
		return nil, errSocksVersion
	}
	if _, err := c.Write([]byte{5, 0}); err != nil { // 选择 NO_AUTH
		return nil, err
	}

	// ---- 请求 ----
	req := make([]byte, 4)
	if _, err := io.ReadFull(c, req); err != nil {
		return nil, errSocksShort
	}
	if req[0] != 5 {
		return nil, errSocksVersion
	}
	if req[1] != 1 { // 1=CONNECT；2=BIND；3=UDP ASSOCIATE 一律拒绝
		return nil, errSocksCmd
	}
	t := &socksTarget{}
	switch req[3] {
	case 1: // IPv4
		b := make([]byte, 4)
		if _, err := io.ReadFull(c, b); err != nil {
			return nil, errSocksShort
		}
		t.host = net.IP(b).String()
	case 3: // 域名（原样转发，上游代解析，无本地污染）
		lb := make([]byte, 1)
		if _, err := io.ReadFull(c, lb); err != nil {
			return nil, errSocksShort
		}
		l := int(lb[0])
		if l == 0 {
			return nil, errSocksAtyp
		}
		b := make([]byte, l)
		if _, err := io.ReadFull(c, b); err != nil {
			return nil, errSocksShort
		}
		t.host = string(b)
	case 4: // IPv6
		b := make([]byte, 16)
		if _, err := io.ReadFull(c, b); err != nil {
			return nil, errSocksShort
		}
		t.host = net.IP(b).String()
	default:
		return nil, errSocksAtyp
	}
	pb := make([]byte, 2)
	if _, err := io.ReadFull(c, pb); err != nil {
		return nil, errSocksShort
	}
	t.port = int(binary.BigEndian.Uint16(pb))
	return t, nil
}

// socksReply 按应答码回包（0 成功，其余按 RFC 1928）。
func socksReply(c net.Conn, code byte) {
	// BND.ADDR 全零占位（回环场景客户端不用）
	_, _ = c.Write([]byte{5, code, 0, 1, 0, 0, 0, 0, 0, 0})
}
