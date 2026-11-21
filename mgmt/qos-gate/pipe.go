package main

import (
	"context"
	"io"
	"net"
	"time"
)

const (
	downChunk = 4096
	upChunk   = 16384
	upDialTO  = 8 * time.Second
	stallMax  = 5 * time.Minute
)

func closeWrite(c net.Conn) {
	if half, ok := c.(interface{ CloseWrite() error }); ok {
		_ = half.CloseWrite()
	}
}

// A normal EOF closes only that write direction. The response may arrive after
// the request EOF; only a real error cancels both directions.
func pipeConn(client, up net.Conn, bucket *Bucket, stats *ClassStats) {
	defer client.Close()
	defer up.Close()
	if tc, ok := up.(*net.TCPConn); ok {
		_ = tc.SetReadBuffer(16 * 1024)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 2)
	copyDirection := func(dst, src net.Conn, down bool) {
		size := upChunk
		if down {
			size = downChunk
		}
		buf := make([]byte, size)
		for {
			granted := len(buf)
			if down {
				var err error
				granted, err = bucket.TakeContext(ctx, granted)
				if err != nil {
					done <- err
					return
				}
			}
			_ = src.SetReadDeadline(time.Now().Add(stallMax))
			n, err := src.Read(buf[:granted])
			if down {
				bucket.Refund(granted - n)
			}
			if n > 0 {
				_ = dst.SetWriteDeadline(time.Now().Add(stallMax))
				written, werr := writeCount(dst, buf[:n])
				if down {
					stats.addDown(uint64(written))
				} else {
					stats.addUp(uint64(written))
				}
				if werr != nil {
					done <- werr
					return
				}
			}
			if err != nil {
				if err == io.EOF {
					closeWrite(dst)
					done <- nil
				} else {
					done <- err
				}
				return
			}
		}
	}
	go copyDirection(up, client, false)
	go copyDirection(client, up, true)
	if err := <-done; err != nil {
		cancel()
		client.SetDeadline(past())
		up.SetDeadline(past())
	}
	<-done
}

func writeCount(w io.Writer, p []byte) (int, error) {
	total := 0
	for len(p) > 0 {
		n, err := w.Write(p)
		total += n
		p = p[n:]
		if err != nil {
			return total, err
		}
		if n == 0 {
			return total, io.ErrShortWrite
		}
	}
	return total, nil
}
func writeFull(w io.Writer, p []byte) error { _, err := writeCount(w, p); return err }
func past() time.Time                       { return time.Now().Add(-time.Second) }
